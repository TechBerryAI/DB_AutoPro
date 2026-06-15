"""Restore service with validation support."""

from __future__ import annotations

import gzip
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.connectors.base import get_connector, resolve_connection
from backend.core.exceptions import RestoreError
from backend.core.logging import get_audit_logger, get_logger
from backend.models.schemas import DatabaseType, OperationResult, OperationStatus, RestoreRequest
from backend.services.backup_service import BackupService

logger = get_logger("services.restore")
audit = get_audit_logger()

_operations: dict[str, OperationResult] = {}


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
            if not backup_path.exists():
                raise RestoreError(f"Backup not found: {request.backup_path}")

            if request.validate_backup:
                if progress_callback:
                    progress_callback(0.1, "Validating backup...")
                self._backup_service.validate_backup(request.backup_path)

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
            audit.log("restore", user, request.backup_path, "success")
            logger.info("Restore completed from %s", request.backup_path)

        except Exception as exc:
            result.status = OperationStatus.FAILED
            result.message = str(exc)
            result.completed_at = datetime.now(timezone.utc)
            audit.log("restore", user, request.backup_path, "failed", {"error": str(exc)})
            raise RestoreError(str(exc)) from exc
        finally:
            _operations[operation_id] = result

        return result

    def _decompress_if_needed(self, path: Path) -> Path:
        if path.suffix == ".gz":
            decompressed = path.with_suffix("")
            with gzip.open(path, "rb") as f_in, decompressed.open("wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            return decompressed
        return path

    def _restore_postgresql(
        self,
        request: RestoreRequest,
        backup_path: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> None:
        conn = resolve_connection(request.profile)
        env = {"PGPASSWORD": conn.password}
        db = request.database or conn.database

        if backup_path.is_dir():
            raise RestoreError("Use pg_restore for directory format backups via CLI")

        cmd = [
            "psql",
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
        cmd = [
            "mongorestore",
            "--host", conn.host,
            "--port", str(conn.port),
            "--username", conn.username,
            "--password", conn.password,
            "--db", request.database or conn.database,
            str(backup_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RestoreError(f"MongoDB restore failed: {result.stderr}")

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
