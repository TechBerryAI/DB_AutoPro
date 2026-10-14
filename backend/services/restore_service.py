"""Restore service with validation support."""

from __future__ import annotations

import gzip
import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from backend.connectors.base import get_connector, resolve_connection
from backend.core.exceptions import BackupError, RestoreError
from backend.core.logging import get_audit_logger, get_logger
from backend.models.schemas import (
    DatabaseType,
    OperationResult,
    OperationStatus,
    RestoreRequest,
    RestoreSourceType,
    RestoreTargetType,
)
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
        "Install MongoDB Database Tools on the Linux server or set the mongorestore binary path."
    )
    return RestoreError(hint)


def _is_remote_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("/") or normalized.startswith("mnt/")


def _uses_server_restore(request: RestoreRequest) -> bool:
    if request.restore_source == RestoreSourceType.OTHER_SERVER:
        return request.restore_target == RestoreTargetType.LINUX_SERVER
    return request.restore_source == RestoreSourceType.CONNECTED_SERVER


def _remote_pg_uses_pg_restore(remote_path: str) -> bool:
    lower = remote_path.lower()
    if lower.endswith(".sql"):
        return False
    if lower.endswith((".dump", ".backup", ".tar")):
        return True
    return True


def _resolve_restore_ssh(request: RestoreRequest) -> dict:
    if request.restore_source == RestoreSourceType.OTHER_SERVER:
        host = (request.external_ssh_host or "").strip()
        user = (request.external_ssh_username or "").strip()
        if not host or not user:
            raise RestoreError(
                "Remote Linux Server restore requires SSH host and username."
            )
        from backend.services.ssh_client import ssh_options_from_external

        return ssh_options_from_external(
            host=host,
            port=request.external_ssh_port or 22,
            username=user,
            password=request.external_ssh_password or "",
        )

    conn = resolve_connection(request.profile)
    from backend.services.ssh_client import ssh_options_from_connection

    ssh = ssh_options_from_connection(conn)
    if not ssh:
        raise RestoreError(
            "Linux Server restore requires SSH on the connection. "
            "Enable SSH in connection settings."
        )
    return ssh


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
            request = self._prepare_restore_request(request, progress_callback)
            backup_path = Path(request.backup_path)
            if not backup_path.exists() and not _is_remote_path(request.backup_path):
                raise RestoreError(f"Backup not found: {request.backup_path}")

            if request.validate_backup and backup_path.exists():
                if progress_callback:
                    progress_callback(0.1, "Validating backup...")
                self._validate_backup_path(backup_path)

            if progress_callback:
                progress_callback(0.3, "Starting restore...")

            if backup_path.exists():
                restore_path = self._decompress_if_needed(backup_path)
            else:
                restore_path = backup_path

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

    def _prepare_restore_request(
        self,
        request: RestoreRequest,
        progress_callback: Callable[[float, str], None] | None,
    ) -> RestoreRequest:
        """Stage remote backups locally when restoring to this PC."""
        if request.restore_source != RestoreSourceType.OTHER_SERVER:
            return request

        if request.restore_target == RestoreTargetType.LINUX_SERVER:
            return request.model_copy(update={"use_remote_exec": True})

        ssh = _resolve_restore_ssh(request)
        remote_path = (request.remote_source_dir or request.backup_path or "").strip()
        if not remote_path:
            raise RestoreError("Select a backup file or folder on the Remote Linux Server.")

        local_path = self._download_remote_backup(ssh, remote_path, progress_callback)
        return request.model_copy(
            update={
                "backup_path": str(local_path),
                "remote_source_dir": None,
                "use_remote_exec": False,
            }
        )

    def _download_remote_backup(
        self,
        ssh: dict,
        remote_path: str,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        from backend.services.ssh_client import (
            _ssh_kwargs,
            download_remote_directory,
            download_remote_file,
            run_ssh_command,
        )

        quoted = shlex.quote(remote_path)
        code_dir, _, _ = run_ssh_command(
            command=f"test -d {quoted}",
            timeout=30,
            **_ssh_kwargs(ssh),
        )
        staging = Path(tempfile.mkdtemp(prefix="db_autopro_restore_"))
        name = PurePosixPath(remote_path.rstrip("/")).name or "backup"

        if code_dir == 0:
            local_path = staging / name
            if progress_callback:
                progress_callback(0.12, f"Downloading folder {remote_path}...")
            download_remote_directory(ssh, remote_path.rstrip("/"), local_path)
            return local_path

        code_file, _, _ = run_ssh_command(
            command=f"test -f {quoted}",
            timeout=30,
            **_ssh_kwargs(ssh),
        )
        if code_file != 0:
            raise RestoreError(f"Remote backup path not found: {remote_path}")

        local_path = staging / name
        if progress_callback:
            progress_callback(0.12, f"Downloading file {remote_path}...")

        def on_progress(transferred: int, total: int) -> None:
            if progress_callback and total > 0:
                ratio = min(transferred / total, 1.0)
                progress_callback(0.12 + ratio * 0.16, "Downloading backup from remote server...")

        download_remote_file(ssh, remote_path, local_path, on_progress=on_progress)
        return local_path

    def _validate_backup_path(self, path: Path) -> None:
        if not path.exists():
            raise RestoreError(f"Backup not found: {path}")
        if path.is_file():
            return
        if path.is_dir():
            has_data = (
                any(path.rglob("*.bson"))
                or any(path.rglob("*.bson.gz"))
                or any(path.rglob("*.metadata.json"))
                or any(path.rglob("*.metadata.json.gz"))
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
        name = path.name.lower()
        # mongorestore reads these itself when --gzip is set.
        if name.endswith((".bson.gz", ".metadata.json.gz", ".archive.gz")):
            return path
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
        db = request.database or conn.database

        if _uses_server_restore(request) or _is_remote_path(str(backup_path)):
            remote_source = (request.remote_source_dir or request.backup_path or "").strip()
            if not remote_source:
                raise RestoreError("Select a backup file on the Linux server.")
            ssh = _resolve_restore_ssh(request)
            self._restore_postgresql_via_ssh(
                request, conn, db, remote_source, ssh, progress_callback
            )
            return

        env = {**os.environ, "PGPASSWORD": conn.password}

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

    def _restore_postgresql_via_ssh(
        self,
        request: RestoreRequest,
        conn,
        database: str,
        remote_source: str,
        ssh: dict,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        from backend.services.ssh_client import (
            build_pg_restore_remote_command,
            build_psql_restore_remote_command,
            can_use_postgres_peer_auth,
            pg_endpoint_from_connection,
            resolve_remote_pg_tool_path,
            run_ssh_command,
            _ssh_kwargs,
        )

        pg_host, pg_port = pg_endpoint_from_connection(conn)
        peer_auth = can_use_postgres_peer_auth(ssh)
        ssh_user = str(ssh.get("username") or "")

        if progress_callback:
            progress_callback(0.4, "Running restore on Linux server...")

        try:
            if _remote_pg_uses_pg_restore(remote_source):
                tool_path = resolve_remote_pg_tool_path(ssh, "pg_restore")
                remote_cmd = build_pg_restore_remote_command(
                    pg_restore_path=tool_path,
                    pg_host=pg_host,
                    pg_port=pg_port,
                    username=conn.username,
                    password=conn.password,
                    database=database,
                    backup_path=remote_source,
                    peer_auth=peer_auth,
                    ssh_username=ssh_user,
                )
            else:
                tool_path = resolve_remote_pg_tool_path(ssh, "psql")
                remote_cmd = build_psql_restore_remote_command(
                    psql_path=tool_path,
                    pg_host=pg_host,
                    pg_port=pg_port,
                    username=conn.username,
                    password=conn.password,
                    database=database,
                    backup_path=remote_source,
                    peer_auth=peer_auth,
                    ssh_username=ssh_user,
                )
        except RuntimeError as exc:
            raise RestoreError(str(exc)) from exc

        exit_code, stdout, stderr = run_ssh_command(
            command=remote_cmd,
            timeout=3600,
            **_ssh_kwargs(ssh),
        )
        if exit_code != 0:
            detail = stderr.strip() or stdout.strip() or "unknown error"
            raise RestoreError(f"Remote PostgreSQL restore failed: {detail}")

    def _restore_mysql(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        db = request.database or conn.database

        if _uses_server_restore(request) or _is_remote_path(str(backup_path)):
            remote_source = (request.remote_source_dir or request.backup_path or "").strip()
            if not remote_source:
                raise RestoreError("Select a backup file on the Linux server.")
            ssh = _resolve_restore_ssh(request)
            self._restore_mysql_via_ssh(
                request, conn, db, remote_source, ssh, progress_callback
            )
            return

        cmd = [
            "mysql",
            "-h", conn.host,
            "-P", str(conn.port),
            "-u", conn.username,
            f"-p{conn.password}",
            db,
        ]
        try:
            with backup_path.open(encoding="utf-8", errors="replace") as f:
                result = subprocess.run(cmd, stdin=f, capture_output=True, text=True)
        except FileNotFoundError:
            from backend.services.ssh_client import ssh_options_from_connection

            ssh = ssh_options_from_connection(conn)
            if not ssh:
                raise RestoreError(
                    "'mysql' was not found on this PC. Install MySQL client tools and add them to PATH, "
                    "or enable SSH on the connection so the restore can run on the Linux server."
                )
            if progress_callback:
                progress_callback(
                    0.2,
                    "mysql is not on this PC — uploading the backup and restoring on the Linux server...",
                )
            remote_source = self._upload_local_backup_for_restore(ssh, backup_path, progress_callback)
            try:
                self._restore_mysql_via_ssh(
                    request, conn, db, remote_source, ssh, progress_callback
                )
            finally:
                from backend.services.ssh_client import remove_remote_path

                try:
                    remove_remote_path(ssh, remote_source)
                except Exception:
                    pass
            return
        if result.returncode != 0:
            raise RestoreError(f"MySQL restore failed: {result.stderr}")

    def _upload_local_backup_for_restore(
        self,
        ssh: dict,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> str:
        """Copy a local backup onto the Linux host so mysql/psql can read it."""
        import shlex

        from backend.services.ssh_client import _ssh_kwargs, run_ssh_command, upload_remote_file

        code, home_out, _err = run_ssh_command(
            command='printf %s "$HOME"',
            timeout=30,
            **_ssh_kwargs(ssh),
        )
        home = (home_out or "").strip() if code == 0 else ""
        if not home:
            user = str(ssh.get("username") or "user").strip() or "user"
            home = f"/home/{user}"
        staging = f"{home.rstrip('/')}/db_autopro_staging"
        mkdir = f"mkdir -p {shlex.quote(staging)} && test -w {shlex.quote(staging)}"
        code, _out, err = run_ssh_command(command=mkdir, timeout=30, **_ssh_kwargs(ssh))
        if code != 0:
            raise RestoreError(
                "Could not create a writable folder on the Linux server "
                f"({staging}): {(err or '').strip() or f'exit {code}'}"
            )
        remote_path = f"{staging}/{backup_path.name}"
        if progress_callback:
            progress_callback(0.3, f"Uploading {backup_path.name} to the Linux server...")
        upload_remote_file(ssh, backup_path, remote_path)
        return remote_path

    def _restore_mysql_via_ssh(
        self,
        request: RestoreRequest,
        conn,
        database: str,
        remote_source: str,
        ssh: dict,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        from backend.services.ssh_client import (
            build_mysql_restore_remote_command,
            pg_endpoint_from_connection,
            resolve_remote_mysql_path,
            run_ssh_command,
            _ssh_kwargs,
        )

        mysql_host, mysql_port = pg_endpoint_from_connection(conn)

        if progress_callback:
            progress_callback(0.4, "Running mysql restore on Linux server...")

        try:
            mysql_path = resolve_remote_mysql_path(ssh)
            remote_cmd = build_mysql_restore_remote_command(
                mysql_path=mysql_path,
                mysql_host=mysql_host,
                mysql_port=mysql_port,
                username=conn.username,
                password=conn.password,
                database=database,
                backup_path=remote_source,
            )
        except RuntimeError as exc:
            raise RestoreError(str(exc)) from exc

        exit_code, stdout, stderr = run_ssh_command(
            command=remote_cmd,
            timeout=3600,
            **_ssh_kwargs(ssh),
        )
        if exit_code != 0:
            detail = stderr.strip() or stdout.strip() or "unknown error"
            raise RestoreError(f"Remote MySQL restore failed: {detail}")

    def _restore_mongodb(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        remote_path = _is_remote_path(request.backup_path)
        server_restore = _uses_server_restore(request) or request.use_remote_exec or remote_path

        if not server_restore:
            self._restore_mongodb_local(request, conn, backup_path, progress_callback)
            return

        ssh = _resolve_restore_ssh(request)

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
