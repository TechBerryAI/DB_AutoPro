"""Reviewed PostgreSQL-to-SQL Server or Oracle plan and audit."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from backend.core.logging import get_audit_logger
from backend.services.migration_framework.registry import MSSQL, _norm
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_sql_migration.engine import SqlMigrationRun, run_pg_sql_migration
from backend.services.pg_sql_migration.precheck import PgSqlPrecheck


RECOVERY_GUIDANCE = {
    "Partially completed": "Committed target changes remain. This attempt was not rolled back.",
    "Cancelled": "Committed target changes remain. An open insert is rolled back only for the table in progress.",
    "Completed with warnings": "Review skipped or replaced target tables.",
    "Verification failed": "Do not use the target as a verified copy.",
    "Completed and verified": "Matching row counts are not a restore test.",
    "Failed": "No committed target change was made by this attempt.",
}


@dataclass
class PgSqlPlan:
    precheck: PgSqlPrecheck
    scope: str
    strategy: str
    batch_size: int
    statement_timeout_ms: int
    text: str
    engine: str = ""


def build_pg_sql_plan(precheck: PgSqlPrecheck, *, scope: str, strategy: str, batch_size: int, statement_timeout_ms: int) -> PgSqlPlan:
    kind = _norm(precheck.target_kind)
    label = "SQL Server" if kind == MSSQL else "Oracle"
    lines = [f"[{item.status}] {item.code}: {item.summary}" for item in precheck.checks]
    lines.append(f"Engine: PostgreSQL to {label}. Rows are inserted with parameterized statements.")
    if not precheck.ready:
        lines.append("The migration is blocked. Nothing will be copied.")
        if precheck.stop_reason:
            lines.append(f"Blocked because: {precheck.stop_reason}")
    else:
        lines.append(f"Scope: {scope}. Conflict strategy: {strategy}. Batch size: {batch_size}.")
        if precheck.dest_will_create:
            lines.append(
                f"Start will create {label} database {precheck.target_database}, then copy "
                f"public tables from {precheck.source_database}. Users, roles, files, views, and routines are not copied."
            )
        lines.append("Objects: " + ", ".join(precheck.tables))
        lines.append("Source data is not changed. Confirm this source, target, and plan before starting.")
    lines.append(f"A completed table copy is not a backup and is not a tested {label} restore.")
    return PgSqlPlan(
        precheck,
        scope,
        strategy,
        batch_size,
        statement_timeout_ms,
        "\n".join(lines),
        engine=precheck.engine or f"postgresql_to_{kind}",
    )


def start_pg_sql_migration(
    plan: PgSqlPlan,
    *,
    confirmed: bool,
    operator: str,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> SqlMigrationRun:
    precheck = plan.precheck
    label = "SQL Server" if _norm(precheck.target_kind) == MSSQL else "Oracle"
    if not precheck.ready:
        return SqlMigrationRun("Failed", precheck.stop_reason or "Prechecks did not pass.")
    if not confirmed:
        return SqlMigrationRun("Failed", "The administrator has not confirmed the source, target, and plan.")
    if not operator.strip():
        return SqlMigrationRun("Failed", "Operator identity is missing.")
    migration_id = str(uuid.uuid4())
    result = run_pg_sql_migration(
        precheck.table_specs,
        precheck.conflicts,
        target_kind=precheck.target_kind,
        scope=plan.scope,
        strategy=plan.strategy,
        batch_size=plan.batch_size,
        fetch_batches=fetch_batches,
        target=target,
        count_source=count_source,
        cancel=cancel or threading.Event(),
        migration_id=migration_id,
        on_progress=on_progress,
    )
    guidance = RECOVERY_GUIDANCE.get(result.status, "")
    if guidance and guidance not in result.summary:
        result.summary = f"{result.summary} {guidance}"
    get_audit_logger().log(
        "migration",
        operator,
        f"{precheck.source_database}->{precheck.target_database}",
        result.status,
        {"engine": plan.engine, "migration_id": result.migration_id, "target": label, "summary": redact_text(result.summary) or ""},
    )
    return result
