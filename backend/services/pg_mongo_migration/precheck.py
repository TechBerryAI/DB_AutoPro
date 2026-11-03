"""PostgreSQL-to-MongoDB checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.migration_framework.destination import destination_name_error
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.backup_proof import BackupReadiness, inspect_backup_file
from backend.services.pg_migration.precheck import (
    SCOPE_DATA,
    SCOPE_SCHEMA,
    SCOPES,
    STRATEGY_FAIL,
    STRATEGY_REPLACE,
    STRATEGY_SKIP,
    PUBLIC_TABLES_SQL,
)
from backend.services.pg_mongo_migration.schema import CollectionSpec, load_collection

SOURCE_IDENTITY_SQL = "SELECT version() AS version, current_database() AS database_name"


@dataclass
class Check:
    code: str
    status: str
    summary: str


@dataclass
class PgMongoPrecheck:
    ready: bool
    checks: list[Check] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    table_specs: list[CollectionSpec] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    conversions: list[str] = field(default_factory=list)
    stop_reason: str = ""
    backup: BackupReadiness | None = None
    source_database: str = ""
    target_database: str = ""
    source_version: str = ""
    target_version: str = ""
    target_server_id: str = ""
    dest_will_create: bool = False
    engine: str = "postgresql_to_mongodb"


def assess_pg_mongo(
    source_query,
    *,
    list_databases,
    list_collections,
    get_version,
    scope: str,
    selected_tables: list[str] | None,
    strategy: str,
    confirm_replace: bool,
    accept_unproven_backup: bool,
    batch_size: int,
    statement_timeout_ms: int,
    expected_database: str | None = None,
) -> PgMongoPrecheck:
    report = PgMongoPrecheck(ready=False)
    if scope not in SCOPES:
        return _stop(report, "scope", f"Migration scope {scope} is not supported.")
    if strategy not in {STRATEGY_SKIP, STRATEGY_FAIL, STRATEGY_REPLACE}:
        return _stop(report, "strategy", f"Conflict strategy {strategy} is not supported.")
    if batch_size < 1:
        return _stop(report, "batch_size", "Batch size must be at least 1.")
    if statement_timeout_ms < 0:
        return _stop(report, "statement_timeout", "Statement timeout cannot be negative.")
    if scope == SCOPE_DATA and strategy == STRATEGY_REPLACE:
        return _stop(report, "strategy", "Data-only migration does not replace target collections.")
    report.checks.append(Check("scope", "pass", f"Scope is {scope}."))

    try:
        source_rows = source_query(SOURCE_IDENTITY_SQL)
    except Exception as exc:
        return _stop(report, "source_identity", f"Source identity could not be read ({exc})")
    source_version = str((source_rows or [{}])[0].get("version") or "")
    if "postgresql" not in source_version.lower():
        return _stop(report, "source_type", "The source connection is not PostgreSQL. No schema or data was copied.")
    report.source_version = source_version
    report.source_database = str((source_rows or [{}])[0].get("database_name") or "")
    report.checks.append(Check("source_identity", "pass", f"PostgreSQL database {report.source_database}."))

    wanted = (expected_database or "").strip()
    name_error = destination_name_error(wanted, "mongodb") if wanted else "Enter the MongoDB destination database name."
    if name_error:
        return _stop(report, "target_database", name_error)
    try:
        version = str(get_version() or "")
        discovered = [str(name) for name in (list_databases() or []) if name]
    except Exception as exc:
        return _stop(report, "target_identity", f"MongoDB identity could not be read ({exc})")
    report.target_version = version
    report.target_database = wanted
    report.target_server_id = wanted
    dest_exists = wanted in discovered or wanted.casefold() in {name.casefold() for name in discovered}
    report.dest_will_create = not dest_exists
    if dest_exists:
        report.checks.append(Check("target_identity", "pass", f"MongoDB will receive the copy in existing database {wanted}."))
    else:
        report.checks.append(
            Check(
                "target_identity",
                "warning",
                f"MongoDB database {wanted} is not on this server yet. Start will create it when the first "
                f"collection is written from {report.source_database}. Users, roles, views, and indexes are not copied.",
            )
        )

    try:
        available = [str(row.get("table_name")) for row in source_query(PUBLIC_TABLES_SQL)]
    except Exception as exc:
        return _stop(report, "tables", f"Source tables could not be read ({exc})")
    chosen = selected_tables or available
    missing = [name for name in chosen if name not in available]
    if missing:
        return _stop(report, "tables", "Selected tables are not public tables: " + ", ".join(missing))
    if not chosen:
        return _stop(report, "tables", "The source has no public tables to migrate.")

    specs: list[CollectionSpec] = []
    for name in chosen:
        spec = load_collection(source_query, name)
        if spec.blocked:
            return _stop(report, "mapping", spec.blocked + " Nothing was copied.")
        specs.append(spec)
        report.conversions.extend(spec.conversions)
    report.table_specs = specs
    report.tables = [spec.name for spec in specs]

    existing: set[str] = set()
    if dest_exists:
        try:
            existing = {str(name) for name in (list_collections(wanted) or []) if name}
        except Exception as exc:
            return _stop(report, "target_tables", f"Target collections could not be read ({exc})")
    report.conflicts = [name for name in report.tables if name in existing]
    if scope == SCOPE_DATA:
        for name in report.tables:
            if name not in existing:
                return _stop(report, "data_only", f"Target collection {name} does not exist. Data-only migration does not create it.")
        report.checks.append(Check("conflicts", "pass", "Target collections exist. Documents will be inserted."))
    elif report.conflicts and strategy == STRATEGY_FAIL:
        return _stop(report, "conflicts", "Target already has collections: " + ", ".join(report.conflicts) + ". Nothing was dropped.")
    elif report.conflicts and strategy == STRATEGY_REPLACE and not confirm_replace:
        return _stop(report, "conflicts", "Replacing existing MongoDB collections requires explicit confirmation. Nothing was dropped.")
    elif report.conflicts and strategy == STRATEGY_SKIP:
        report.checks.append(Check("conflicts", "warning", "Existing MongoDB collections will be left unchanged: " + ", ".join(report.conflicts)))
    elif report.conflicts:
        report.checks.append(Check("conflicts", "warning", "Existing MongoDB collections will be dropped and rewritten because that was explicitly confirmed: " + ", ".join(report.conflicts)))
    else:
        report.checks.append(Check("conflicts", "pass", "The selected collections are not already on the MongoDB target."))

    report.unsupported = ["views, routines, triggers, users, indexes, and foreign keys are not copied"]
    report.checks.append(Check("unsupported", "warning", "These objects will not be migrated: " + "; ".join(report.unsupported) + "."))
    if report.conversions:
        report.checks.append(Check("conversions", "warning", "Document conversions: " + " ".join(report.conversions)))
    else:
        report.checks.append(Check("conversions", "pass", "No special document conversions were required."))

    readiness = inspect_backup_file(None)
    report.backup = readiness
    report.checks.append(Check("backup_file", "warning", readiness.file_summary))
    report.checks.append(Check("backup_format", "warning", readiness.format_summary))
    report.checks.append(Check("restore_verified", "warning", "Restore verification is false. A completed collection copy is not a tested MongoDB restore."))
    if scope != SCOPE_SCHEMA and not accept_unproven_backup:
        return _stop(report, "backup", "Restore has not been proven. Confirm that you accept this before copying data.")
    report.ready = True
    return report


def _stop(report: PgMongoPrecheck, code: str, summary: str) -> PgMongoPrecheck:
    text = redact_text(summary) or summary
    report.checks.append(Check(code, "fail", text))
    report.stop_reason = text
    report.ready = False
    return report
