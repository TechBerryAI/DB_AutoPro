"""Reviewed PostgreSQL-to-MongoDB plan and audit."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from backend.core.logging import get_audit_logger
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_mongo_migration.engine import MongoMigrationRun, run_pg_mongo_migration
from backend.services.pg_mongo_migration.precheck import PgMongoPrecheck

RECOVERY_GUIDANCE = {
    "Partially completed": "Written MongoDB collections remain. This attempt was not rolled back.",
    "Cancelled": "Written MongoDB collections remain. The collection in progress may be incomplete.",
    "Completed with warnings": "Review skipped or replaced MongoDB collections.",
    "Verification failed": "Do not use the MongoDB target as a verified copy.",
    "Completed and verified": "Matching document counts are not a restore test.",
    "Failed": "No MongoDB collection was written by this attempt.",
}


@dataclass
class PgMongoPlan:
    precheck: PgMongoPrecheck
    scope: str
    strategy: str
    batch_size: int
    statement_timeout_ms: int
    text: str
    engine: str = "postgresql_to_mongodb"


def build_pg_mongo_plan(precheck: PgMongoPrecheck, *, scope: str, strategy: str, batch_size: int, statement_timeout_ms: int) -> PgMongoPlan:
    lines = [f"[{item.status}] {item.code}: {item.summary}" for item in precheck.checks]
    lines.append("Engine: PostgreSQL to MongoDB. Each public table becomes a collection of documents.")
    if not precheck.ready:
        lines.append("The migration is blocked. Nothing will be copied.")
        if precheck.stop_reason:
            lines.append(f"Blocked because: {precheck.stop_reason}")
    else:
        lines.append(f"Scope: {scope}. Conflict strategy: {strategy}. Batch size: {batch_size}.")
        if precheck.dest_will_create:
            lines.append(
                f"Start will create MongoDB database {precheck.target_database} when the first collection is written "
                f"from {precheck.source_database}. Users, roles, views, and indexes are not copied."
            )
        lines.append("Collections: " + ", ".join(precheck.tables))
        lines.append("Source data is not changed. Confirm this source, target, and plan before starting.")
    lines.append("A completed collection copy is not a backup and is not a tested MongoDB restore.")
    return PgMongoPlan(precheck, scope, strategy, batch_size, statement_timeout_ms, "\n".join(lines), engine=precheck.engine)


def start_pg_mongo_migration(
    plan: PgMongoPlan,
    *,
    confirmed: bool,
    operator: str,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> MongoMigrationRun:
    precheck = plan.precheck
    if not precheck.ready:
        return MongoMigrationRun("Failed", precheck.stop_reason or "Prechecks did not pass.")
    if not confirmed:
        return MongoMigrationRun("Failed", "The administrator has not confirmed the source, target, and plan.")
    if not operator.strip():
        return MongoMigrationRun("Failed", "Operator identity is missing.")
    result = run_pg_mongo_migration(
        precheck.table_specs,
        precheck.conflicts,
        scope=plan.scope,
        strategy=plan.strategy,
        batch_size=plan.batch_size,
        fetch_batches=fetch_batches,
        target=target,
        count_source=count_source,
        cancel=cancel or threading.Event(),
        migration_id=str(uuid.uuid4()),
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
