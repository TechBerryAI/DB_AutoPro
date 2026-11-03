"""Reviewed inbound PostgreSQL migration plan and audit."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from backend.core.logging import get_audit_logger
from backend.services.mysql_patch.redaction import redact_text
from backend.services.to_pg_migration.engine import ToPgMigrationRun, run_to_pg_migration
from backend.services.to_pg_migration.precheck import ToPgPrecheck, assess_to_pg

RECOVERY_GUIDANCE = {
    "Partially completed": "Committed PostgreSQL changes remain. This attempt was not rolled back.",
    "Cancelled": "Committed PostgreSQL changes remain. An open insert is rolled back only for the table in progress.",
    "Completed with warnings": "Review skipped or replaced PostgreSQL tables.",
    "Verification failed": "Do not use the PostgreSQL target as a verified copy.",
    "Completed and verified": "Matching row counts are not a restore test.",
    "Failed": "No committed PostgreSQL change was made by this attempt.",
}


@dataclass
class ToPgPlan:
    precheck: ToPgPrecheck
    scope: str
    strategy: str
    batch_size: int
    statement_timeout_ms: int
    text: str
    engine: str = "to_postgresql"


def build_to_pg_plan(precheck: ToPgPrecheck, *, scope: str, strategy: str, batch_size: int, statement_timeout_ms: int) -> ToPgPlan:
    kind = precheck.source_kind or "source"
    lines = [f"[{item.status}] {item.code}: {item.summary}" for item in precheck.checks]
    lines.append(f"Engine: {kind} to PostgreSQL. Rows are inserted with parameterized statements.")
    if not precheck.ready:
        lines.append("The migration is blocked. Nothing will be copied.")
        if precheck.stop_reason:
            lines.append(f"Blocked because: {precheck.stop_reason}")
    else:
        lines.append(f"Scope: {scope}. Conflict strategy: {strategy}. Batch size: {batch_size}.")
        if precheck.dest_will_create:
            lines.append(
                f"Start will create PostgreSQL database {precheck.target_database}, then copy "
                f"{'collections' if kind == 'mongodb' else 'tables'} from {precheck.source_database or kind}. "
                "Users, roles, files, views, and routines are not copied."
            )
        lines.append("Objects: " + ", ".join(precheck.tables))
        lines.append("Source data is not changed. Confirm this source, target, and plan before starting.")
    lines.append("A completed table copy is not a backup and is not a tested PostgreSQL restore.")
    return ToPgPlan(precheck, scope, strategy, batch_size, statement_timeout_ms, "\n".join(lines), engine=precheck.engine or f"{kind}_to_postgresql")


def start_to_pg_migration(
    plan: ToPgPlan,
    *,
    confirmed: bool,
    operator: str,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> ToPgMigrationRun:
    precheck = plan.precheck
    if not precheck.ready:
        return ToPgMigrationRun("Failed", precheck.stop_reason or "Prechecks did not pass.")
    if not confirmed:
        return ToPgMigrationRun("Failed", "The administrator has not confirmed the source, target, and plan.")
    if not operator.strip():
        return ToPgMigrationRun("Failed", "Operator identity is missing.")
    migration_id = str(uuid.uuid4())
    if plan.statement_timeout_ms:
        try:
            target.execute(f"SET statement_timeout = {int(plan.statement_timeout_ms)}")
        except Exception:
            pass
    result = run_to_pg_migration(
        precheck.table_specs,
        precheck.conflicts,
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
        {"engine": plan.engine, "migration_id": result.migration_id, "summary": redact_text(result.summary) or ""},
    )
    return result
