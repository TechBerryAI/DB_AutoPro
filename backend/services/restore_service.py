"""Restore service with validation support."""

from __future__ import annotations

import gzip
import os
import shlex
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.connectors.base import get_connector, resolve_connection
from backend.core.exceptions import BackupError, RestoreError
from backend.core.logging import get_audit_logger, get_logger
from backend.models.schemas import DatabaseType, OperationResult, OperationStatus, RestoreRequest
from backend.services.backup_service import BackupService
from backend.services.mongo_restore import MongorestorePlan, plan_mongorestore
from backend.services.mongo_tools import resolve_local_mongo_tool
from backend.services.pg_tools import is_pg_custom_dump, resolve_local_pg_tool

logger = get_logger("services.restore")
audit = get_audit_logger()

_operations: dict[str, OperationResult] = {}


def _cli_not_found_error(tool: str, detail: str | None = None) -> RestoreError:
    hint = detail or (
        f"'{tool}' was not found on this PC. "
        "Install MongoDB Database Tools, add the bin folder to PATH, "
        "or check 'Run mongorestore on Linux server (remote)'."
    )
    return RestoreError(hint)


def _is_remote_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("/") or normalized.startswith("mnt/")


def _apply_mongorestore_plan(cmd: list[str], plan: MongorestorePlan) -> None:
    cmd.extend(plan.extra_flags)
    if plan.database:
        cmd.extend(["--db", plan.database])
    if plan.collection:
        cmd.extend(["--collection", plan.collection])
    if plan.source_path and not plan.uses_archive_flags:
        cmd.append(plan.source_path)


class RestoreService:
    """Orchestrates database restore operations."""

    def __init__(self) -> None:
        self._backup_service = BackupService()

    def restore(
        self,
        request: RestoreRequest,
        user: str = "system",
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> OperationResult:
        """Restore database from backup file."""
        operation_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        result = OperationResult(
            operation_id=operation_id,
            status=OperationStatus.RUNNING,
            message="Restore started",
            started_at=started_at,
        )
        _operations[operation_id] = result

        try:
            backup_path = Path(request.backup_path)
            if not backup_path.exists() and not _is_remote_path(request.backup_path):
                raise RestoreError(f"Backup not found: {request.backup_path}")

            if request.validate_backup and not _is_remote_path(request.backup_path):
                if progress_callback:
                    progress_callback(0.1, "Validating backup...")
                self._validate_backup_path(backup_path)

            if progress_callback:
                progress_callback(0.3, "Starting restore...")

            restore_path = self._decompress_if_needed(backup_path)

            handlers = {
                DatabaseType.POSTGRESQL: self._restore_postgresql,
                DatabaseType.MYSQL: self._restore_mysql,
                DatabaseType.MONGODB: self._restore_mongodb,
                DatabaseType.MSSQL: self._restore_mssql,
            }
            handler = handlers.get(request.db_type)
            if not handler:
                raise RestoreError(f"Restore not supported for: {request.db_type}")

            handler(request, restore_path, progress_callback)

            if progress_callback:
                progress_callback(1.0, "Restore completed")

            result.status = OperationStatus.COMPLETED
            result.message = "Restore completed successfully"
            result.completed_at = datetime.now(timezone.utc)
            audit.log(
                "restore",
                user,
                request.backup_path,
                "success",
                {"db_type": request.db_type.value},
            )
            logger.info("Restore completed from %s", request.backup_path)

        except Exception as exc:
            result.status = OperationStatus.FAILED
            result.message = str(exc)
            result.completed_at = datetime.now(timezone.utc)
            audit.log("restore", user, request.backup_path, "failed", {
                "error": str(exc),
                "db_type": request.db_type.value,
            })
            raise RestoreError(str(exc)) from exc
        finally:
            _operations[operation_id] = result

        return result

    def _validate_backup_path(self, path: Path) -> None:
        if not path.exists():
            raise RestoreError(f"Backup not found: {path}")
        if path.is_file():
            return
        if path.is_dir():
            has_data = (
                any(path.rglob("*.bson"))
                or any(path.rglob("*.metadata.json"))
                or any(path.glob("*.archive"))
                or any(path.glob("*.archive.gz"))
            )
            if not has_data:
                raise RestoreError(
                    f"No backup data found under {path}. "
                    "Select a mongodump folder, .bson file, or .archive file."
                )
            return
        try:
            self._backup_service.validate_backup(str(path))
        except BackupError as exc:
            raise RestoreError(str(exc)) from exc

    def _decompress_if_needed(self, path: Path) -> Path:
        if path.suffix == ".gz":
            decompressed = path.with_suffix("")
            with gzip.open(path, "rb") as f_in, decompressed.open("wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            return decompressed
        return path

    @staticmethod
    def _mongo_target_database(request: RestoreRequest) -> str | None:
        if request.database and str(request.database).strip():
            return str(request.database).strip()
        return None

    def _mongorestore_plan(self, request: RestoreRequest, backup_path: Path) -> MongorestorePlan:
        return plan_mongorestore(request, backup_path)

    def _restore_postgresql(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        env = {**os.environ, "PGPASSWORD": conn.password}
        db = request.database or conn.database

        if backup_path.is_dir():
            raise RestoreError("Use pg_restore for directory format backups via CLI")

        if is_pg_custom_dump(backup_path):
            pg_restore = resolve_local_pg_tool("pg_restore")
            cmd = [
                pg_restore,
                "-h", conn.host,
                "-p", str(conn.port),
                "-U", conn.username,
                "-d", db,
                "--clean",
                "--if-exists",
                "--no-owner",
                str(backup_path),
            ]
            result = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise RestoreError(f"PostgreSQL restore failed: {detail}")
            return

        psql = resolve_local_pg_tool("psql")
        cmd = [
            psql,
            "-h", conn.host,
            "-p", str(conn.port),
            "-U", conn.username,
            "-d", db,
            "-f", str(backup_path),
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise RestoreError(f"PostgreSQL restore failed: {result.stderr}")

    def _restore_mysql(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        db = request.database or conn.database
        cmd = [
            "mysql",
            "-h", conn.host,
            "-P", str(conn.port),
            "-u", conn.username,
            f"-p{conn.password}",
            db,
        ]
        with backup_path.open(encoding="utf-8", errors="replace") as f:
            result = subprocess.run(cmd, stdin=f, capture_output=True, text=True)
        if result.returncode != 0:
            raise RestoreError(f"MySQL restore failed: {result.stderr}")

    def _restore_mongodb(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        from backend.services.ssh_client import ssh_options_from_connection

        ssh = ssh_options_from_connection(conn)
        if not request.use_remote_exec:
            self._restore_mongodb_local(request, conn, backup_path, progress_callback)
            return
        if not ssh:
            raise RestoreError(
                "Remote restore requires SSH on this connection. "
                "Enable SSH in connection settings or uncheck 'Run mongorestore on Linux server'."
            )

        remote_source = request.remote_source_dir if (
            request.remote_source_dir and _is_remote_path(request.remote_source_dir)
        ) else (
            request.backup_path if _is_remote_path(request.backup_path) else None
        )
        self._restore_mongodb_via_ssh(
            request, conn, backup_path, ssh, remote_source, progress_callback
        )

    def _restore_mongodb_local(
        self,
        request: RestoreRequest,
        conn,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        plan = self._mongorestore_plan(request, backup_path)
        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        preferred = request.remote_binary_path or opts.get("mongorestore_path")
        if not preferred and opts.get("mongodump_path"):
            preferred = str(opts["mongodump_path"]).replace("mongodump", "mongorestore")
        try:
            mongorestore_bin = resolve_local_mongo_tool("mongorestore", preferred)
        except FileNotFoundError as exc:
            raise _cli_not_found_error("mongorestore", str(exc)) from exc
        cmd = [
            mongorestore_bin,
            "--host", conn.host,
            "--port", str(conn.port),
            "--username", conn.username,
            "--password", conn.password,
            "--authenticationDatabase", str(auth_db),
        ]
        _apply_mongorestore_plan(cmd, plan)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise _cli_not_found_error("mongorestore") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RestoreError(f"mongorestore failed: {detail}")

    def _restore_mongodb_via_ssh(
        self,
        request: RestoreRequest,
        conn,
        backup_path: Path,
        ssh: dict,
        remote_source: str | None,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        from backend.services.ssh_client import (
            build_mongorestore_remote_command,
            mongo_endpoint_from_connection,
            remove_remote_path,
            resolve_remote_mongorestore_path,
            run_ssh_command,
            upload_remote_directory,
        )

        plan = self._mongorestore_plan(request, backup_path)

        preferred = request.remote_binary_path or ssh.get("mongorestore_path")
        if not preferred and ssh.get("mongodump_path"):
            preferred = str(ssh["mongodump_path"]).replace("mongodump", "mongorestore")

        if progress_callback:
            progress_callback(0.2, "Locating mongorestore on Linux server...")
        try:
            mongorestore_path = resolve_remote_mongorestore_path(ssh, preferred)
        except RuntimeError as exc:
            raise RestoreError(str(exc)) from exc

        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        mongo_host, mongo_port = mongo_endpoint_from_connection(conn)

        staging_dir: str | None = None
        remote_extra_args = list(plan.extra_flags)
        restore_input: str

        if remote_source and _is_remote_path(remote_source):
            restore_input = remote_source.rstrip("/")
            if plan.uses_archive_flags:
                remote_extra_args = [
                    arg.replace(str(backup_path), restore_input) for arg in remote_extra_args
                ]
            if progress_callback:
                progress_callback(0.4, f"Using backup on Linux server: {restore_input}")
        else:
            staging_dir = f"/tmp/mongorestore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if backup_path.is_file():
                from backend.services.ssh_client import upload_remote_file

                remote_file = f"{staging_dir}/{backup_path.name}"
                if progress_callback:
                    progress_callback(0.35, f"Uploading backup file to Linux server ({remote_file})...")
                try:
                    upload_remote_file(ssh, backup_path, remote_file)
                except Exception as exc:
                    raise RestoreError(f"Could not upload backup to Linux server: {exc}") from exc
                if plan.uses_archive_flags:
                    remote_extra_args = [
                        arg.replace(str(backup_path), remote_file) for arg in remote_extra_args
                    ]
                restore_input = remote_file
            else:
                if progress_callback:
                    progress_callback(0.35, f"Uploading backup to Linux server ({staging_dir})...")
                try:
                    upload_remote_directory(ssh, backup_path, staging_dir)
                except Exception as exc:
                    raise RestoreError(f"Could not upload backup to Linux server: {exc}") from exc
                restore_input = staging_dir

        if progress_callback:
            progress_callback(0.6, f"Running mongorestore ({mongorestore_path})...")

        remote_cmd = build_mongorestore_remote_command(
            mongorestore_path=mongorestore_path,
            mongo_host=mongo_host,
            mongo_port=mongo_port,
            username=conn.username,
            password=conn.password,
            auth_database=str(auth_db),
            input_dir=restore_input,
            database=plan.database,
            collection=plan.collection,
            drop_existing=False,
            extra_args=remote_extra_args or None,
        )
        exit_code, stdout, stderr = run_ssh_command(
            ssh_host=ssh["ssh_host"],
            fallback_host=ssh["fallback_host"],
            ssh_port=ssh["ssh_port"],
            username=ssh["username"],
            password=ssh["password"],
            command=remote_cmd,
            timeout=1800,
        )

        if staging_dir:
            try:
                remove_remote_path(ssh, staging_dir)
            except RuntimeError:
                logger.warning("Could not remove remote staging folder: %s", staging_dir)

        if exit_code != 0:
            detail = stderr.strip() or stdout.strip() or "unknown error"
            raise RestoreError(f"Remote mongorestore failed: {detail}")

    def _restore_mssql(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        db = request.database or conn.database
        connector = get_connector(DatabaseType.MSSQL, request.profile)
        connector.execute_script(
            f"RESTORE DATABASE [{db}] FROM DISK = N'{backup_path}' WITH REPLACE"
        )

    def get_operation(self, operation_id: str) -> OperationResult | None:
        return _operations.get(operation_id)
