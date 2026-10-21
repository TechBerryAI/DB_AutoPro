"""Reviewed PostgreSQL migration. The source is not modified."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from backend.core.logging import get_audit_logger
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.engine import MigrationRun, run_migration
from backend.services.pg_migration.precheck import (
    STRATEGY_REPLACE,
    MigrationPrecheck,
    assess_migration,
)

RECOVERY_GUIDANCE = {
    "Partially completed": "Committed target changes remain. Restoring a backup is the recovery path. This attempt was not rolled back.",
    "Cancelled": "Committed target changes remain. Restoring a backup is the recovery path for those changes. An in-progress COPY is rolled back only when that rollback was verified.",
    "Completed with warnings": "Review replaced or skipped objects before using the target. A confirmed replacement is recovered from a backup, not by a migration rollback.",
    "Verification failed": "Do not use the target as a verified copy. Restoring a backup is the recovery path if the target must be returned to its earlier contents.",
    "Completed and verified": "Backup readiness was not proven. Matching row counts are not a restore test.",
    "Failed": "No target change was made by this attempt.",
}

_active_targets: set[tuple[str, str]] = set()
_guard = threading.Lock()


@dataclass
class MigrationPlan:
    precheck: MigrationPrecheck
    scope: str
    strategy: str
    batch_size: int
    statement_timeout_ms: int
    text: str


def build_plan(precheck: MigrationPrecheck, *, scope: str, strategy: str, batch_size: int, statement_timeout_ms: int) -> MigrationPlan:
    lines = [f"[{item.status}] {item.code}: {item.summary}" for item in precheck.checks]
    if not precheck.ready:
        lines.append("The migration is blocked. Nothing will be copied.")
    else:
        lines.append(f"Scope: {scope}. Conflict strategy: {strategy}. Batch size: {batch_size}.")
        lines.append("Tables: " + ", ".join(precheck.tables))
        lines.append("Production data is overwritten only when replacement was explicitly confirmed.")
        lines.append("Source data is not changed. A failed copy is not claimed to be rolled back.")
        lines.append("Confirm this source, target, and plan before starting.")
    backup = precheck.backup
    lines.append(
        "Migration completion, backup creation, backup validation, and restore verification are separate. "
        f"Backup file exists: {bool(getattr(backup, 'file_exists', False))}. "
        f"Backup format checked: {bool(getattr(backup, 'format_checked', False))}. "
        f"Restore verified: {bool(getattr(backup, 'restore_verified', False))}."
    )
    if precheck.backup_proven:
        lines.append("Restore was successfully tested and verified for the supplied backup.")
    else:
        lines.append("Backup readiness: not proven. An existing backup file is not a tested restore.")
    return MigrationPlan(precheck, scope, strategy, batch_size, statement_timeout_ms, "\n".join(lines))


def start_migration(
    plan: MigrationPlan,
    *,
    confirmed: bool,
    operator: str,
    source_query,
    target_query,
    target_execute,
    copy_rows,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> MigrationRun:
    precheck = plan.precheck
    if not precheck.ready:
        return MigrationRun("Failed", precheck.stop_reason or "Prechecks did not pass.")
    if not confirmed:
        return MigrationRun("Failed", "The administrator has not confirmed the source, target, and plan.")
    if not operator.strip():
        return MigrationRun("Failed", "Operator identity is missing.")
    identity = precheck.target_identity
    key = (identity.system_identifier, identity.database_oid)
    with _guard:
        if key in _active_targets:
            return MigrationRun("Failed", "A migration is already running for this target database.")
        _active_targets.add(key)
    migration_id = str(uuid.uuid4())
    token = cancel or threading.Event()
    try:
        if plan.statement_timeout_ms:
            target_execute(f"SET statement_timeout = {int(plan.statement_timeout_ms)}")
        result = run_migration(
            precheck,
            scope=plan.scope,
            strategy=plan.strategy,
            batch_size=plan.batch_size,
            source_query=source_query,
            target_query=target_query,
            target_execute=target_execute,
            copy_rows=copy_rows,
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
    _audit(operator, plan, result)
    return result


def _audit(operator: str, plan: MigrationPlan, result: MigrationRun) -> None:
    source = plan.precheck.source_identity
    target = plan.precheck.target_identity
    status = result.status
    replaced = plan.strategy == STRATEGY_REPLACE and any(
        item.code == "conflicts" and "explicitly confirmed" in item.summary for item in plan.precheck.checks
    )
    get_audit_logger().log(
        "migration",
        operator,
        f"{getattr(source, 'database_name', '')}->{getattr(target, 'database_name', '')}",
        status,
        {
            "migration_id": result.migration_id,
            "scope": plan.scope,
            "status": result.status,
            "source_cluster": getattr(source, "system_identifier", ""),
            "target_cluster": getattr(target, "system_identifier", ""),
            "target_oid": getattr(target, "database_oid", ""),
            "backup_file_exists": bool(getattr(plan.precheck.backup, "file_exists", False)),
            "backup_format_checked": bool(getattr(plan.precheck.backup, "format_checked", False)),
            "restore_verified": bool(getattr(plan.precheck.backup, "restore_verified", False)),
            "backup_proven": plan.precheck.backup_proven,
            "replace_confirmed": replaced,
            "outcomes": [{"name": item.name, "action": item.action} for item in result.outcomes],
            "recovery": RECOVERY_GUIDANCE.get(result.status, ""),
            "summary": redact_text(result.summary) or "",
        },
    )
