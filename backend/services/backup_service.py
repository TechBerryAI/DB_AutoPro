"""Backup service with multi-database support, compression, and retention."""

from __future__ import annotations

import gzip
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
from backend.models.schemas import (
    BackupMethod,
    BackupRequest,
    DatabaseType,
    OperationResult,
    OperationStatus,
)

logger = get_logger("services.backup")
audit = get_audit_logger()

# In-memory operation tracking (production would use Redis/DB)
_operations: dict[str, OperationResult] = {}


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

            if request.compress and self.settings.backup.compression:
                if progress_callback:
                    progress_callback(0.8, "Compressing backup...")
                final_path = self._compress(final_path)

            if progress_callback:
                progress_callback(1.0, "Backup completed")

            result.status = OperationStatus.COMPLETED
            result.message = "Backup completed successfully"
            result.output_path = str(final_path)
            result.completed_at = datetime.now(timezone.utc)
            result.details = {
                "size_bytes": final_path.stat().st_size,
                "method": request.method.value,
                "db_type": request.db_type.value,
            }

            audit.log("backup", user, str(final_path), "success", result.details)
            logger.info("Backup completed: %s", final_path)

        except Exception as exc:
            result.status = OperationStatus.FAILED
            result.message = str(exc)
            result.completed_at = datetime.now(timezone.utc)
            audit.log("backup", user, request.profile, "failed", {"error": str(exc)})
            logger.error("Backup failed: %s", exc)
            raise BackupError(str(exc)) from exc
        finally:
            _operations[operation_id] = result

        return result

    def _resolve_output_path(self, request: BackupRequest) -> Path:
        if request.output_path:
            return Path(request.output_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_name = request.database or "all"
        filename = f"{request.db_type.value}_{db_name}_{timestamp}.sql"
        return self.output_dir / filename

    def _backup_postgresql(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        env = {"PGPASSWORD": conn.password}

        if request.method == BackupMethod.PG_DUMPALL:
            cmd = [
                "pg_dumpall",
                "-h", conn.host,
                "-p", str(conn.port),
                "-U", conn.username,
                "-f", str(output_path),
            ]
        elif request.method == BackupMethod.PG_BASEBACKUP:
            backup_dir = output_path.with_suffix("")
            backup_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                "pg_basebackup",
                "-h", conn.host,
                "-p", str(conn.port),
                "-U", conn.username,
                "-D", str(backup_dir),
                "-Ft", "-z", "-P",
            ]
            subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
            return backup_dir
        else:
            db = request.database or conn.database
            cmd = [
                "pg_dump",
                "-h", conn.host,
                "-p", str(conn.port),
                "-U", conn.username,
                "-d", db,
                "-f", str(output_path),
                "--format=plain",
            ]

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise BackupError(f"pg_dump failed: {result.stderr}")
        return output_path

    def _backup_mysql(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        db = request.database or conn.database
        cmd = [
            "mysqldump",
            "-h", conn.host,
            "-P", str(conn.port),
            "-u", conn.username,
            f"-p{conn.password}",
            db,
            "--result-file", str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise BackupError(f"mysqldump failed: {result.stderr}")
        return output_path

    def _backup_mongodb(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        dump_dir = output_path.with_suffix("")
        cmd = [
            "mongodump",
            "--host", conn.host,
            "--port", str(conn.port),
            "--username", conn.username,
            "--password", conn.password,
            "--db", request.database or conn.database,
            "--out", str(dump_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise BackupError(f"mongodump failed: {result.stderr}")
        return dump_dir

    def _backup_mssql(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        db = request.database or conn.database
        backup_file = str(output_path.with_suffix(".bak"))
        connector = get_connector(DatabaseType.MSSQL, request.profile)
        connector.execute_script(
            f"BACKUP DATABASE [{db}] TO DISK = N'{backup_file}' WITH FORMAT, INIT"
        )
        return Path(backup_file)

    def _backup_oracle(
        self,
        request: BackupRequest,
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> Path:
        conn = resolve_connection(request.profile)
        cmd = [
            "expdp",
            f"{conn.username}/{conn.password}@{conn.host}:{conn.port}/{conn.database}",
            f"DIRECTORY=DATA_PUMP_DIR",
            f"DUMPFILE={output_path.name}",
            "FULL=Y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise BackupError(f"Oracle expdp failed: {result.stderr}")
        return output_path

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

    def list_backups(self) -> list[dict]:
        """List available backup files."""
        backups = []
        for item in sorted(self.output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = item.stat()
            backups.append({
                "name": item.name,
                "path": str(item),
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return backups

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
