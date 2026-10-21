"""Database migration service with progress monitoring."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

from backend.connectors.base import get_connector
from backend.core.exceptions import MigrationError, MigrationRejected
from backend.core.logging import get_audit_logger
from backend.models.schemas import DatabaseType, MigrationRequest, OperationResult, OperationStatus
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.precheck import quote_ident
from backend.services.pg_mysql_migration.api_request import pg_mysql_request_block
from backend.services.pg_mysql_migration.engine import MysqlTargetSession
from backend.services.pg_mysql_migration.precheck import assess_pg_mysql
from backend.services.pg_mysql_migration.schema import source_count_sql
from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore, MigrationStoreError
from backend.services.pg_mysql_migration.workflow import (
    RECOVERY_GUIDANCE,
    build_pg_mysql_plan,
    start_pg_mysql_migration,
    table_groups,
)

audit = get_audit_logger()

_operations: dict[str, OperationResult] = {}

_STORED_STATUS = {
    "Completed and verified": "completed",
    "Completed with warnings": "completed_with_warnings",
    "Partially completed": "partially_completed",
    "Cancelled": "cancelled",
    "Verification failed": "verification_failed",
    "Failed": "failed",
}
_ENGINE_STATUS = {value: key for key, value in _STORED_STATUS.items()}


def _operation_status(engine_status: str) -> OperationStatus:
    if engine_status == "Cancelled":
        return OperationStatus.CANCELLED
    if engine_status in {"Completed and verified", "Completed with warnings"}:
        return OperationStatus.COMPLETED
    if engine_status == "running":
        return OperationStatus.RUNNING
    return OperationStatus.FAILED


_ROW_STATUS = {
    "running": OperationStatus.RUNNING,
    "completed": OperationStatus.COMPLETED,
    "completed_with_warnings": OperationStatus.COMPLETED,
    "cancelled": OperationStatus.CANCELLED,
    "partially_completed": OperationStatus.FAILED,
    "verification_failed": OperationStatus.FAILED,
    "failed": OperationStatus.FAILED,
    "reconciliation_required": OperationStatus.FAILED,
}


def _result_from_row(row: dict) -> OperationResult:
    stored = row["status"]
    engine_status = _ENGINE_STATUS.get(stored, stored)
    try:
        tables = json.loads(row["tables_json"] or "{}")
    except json.JSONDecodeError:
        tables = {}
    started = datetime.fromisoformat(row["created_at"])
    finished = None if stored == "running" else datetime.fromisoformat(row["updated_at"])
    return OperationResult(
        operation_id=row["operation_id"],
        status=_ROW_STATUS.get(stored, OperationStatus.FAILED),
        message=row["summary"] or stored,
        started_at=started,
        completed_at=finished,
        details={
            "engine": "postgresql_to_mysql",
            "executed": stored != "running",
            "status": engine_status,
            "tables": tables,
            "recovery": row["recovery"],
            "reconciled": bool(row["reconciled"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "backup_file_exists": False,
            "backup_format_checked": False,
            "restore_verified": False,
            "backup_proven": False,
            "source_database": row["source_database"],
            "target_database": row["target_database"],
        },
    )


class MigrationService:
    """Handles schema and data migration between databases."""

    def __init__(self, connector_factory=None, store: MigrationOperationStore | None = None) -> None:
        self._connector_factory = connector_factory or get_connector
        self._store = store or MigrationOperationStore()

    def preview(self, request: MigrationRequest, user: str = "system") -> OperationResult:
        """Return the PostgreSQL-to-MySQL plan. No table is created or changed."""
        if request.source_type != DatabaseType.POSTGRESQL or request.target_type != DatabaseType.MYSQL:
            raise MigrationRejected(self._unsupported_pair(request))
        blocked = pg_mysql_request_block(request, execute=False)
        if blocked:
            raise MigrationRejected(blocked)
        return self._run_pg_mysql(request, user, execute=False)

    def migrate(
        self,
        request: MigrationRequest,
        user: str = "system",
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> OperationResult:
        operation_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        replay = self._replay(request)
        if replay is not None:
            return replay

        if request.source_type == DatabaseType.POSTGRESQL and request.target_type == DatabaseType.MYSQL:
            blocked = pg_mysql_request_block(request, execute=True)
            if blocked:
                raise MigrationRejected(blocked)
            return self._run_pg_mysql(request, user, execute=True, operation_id=operation_id, started_at=started_at)

        raise MigrationRejected(
            "PostgreSQL-to-PostgreSQL migration is performed from the Migration page after validation and confirmation. "
            "This API did not open a connection or copy schema or data."
            if request.source_type == DatabaseType.POSTGRESQL and request.target_type == DatabaseType.POSTGRESQL
            else self._unsupported_pair(request)
        )

    def _run_pg_mysql(
        self,
        request: MigrationRequest,
        user: str,
        *,
        execute: bool,
        operation_id: str | None = None,
        started_at: datetime | None = None,
    ) -> OperationResult:
        operation_id = operation_id or str(uuid.uuid4())
        started_at = started_at or datetime.now(timezone.utc)
        result = OperationResult(
            operation_id=operation_id,
            status=OperationStatus.RUNNING,
            message="Migration preview" if not execute else "Migration started",
            started_at=started_at,
        )
        _operations[operation_id] = result
        claimed = False
        if execute:
            try:
                claimed, existing = self._store.claim(
                    operation_id=operation_id,
                    client_request_id=request.client_request_id,
                    source_database=request.source_database,
                    target_database=request.target_database,
                    scope=request.scope,
                    strategy=request.strategy,
                    confirm_retry=request.confirm_retry,
                )
            except (sqlite3.Error, MigrationStoreError) as exc:
                raise MigrationError(
                    "The migration operation store is unavailable. No schema or data was copied."
                ) from exc
            if not claimed:
                return _result_from_row(existing)
        source = target = None
        source_conn = target_conn = None
        cancel = threading.Event()
        try:
            source = self._connector_factory(request.source_type, request.source_profile)
            target = self._connector_factory(request.target_type, request.target_profile)
            report = assess_pg_mysql(
                source.execute_query,
                target.execute_query,
                scope=request.scope,
                selected_tables=request.tables or None,
                strategy=request.strategy,
                confirm_replace=request.confirm_replace,
                accept_unproven_backup=request.accept_unproven_backup or request.scope == "schema_only",
                batch_size=request.batch_size,
                statement_timeout_ms=request.statement_timeout_ms,
            )
            if report.source_database != request.source_database or report.target_database != request.target_database:
                raise MigrationRejected(
                    "The connected database names do not match the requested source and target identities. "
                    "No schema or data was copied."
                )
            plan = build_pg_mysql_plan(
                report,
                scope=request.scope,
                strategy=request.strategy,
                batch_size=request.batch_size,
                statement_timeout_ms=request.statement_timeout_ms,
            )
            if not execute or not report.ready:
                if claimed:
                    self._store.fail(operation_id, plan.text)
                result.status = OperationStatus.COMPLETED if report.ready and not execute else OperationStatus.FAILED
                result.message = plan.text
                result.details = self._pg_mysql_details(plan, None)
                result.completed_at = datetime.now(timezone.utc)
                return result
            blocked = self._store.bind_target(
                operation_id,
                report.target_server_id,
                confirm_retry=request.confirm_retry,
            )
            if blocked:
                self._store.fail(operation_id, blocked)
                raise MigrationRejected(blocked)

            source_conn = source.open_connection()
            target_conn = target.open_connection()
            try:
                target_conn.autocommit = False
            except Exception:
                pass
            if self._store.cancel_requested(operation_id):
                cancel.set()
            watcher = threading.Thread(target=self._watch_cancel, args=(operation_id, cancel), daemon=True)
            watcher.start()

            def fetch_batches(table: str, columns: list[str], batch_size: int, cancel: threading.Event):
                column_sql = ", ".join(quote_ident(name) for name in columns)
                cursor = source_conn.cursor(name="pgmysql_api")
                try:
                    cursor.itersize = batch_size
                    cursor.execute(f"SELECT {column_sql} FROM public.{quote_ident(table)}")
                    while True:
                        if cancel.is_set():
                            return
                        rows = cursor.fetchmany(batch_size)
                        if not rows:
                            return
                        yield rows
                finally:
                    cursor.close()

            def count_source(table: str) -> int:
                rows = source.execute_query(source_count_sql(table))
                return int(rows[0].get("row_count") or 0)

            run = start_pg_mysql_migration(
                plan,
                confirmed=True,
                operator=user,
                fetch_batches=fetch_batches,
                target=MysqlTargetSession(target_conn),
                count_source=count_source,
                cancel=cancel,
            )
            groups = table_groups(run.outcomes)
            recovery = RECOVERY_GUIDANCE.get(run.status, "")
            self._store.finish(
                operation_id,
                status=_STORED_STATUS.get(run.status, "failed"),
                summary=run.summary,
                tables=groups,
                recovery=recovery,
            )
            result.status = _operation_status(run.status)
            result.message = redact_text(run.summary) or run.status
            result.details = self._pg_mysql_details(plan, run)
            result.completed_at = datetime.now(timezone.utc)
            return result
        except MigrationRejected as exc:
            if claimed:
                self._store.fail(operation_id, str(exc))
            result.status = OperationStatus.FAILED
            result.message = str(exc)
            result.completed_at = datetime.now(timezone.utc)
            raise
        except Exception as exc:
            message = redact_text(str(exc)) or "Migration failed."
            if claimed:
                self._store.fail(operation_id, message)
            result.status = OperationStatus.FAILED
            result.message = message
            result.completed_at = datetime.now(timezone.utc)
            audit.log("migration", user, "postgresql-to-mysql", "failed", {"error": message, "migration_id": operation_id})
            raise MigrationError(message) from exc
        finally:
            for conn in (source_conn, target_conn):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            _operations[operation_id] = result
            cancel.set()

    @staticmethod
    def _pg_mysql_details(plan, run) -> dict:
        precheck = plan.precheck
        outcomes = []
        if run is not None:
            outcomes = [
                {"name": item.name, "action": item.action, "detail": redact_text(item.detail) or ""}
                for item in run.outcomes
            ]
        return {
            "engine": "postgresql_to_mysql",
            "ready": precheck.ready,
            "executed": run is not None,
            "status": run.status if run is not None else "preview",
            "scope": plan.scope,
            "strategy": plan.strategy,
            "plan": plan.text,
            "conversions": list(precheck.conversions),
            "unsupported": list(precheck.unsupported),
            "outcomes": outcomes,
            "tables": table_groups(run.outcomes) if run is not None else {},
            "recovery": RECOVERY_GUIDANCE.get(run.status, "") if run is not None else "",
            "migration_completed": bool(run is not None and run.status in {"Completed and verified", "Completed with warnings"}),
            "backup_file_exists": bool(getattr(precheck.backup, "file_exists", False)),
            "backup_format_checked": bool(getattr(precheck.backup, "format_checked", False)),
            "restore_verified": False,
            "backup_proven": False,
            "source_database": precheck.source_database,
            "target_database": precheck.target_database,
        }

    def request_cancel(self, operation_id: str) -> bool:
        """Ask a running migration to stop between batches. The current statement can still finish."""
        return self._store.request_cancel(operation_id)

    def reconcile(self, operation_id: str) -> dict | None:
        """Record operator review of an interrupted or partial migration. Does not rerun it."""
        return self._store.reconcile(operation_id)

    def _watch_cancel(self, operation_id: str, cancel: threading.Event) -> None:
        while not cancel.is_set():
            if self._store.cancel_requested(operation_id):
                cancel.set()
                return
            if cancel.wait(0.2):
                return

    def _replay(self, request: MigrationRequest) -> OperationResult | None:
        row = self._store.get_by_client_request(request.client_request_id)
        if row is None:
            return None
        return _result_from_row(row)

    @staticmethod
    def _unsupported_pair(request: MigrationRequest) -> str:
        return (
            f"{request.source_type.value}-to-{request.target_type.value} migration is not implemented. "
            "No schema or data was copied."
        )

    def get_operation(self, operation_id: str) -> OperationResult | None:
        return _operations.get(operation_id)

    def get_progress(self, operation_id: str) -> dict:
        op = _operations.get(operation_id)
        if op is None:
            row = self._store.get(operation_id)
            if row is None:
                raise MigrationError(f"Operation not found: {operation_id}")
            op = _result_from_row(row)
        return {
            "operation_id": operation_id,
            "status": op.status.value,
            "message": op.message,
            "details": op.details,
        }
