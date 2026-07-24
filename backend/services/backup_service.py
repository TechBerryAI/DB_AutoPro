"""Backup service with multi-database support, compression, and retention."""

from __future__ import annotations

import gzip
import shlex
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from backend.connectors.base import get_connector, resolve_connection
from backend.core.config import get_settings
from backend.core.exceptions import BackupError
from backend.core.logging import get_audit_logger, get_logger
from backend.services.backup_mode_handlers import (
    backup_mysql,
    backup_oracle,
    backup_postgresql,
)
from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    BackupMode,
    BackupRequest,
    DatabaseType,
    OperationResult,
    OperationStatus,
    backup_method_category,
    backup_modes_for,
)
from backend.services.backup_chain import load_chain, record_backup

logger = get_logger("services.backup")
audit = get_audit_logger()

# In-memory operation tracking (production would use Redis/DB)
_operations: dict[str, OperationResult] = {}


def _cli_not_found_error(tool: str) -> BackupError:
    install_hints = {
        "pg_dump": "Install PostgreSQL for Windows and add its bin folder to PATH.",
        "pg_dumpall": "Install PostgreSQL for Windows and add its bin folder to PATH.",
        "pg_basebackup": "Install PostgreSQL for Windows and add its bin folder to PATH.",
        "pg_receivewal": "Install PostgreSQL for Windows and add its bin folder to PATH.",
        "mongodump": (
            "Install MongoDB Database Tools for Windows from mongodb.com/try/download/database-tools "
            "and add the bin folder to PATH."
        ),
        "mysqldump": "Install MySQL client tools and add them to PATH.",
        "mysqlbinlog": "Install MySQL client tools and add them to PATH.",
    }
    hint = install_hints.get(tool, "Install the database client tools and add them to PATH.")
    return BackupError(f"'{tool}' was not found on this PC. {hint}")


class BackupService:
    """Orchestrates database backup operations."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.output_dir = Path(self.settings.backup.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self,
        request: BackupRequest,
        user: str = "system",
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> OperationResult:
        """Execute backup based on database type and method."""
        operation_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        result = OperationResult(
            operation_id=operation_id,
            status=OperationStatus.RUNNING,
            message="Backup started",
            started_at=started_at,
        )
        _operations[operation_id] = result

        try:
            if progress_callback:
                progress_callback(0.1, "Preparing backup...")

            self._validate_backup_mode(request)
            output_path = self._resolve_output_path(request)
            handlers = {
                DatabaseType.POSTGRESQL: self._backup_postgresql,
                DatabaseType.MYSQL: self._backup_mysql,
                DatabaseType.MONGODB: self._backup_mongodb,
                DatabaseType.MSSQL: self._backup_mssql,
                DatabaseType.ORACLE: self._backup_oracle,
            }
            handler = handlers.get(request.db_type)
            if not handler:
                raise BackupError(f"Unsupported database type: {request.db_type}")

            if progress_callback:
                progress_callback(0.3, f"Running {request.method.value}...")

            final_path = handler(request, output_path, progress_callback)

            physical = backup_method_category(request.method) == BackupCategory.PHYSICAL
            remote = bool(request.use_remote_exec)
            if (
                request.compress
                and self.settings.backup.compression
                and not physical
                and not remote
            ):
                if progress_callback:
                    progress_callback(0.8, "Compressing backup...")
                final_path = self._compress(final_path)

            if progress_callback:
                progress_callback(1.0, "Backup completed")

            result.status = OperationStatus.COMPLETED
            result.message = "Backup completed successfully"
            result.output_path = str(final_path)
            result.completed_at = datetime.now(timezone.utc)
            if remote:
                result.message = f"Remote backup completed on Linux server: {final_path}"
                size_bytes = 0
            else:
                size_bytes = (
                    sum(f.stat().st_size for f in final_path.rglob("*") if f.is_file())
                    if final_path.is_dir()
                    else final_path.stat().st_size
                )
            result.details = {
                "size_bytes": size_bytes,
                "method": request.method.value,
                "backup_mode": request.backup_mode.value,
                "category": backup_method_category(request.method).value,
                "db_type": request.db_type.value,
                "remote": remote,
            }

            audit.log("backup", user, str(final_path), "success", result.details)
            logger.info("Backup completed: %s", final_path)

        except Exception as exc:
            result.status = OperationStatus.FAILED
            result.message = str(exc)
            result.completed_at = datetime.now(timezone.utc)
            audit.log("backup", user, request.profile, "failed", {
                "error": str(exc),
                "db_type": request.db_type.value,
            })
            logger.error("Backup failed: %s", exc)
            raise BackupError(str(exc)) from exc
        finally:
            _operations[operation_id] = result

        return result

    @staticmethod
    def _validate_backup_mode(request: BackupRequest) -> None:
        category = backup_method_category(request.method)
        allowed = backup_modes_for(request.db_type, category)
        if request.backup_mode not in allowed:
            raise BackupError(
                f"Backup mode '{request.backup_mode.value}' is not supported for "
                f"{request.db_type.value} {category.value} backups."
            )

    @staticmethod
    def _mongo_target_database(request: BackupRequest) -> str | None:
        """Explicit database from backup form; None dumps all databases on the server."""
        if request.database and str(request.database).strip():
            return str(request.database).strip()
        return None

    def _resolve_output_path(self, request: BackupRequest) -> Path:
        if request.output_path:
            return Path(request.output_path)
        base_dir = Path(request.local_output_dir) if request.local_output_dir else self.output_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_name = self._mongo_target_database(request) or "all"
        category = backup_method_category(request.method)
        mode_tag = request.backup_mode.value

        if request.method == BackupMethod.PG_BASEBACKUP:
            return base_dir / f"{request.db_type.value}_physical_{mode_tag}_{timestamp}"
        if request.method == BackupMethod.MSSQL_BACKUP:
            ext = ".trn" if request.backup_mode == BackupMode.INCREMENTAL else ".bak"
            return base_dir / f"{request.db_type.value}_{db_name}_{mode_tag}_{timestamp}{ext}"
        if request.method == BackupMethod.MONGODUMP:
            from backend.services.ssh_client import remote_backup_folder_name

            folder = remote_backup_folder_name(request.backup_folder_name, db_name)
            return base_dir / folder

        if request.backup_mode != BackupMode.FULL:
            if request.db_type == DatabaseType.POSTGRESQL:
                return base_dir / f"{request.db_type.value}_{db_name}_{mode_tag}_{timestamp}_wal"
            suffix = "sql"
            return base_dir / f"{request.db_type.value}_{db_name}_{mode_tag}_{timestamp}.{suffix}"

        suffix = "sql" if category.value == "logical" else "backup"
        return base_dir / f"{request.db_type.value}_{db_name}_full_{timestamp}.{suffix}"

    def _backup_postgresql(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        return backup_postgresql(
            request,
            output_path,
            progress_callback,
            cli_not_found_error=_cli_not_found_error,
        )

    def _backup_mysql(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        return backup_mysql(request, output_path, progress_callback)

    def _backup_mongodb(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        db = self._mongo_target_database(request)
        if request.collection and not db:
            raise BackupError("Select a database when backing up a single collection.")
        opts = conn.extra_options or {}
        use_remote = request.use_remote_exec
        if use_remote:
            return self._backup_mongodb_remote(request, conn, progress_callback)
        return self._backup_mongodb_local(request, conn, output_path, progress_callback)

    def _backup_mongodb_local(
        self,
        request: BackupRequest,
        conn,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        from backend.services.ssh_client import ssh_options_from_connection

        ssh = ssh_options_from_connection(conn)
        if ssh:
            return self._backup_mongodb_local_via_ssh(
                request, conn, output_path, ssh, progress_callback
            )
        return self._backup_mongodb_local_subprocess(
            request, conn, output_path, progress_callback
        )

    def _backup_mongodb_local_subprocess(
        self,
        request: BackupRequest,
        conn,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        dump_dir = output_path.with_suffix("")
        dump_dir.mkdir(parents=True, exist_ok=True)
        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        db = self._mongo_target_database(request)
        from backend.services.mongo_tools import resolve_local_mongo_tool

        preferred = request.remote_binary_path or opts.get("mongodump_path")
        try:
            mongodump_bin = resolve_local_mongo_tool("mongodump", preferred)
        except FileNotFoundError as exc:
            shutil.rmtree(dump_dir, ignore_errors=True)
            raise _cli_not_found_error("mongodump") from exc
        cmd = [
            mongodump_bin,
            "--host", conn.host,
            "--port", str(conn.port),
            "--username", conn.username,
            "--password", conn.password,
            "--authenticationDatabase", str(auth_db),
        ]
        if db:
            cmd.extend(["--db", db])
        if request.collection:
            cmd.extend(["--collection", request.collection])
        cmd.extend(["--out", str(dump_dir)])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError as exc:
            shutil.rmtree(dump_dir, ignore_errors=True)
            raise _cli_not_found_error("mongodump") from exc
        if result.returncode != 0:
            shutil.rmtree(dump_dir, ignore_errors=True)
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise BackupError(f"mongodump failed: {detail}")
        if not any(dump_dir.rglob("*")):
            shutil.rmtree(dump_dir, ignore_errors=True)
            raise BackupError("mongodump finished but no backup files were created.")
        from backend.services.ssh_client import flatten_mongodump_output

        if db:
            flatten_mongodump_output(dump_dir, db)
        return dump_dir

    def _backup_mongodb_local_via_ssh(
        self,
        request: BackupRequest,
        conn,
        output_path: Path,
        ssh: dict,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        """Run mongodump on Linux, then download the dump to the local output folder."""
        from backend.services.ssh_client import (
            build_mongodump_remote_command,
            download_remote_directory,
            flatten_mongodump_output,
            mongo_endpoint_from_connection,
            remote_backup_folder_name,
            remove_remote_path,
            resolve_remote_mongodump_path,
            run_ssh_command,
        )

        dump_dir = output_path.with_suffix("")
        dump_dir.mkdir(parents=True, exist_ok=True)

        preferred_dump = request.remote_binary_path or ssh.get("mongodump_path") or None
        if progress_callback:
            progress_callback(0.2, "Locating mongodump on Linux server...")
        try:
            mongodump_path = resolve_remote_mongodump_path(ssh, preferred_dump)
        except RuntimeError as exc:
            shutil.rmtree(dump_dir, ignore_errors=True)
            raise BackupError(str(exc)) from exc

        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        db = self._mongo_target_database(request)
        mongo_host, mongo_port = mongo_endpoint_from_connection(conn)
        remote_stage = f"/tmp/{remote_backup_folder_name(request.backup_folder_name, db or 'all')}"

        if progress_callback:
            progress_callback(0.35, f"Running mongodump on Linux server ({mongodump_path})...")

        remote_cmd = build_mongodump_remote_command(
            mongodump_path=mongodump_path,
            mongo_host=mongo_host,
            mongo_port=mongo_port,
            username=conn.username,
            password=conn.password,
            auth_database=str(auth_db),
            database=db,
            output_dir=remote_stage,
            collection=request.collection,
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
        if exit_code != 0:
            shutil.rmtree(dump_dir, ignore_errors=True)
            detail = stderr.strip() or stdout.strip() or "unknown error"
            raise BackupError(f"Remote mongodump failed: {detail}")

        try:
            if progress_callback:
                progress_callback(0.7, f"Downloading backup to {dump_dir}...")
            download_remote_directory(ssh, remote_stage, dump_dir)
        except Exception as exc:
            shutil.rmtree(dump_dir, ignore_errors=True)
            raise BackupError(f"Could not download backup to this PC: {exc}") from exc
        finally:
            try:
                remove_remote_path(ssh, remote_stage)
            except RuntimeError:
                logger.warning("Could not remove remote staging folder: %s", remote_stage)

        if not any(dump_dir.rglob("*")):
            shutil.rmtree(dump_dir, ignore_errors=True)
            raise BackupError("Backup download finished but the local folder is empty.")

        if db:
            flatten_mongodump_output(dump_dir, db)

        if progress_callback:
            progress_callback(1.0, "Backup saved on this PC")
        return dump_dir

    def _backup_mongodb_remote(
        self,
        request: BackupRequest,
        conn,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        from backend.services.ssh_client import (
            build_mongodump_remote_command,
            flatten_remote_mongodump_output,
            mongo_endpoint_from_connection,
            remote_backup_folder_name,
            resolve_remote_mongodump_path,
            run_ssh_command,
            ssh_options_from_connection,
        )

        ssh = ssh_options_from_connection(conn)
        if not ssh:
            raise BackupError(
                "Remote MongoDB backup requires Linux SSH credentials on the connection. "
                "Enable SSH on the connection page and save."
            )

        preferred_dump = (
            request.remote_binary_path
            or ssh.get("mongodump_path")
            or None
        )
        if progress_callback:
            progress_callback(0.2, "Locating mongodump on Linux server...")
        try:
            mongodump_path = resolve_remote_mongodump_path(ssh, preferred_dump)
        except RuntimeError as exc:
            raise BackupError(str(exc)) from exc
        if progress_callback:
            progress_callback(0.25, f"Using mongodump: {mongodump_path}")

        backup_root = request.remote_output_dir or ssh.get("remote_backup_dir") or "/mnt/backup"
        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        db = self._mongo_target_database(request)
        mongo_host, mongo_port = mongo_endpoint_from_connection(conn)
        remote_out = f"{backup_root.rstrip('/')}/{remote_backup_folder_name(request.backup_folder_name, db or 'all')}"

        if progress_callback:
            progress_callback(0.3, "Running mongodump on Linux server...")

        mkdir_cmd = f"mkdir -p {shlex.quote(backup_root.rstrip('/'))}"
        exit_code, _out, err = run_ssh_command(
            ssh_host=ssh["ssh_host"],
            fallback_host=ssh["fallback_host"],
            ssh_port=ssh["ssh_port"],
            username=ssh["username"],
            password=ssh["password"],
            command=mkdir_cmd,
        )
        if exit_code != 0:
            raise BackupError(f"Could not create remote backup directory: {err.strip()}")

        remote_cmd = build_mongodump_remote_command(
            mongodump_path=mongodump_path,
            mongo_host=mongo_host,
            mongo_port=mongo_port,
            username=conn.username,
            password=conn.password,
            auth_database=str(auth_db),
            database=db,
            output_dir=remote_out,
            collection=request.collection,
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
        if exit_code != 0:
            detail = stderr.strip() or stdout.strip() or "unknown error"
            if "No such file or directory" in detail and "mongodump" in detail:
                detail += (
                    "\n\nThe mongodump binary path is wrong on the Linux server. "
                    "Update 'Mongodump binary' in Backup, or install MongoDB Database Tools under /mnt."
                )
            raise BackupError(f"Remote mongodump failed: {detail}")

        if db:
            flatten_remote_mongodump_output(ssh, remote_out, db)

        if progress_callback:
            progress_callback(1.0, "Remote backup completed")

        return Path(remote_out)

    def _backup_mssql(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        db = request.database or conn.database
        if request.backup_mode == BackupMode.INCREMENTAL:
            backup_file = str(output_path.with_suffix(".trn"))
            sql = f"BACKUP LOG [{db}] TO DISK = N'{backup_file}'"
        if request.backup_mode == BackupMode.DIFFERENTIAL:
            chain = load_chain(output_path.parent, request)
            if not chain.get("full"):
                raise BackupError(
                    "Run a Full backup first before a differential backup."
                )
            backup_file = str(output_path.with_suffix(".bak"))
            sql = f"BACKUP DATABASE [{db}] TO DISK = N'{backup_file}' WITH DIFFERENTIAL, INIT"
        else:
            backup_file = str(output_path.with_suffix(".bak"))
            sql = f"BACKUP DATABASE [{db}] TO DISK = N'{backup_file}' WITH FORMAT, INIT"
        connector = get_connector(DatabaseType.MSSQL, request.profile)
        connector.execute_script(sql)
        record_backup(
            output_path.parent,
            request,
            {"path": backup_file, "database": db},
            request.backup_mode,
        )
        return Path(backup_file)

    def _backup_oracle(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        return backup_oracle(request, output_path, progress_callback)

    def _compress(self, path: Path) -> Path:
        if self.settings.backup.compression_format == "gzip":
            compressed = path.with_suffix(path.suffix + ".gz")
            with path.open("rb") as f_in, gzip.open(compressed, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            path.unlink(missing_ok=True)
            return compressed
        return path

    def apply_retention_policy(self) -> int:
        """Delete backups older than retention_days. Returns count deleted."""
        retention = self.settings.backup.retention_days
        cutoff = datetime.now() - timedelta(days=retention)
        deleted = 0
        for item in self.output_dir.iterdir():
            if item.is_file() or item.is_dir():
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < cutoff:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    deleted += 1
                    logger.info("Retention: deleted %s", item)
        return deleted

    def list_remote_backups(
        self, profile: str = "default", remote_dir: str | None = None
    ) -> list[dict]:
        """List MongoDB backups stored on the remote Linux server."""
        import shlex as _shlex

        from backend.services.ssh_client import run_ssh_command, ssh_options_from_connection

        conn = resolve_connection(profile)
        ssh = ssh_options_from_connection(conn)
        if not ssh:
            return []

        remote_root = remote_dir or ssh.get("remote_backup_dir", "/mnt/backup")
        quoted_root = _shlex.quote(remote_root.rstrip("/"))
        list_cmd = (
            f"find {quoted_root} -mindepth 1 -maxdepth 1 "
            f"\\( -type d -o -type f \\) -printf '%T@ %s %p\\n' 2>/dev/null | sort -rn"
        )
        exit_code, stdout, _stderr = run_ssh_command(
            ssh_host=ssh["ssh_host"],
            fallback_host=ssh["fallback_host"],
            ssh_port=ssh["ssh_port"],
            username=ssh["username"],
            password=ssh["password"],
            command=list_cmd,
        )
        if exit_code != 0:
            return []

        backups: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            try:
                mtime = float(parts[0])
                size_bytes = int(parts[1])
            except ValueError:
                continue
            path = parts[2].strip()
            if not path:
                continue
            name = path.rstrip("/").split("/")[-1]
            backups.append({
                "name": name,
                "path": path,
                "size_bytes": size_bytes,
                "created_at": datetime.fromtimestamp(mtime).isoformat(),
                "remote": True,
            })
        return backups

    def _resolve_local_backup_dir(self, local_dir: str | None = None) -> Path:
        scan_dir = Path(local_dir) if local_dir else self.output_dir
        if not scan_dir.is_absolute():
            scan_dir = (Path.cwd() / scan_dir).resolve()
        else:
            scan_dir = scan_dir.resolve()
        return scan_dir

    def _folder_size_bytes(self, path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def list_local_backups(self, local_dir: str | None = None) -> list[dict]:
        """List backup files and folders stored on this PC."""
        scan_dir = self._resolve_local_backup_dir(local_dir)
        if not scan_dir.exists():
            return []

        local: list[dict] = []
        for item in sorted(scan_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = item.stat()
            local.append({
                "name": item.name,
                "path": str(item),
                "size_bytes": self._folder_size_bytes(item),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "remote": False,
            })
        return local

    def list_backups(
        self,
        profile: str = "default",
        local_dir: str | None = None,
        remote_dir: str | None = None,
        *,
        list_remote: bool = False,
    ) -> list[dict]:
        """List backups from the remote Linux server or from this PC."""
        if list_remote:
            return self.list_remote_backups(profile, remote_dir=remote_dir)
        return self.list_local_backups(local_dir)

    def get_operation(self, operation_id: str) -> OperationResult | None:
        return _operations.get(operation_id)

    def validate_backup(self, backup_path: str) -> bool:
        """Validate backup file integrity."""
        path = Path(backup_path)
        if not path.exists():
            raise BackupError(f"Backup file not found: {backup_path}")
        if path.suffix == ".gz":
            try:
                with gzip.open(path, "rb") as f:
                    f.read(1024)
                return True
            except Exception as exc:
                raise BackupError(f"Invalid gzip backup: {exc}") from exc
        return path.stat().st_size > 0
