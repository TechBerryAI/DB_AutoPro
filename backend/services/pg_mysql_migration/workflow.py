"""Reviewed PostgreSQL-to-MySQL migration plan and audit."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from backend.core.logging import get_audit_logger
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_mysql_migration.engine import MysqlMigrationRun, run_pg_mysql_migration
from backend.services.pg_mysql_migration.precheck import PgMysqlPrecheck, assess_pg_mysql

RECOVERY_GUIDANCE = {
    "Partially completed": "Committed MySQL changes remain. This attempt was not rolled back. Restore the MySQL database from a MySQL backup if those changes must be undone.",
    "Cancelled": "Committed MySQL changes remain. An open insert transaction is rolled back only for the table that was in progress.",
    "Completed with warnings": "Review skipped or replaced MySQL tables. A confirmed DROP is not undone by this migration.",
    "Verification failed": "Do not use the MySQL target as a verified copy. Restoring a MySQL backup is the recovery path.",
    "Completed and verified": "Matching row counts are not a MySQL restore test. A PostgreSQL backup file was not proven against this target.",
    "Failed": "No committed MySQL change was made by this attempt.",
}

_active_targets: set[tuple[str, str]] = set()
_incomplete_targets: dict[tuple[str, str], str] = {}
_guard = threading.Lock()

_UNSAFE_RETRY = {"Partially completed", "Cancelled", "Verification failed"}
_TABLE_LABELS = {
    "data_copied": "Copied",
    "schema_created": "Created",
    "skipped": "Skipped",
    "dropped": "Dropped",
    "failed": "Failed",
    "rolled_back": "Rolled back",
}


def table_groups(outcomes) -> dict[str, list[str]]:
    """Group table and constraint names by what happened to them."""
    groups = {label: [] for label in _TABLE_LABELS.values()}
    for item in outcomes:
        label = _TABLE_LABELS.get(item.action)
        if label:
            groups[label].append(item.name)
    return {label: names for label, names in groups.items() if names}


def describe_tables(outcomes) -> str:
    groups = table_groups(outcomes)
    return " ".join(f"{label}: {', '.join(names)}." for label, names in groups.items())


def incomplete_migration(server_id: str, database: str) -> str | None:
    return _incomplete_targets.get((server_id, database))


def clear_migration_guards() -> None:
    with _guard:
        _active_targets.clear()
        _incomplete_targets.clear()


@dataclass
class PgMysqlPlan:
    precheck: PgMysqlPrecheck
    scope: str
    strategy: str
    batch_size: int
    statement_timeout_ms: int
    text: str
    engine: str = "postgresql_to_mysql"


def build_pg_mysql_plan(
    precheck: PgMysqlPrecheck,
    *,
    scope: str,
    strategy: str,
    batch_size: int,
    statement_timeout_ms: int,
) -> PgMysqlPlan:
    lines = [f"[{item.status}] {item.code}: {item.summary}" for item in precheck.checks]
    lines.append("Engine: PostgreSQL to MySQL. Rows are inserted with parameterized statements, not PostgreSQL COPY.")
    if not precheck.ready:
        lines.append("The migration is blocked. Nothing will be copied.")
    else:
        lines.append(f"Scope: {scope}. Conflict strategy: {strategy}. Batch size: {batch_size}.")
        if precheck.dest_will_create:
            lines.append(
                f"Start will create MySQL database {precheck.target_database} on the target server, "
                f"then copy public tables from {precheck.source_database}. "
                "Users, roles, files, views, and routines are not copied."
            )
        lines.append("Tables: " + ", ".join(precheck.tables))
        lines.append("Existing MySQL rows are dropped only when replacement was explicitly confirmed.")
        lines.append("Source data is not changed. MySQL DDL commits immediately and is not rolled back with the insert.")
        lines.append("Confirm this source, target, and plan before starting.")
    backup = precheck.backup
    lines.append(
        "Migration completion, backup creation, backup validation, and restore verification are separate. "
        f"Backup file exists: {bool(getattr(backup, 'file_exists', False))}. "
        f"Backup format checked: {bool(getattr(backup, 'format_checked', False))}. "
        f"Restore verified: {bool(getattr(backup, 'restore_verified', False))}."
    )
    if precheck.unsupported:
        lines.append("Not migrated: " + ", ".join(precheck.unsupported) + ".")
    lines.append("A completed table copy is not a backup and is not a tested MySQL restore.")
    return PgMysqlPlan(precheck, scope, strategy, batch_size, statement_timeout_ms, "\n".join(lines))


def start_pg_mysql_migration(
    plan: PgMysqlPlan,
    *,
    confirmed: bool,
    operator: str,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> MysqlMigrationRun:
    precheck = plan.precheck
    if not precheck.ready:
        return MysqlMigrationRun("Failed", precheck.stop_reason or "Prechecks did not pass.")
    if not confirmed:
        return MysqlMigrationRun("Failed", "The administrator has not confirmed the source, target, and plan.")
    if not operator.strip():
        return MysqlMigrationRun("Failed", "Operator identity is missing.")
    key = (precheck.target_server_id, precheck.target_database)
    with _guard:
        if key in _active_targets:
            return MysqlMigrationRun("Failed", "A migration is already running for this MySQL database.")
        _active_targets.add(key)
    migration_id = str(uuid.uuid4())
    token = cancel or threading.Event()
    try:
        if plan.statement_timeout_ms:
            target.execute(f"SET SESSION max_execution_time = {int(plan.statement_timeout_ms)}")
        result = run_pg_mysql_migration(
            precheck.table_specs,
            precheck.conflicts,
            scope=plan.scope,
            strategy=plan.strategy,
            batch_size=plan.batch_size,
            fetch_batches=fetch_batches,
            target=target,
            count_source=count_source,
            cancel=token,
            migration_id=migration_id,
            on_progress=on_progress,
        )
    finally:
        with _guard:
            _active_targets.discard(key)
    guidance = RECOVERY_GUIDANCE.get(result.status, "")
    if guidance and guidance not in result.summary:
        result.summary = f"{result.summary} {guidance}"
    table_text = describe_tables(result.outcomes)
    if table_text and table_text not in result.summary:
        result.summary = f"{result.summary} {table_text}"
    with _guard:
        key_done = (precheck.target_server_id, precheck.target_database)
        if result.status in _UNSAFE_RETRY:
            _incomplete_targets[key_done] = result.status
        elif result.status in {"Completed and verified", "Completed with warnings"}:
            _incomplete_targets.pop(key_done, None)
    _audit(operator, plan, result)
    return result


def _audit(operator: str, plan: PgMysqlPlan, result: MysqlMigrationRun) -> None:
    precheck = plan.precheck
    get_audit_logger().log(
        "migration",
        operator,
        f"{precheck.source_database}->{precheck.target_database}",
        result.status,
        {
            "migration_id": result.migration_id,
            "engine": plan.engine,
            "scope": plan.scope,
            "status": result.status,
            "source_database": precheck.source_database,
            "target_database": precheck.target_database,
            "target_server_id": precheck.target_server_id,
            "backup_file_exists": bool(getattr(precheck.backup, "file_exists", False)),
            "backup_format_checked": bool(getattr(precheck.backup, "format_checked", False)),
            "restore_verified": False,
            "backup_proven": False,
            "replace_confirmed": any(item.action == "dropped" for item in result.outcomes),
            "outcomes": [{"name": item.name, "action": item.action} for item in result.outcomes],
            "tables": table_groups(result.outcomes),
            "recovery": RECOVERY_GUIDANCE.get(result.status, ""),
            "summary": redact_text(result.summary) or "",
        },
    )
