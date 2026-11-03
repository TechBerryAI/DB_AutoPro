"""Checks for copying another engine into PostgreSQL. Target SQL is PostgreSQL only."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.backup_proof import BackupReadiness, inspect_backup_file
from backend.services.pg_migration.precheck import (
    SCOPE_DATA,
    SCOPE_SCHEMA,
    SCOPES,
    STRATEGY_FAIL,
    STRATEGY_REPLACE,
    STRATEGY_SKIP,
)
from backend.services.migration_framework.destination import destination_name_error
from backend.services.to_pg_migration.schema import TableSpec, list_source_tables, load_relational_table, mongo_spec


@dataclass
class Check:
    code: str
    status: str
    summary: str


@dataclass
class ToPgPrecheck:
    ready: bool
    checks: list[Check] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    table_specs: list[TableSpec] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    conversions: list[str] = field(default_factory=list)
    stop_reason: str = ""
    backup: BackupReadiness | None = None
    source_kind: str = ""
    source_database: str = ""
    target_database: str = ""
    source_version: str = ""
    target_version: str = ""
    target_server_id: str = ""
    dest_will_create: bool = False
    engine: str = ""


def assess_to_pg(
    source_query,
    target_query,
    *,
    source_kind: str,
    scope: str,
    selected_tables: list[str] | None,
    strategy: str,
    confirm_replace: bool,
    accept_unproven_backup: bool,
    batch_size: int,
    statement_timeout_ms: int,
    expected_database: str | None = None,
    mongo_collections: list[str] | None = None,
    source_database: str = "",
) -> ToPgPrecheck:
    from backend.services.migration_framework.registry import _norm

    source_kind = _norm(source_kind)
    report = ToPgPrecheck(ready=False, source_kind=source_kind, engine=f"{source_kind}_to_postgresql")
    if scope not in SCOPES:
        return _stop(report, "scope", f"Migration scope {scope} is not supported.")
    if strategy not in {STRATEGY_SKIP, STRATEGY_FAIL, STRATEGY_REPLACE}:
        return _stop(report, "strategy", f"Conflict strategy {strategy} is not supported.")
    if batch_size < 1:
        return _stop(report, "batch_size", "Batch size must be at least 1.")
    if statement_timeout_ms < 0:
        return _stop(report, "statement_timeout", "Statement timeout cannot be negative.")
    report.checks.append(Check("scope", "pass", f"Scope is {scope}."))

    source_db, source_version, source_error = _source_identity(source_kind, source_query, source_database)
    if source_error:
        return _stop(report, "source_identity", source_error)
    report.source_database = source_db
    report.source_version = source_version
    label = "MongoDB database" if source_kind == "mongodb" else f"{source_kind} database"
    report.checks.append(Check("source_identity", "pass", f"{label} {source_db or '(current)'}."))

    try:
        target_rows = target_query("SELECT version() AS version, current_database() AS database_name")
    except Exception as exc:
        return _stop(report, "target_identity", f"Target identity could not be read ({exc})")
    version = str((target_rows or [{}])[0].get("version") or "")
    if "postgresql" not in version.lower():
        return _stop(report, "target_type", "The target connection is not PostgreSQL. Nothing was copied.")
    observed = str((target_rows or [{}])[0].get("database_name") or "")
    wanted = (expected_database or "").strip() or observed
    name_error = destination_name_error(wanted, "postgresql") if wanted else None
    if name_error:
        return _stop(report, "target_database", name_error)
    report.target_database = wanted
    report.target_version = version
    report.target_server_id = wanted
    dest_exists = wanted == observed or _pg_database_exists(target_query, wanted)
    report.dest_will_create = not dest_exists
    if dest_exists:
        report.checks.append(Check("target_identity", "pass", f"PostgreSQL server will receive the copy in existing database {wanted}."))
    else:
        report.checks.append(
            Check(
                "target_identity",
                "warning",
                f"PostgreSQL database {wanted} is not on this server yet. Start will create it and copy "
                f"{'collections' if source_kind == 'mongodb' else 'tables'} from {source_db or source_kind}. "
                "Users, roles, files, views, and routines are not copied.",
            )
        )

    if source_kind == "mongodb":
        chosen = selected_tables or list(mongo_collections or [])
        if not chosen:
            return _stop(report, "tables", "The MongoDB database has no collections to migrate.")
        specs = [mongo_spec(name) for name in chosen]
    else:
        try:
            available = list_source_tables(source_kind, source_query)
        except Exception as exc:
            return _stop(report, "tables", f"Source tables could not be read ({exc})")
        chosen = selected_tables or available
        missing = [name for name in chosen if name not in available]
        if missing:
            return _stop(report, "tables", "Selected tables are not on the source: " + ", ".join(missing))
        if not chosen:
            return _stop(report, "tables", "The source has no tables to migrate.")
        specs = []
        for name in chosen:
            spec = load_relational_table(source_kind, source_query, name)
            if spec.blocked:
                return _stop(report, "mapping", spec.blocked + " Nothing was copied.")
            specs.append(spec)
    report.table_specs = specs
    report.tables = [spec.name for spec in specs]
    for spec in specs:
        report.conversions.extend(spec.conversions)

    try:
        existing = {
            str(row.get("table_name") or row.get("tablename") or "")
            for row in target_query(
                "SELECT tablename AS table_name FROM pg_tables WHERE schemaname = 'public'"
            )
        } if dest_exists else set()
    except Exception as exc:
        if dest_exists:
            return _stop(report, "target_tables", f"Target tables could not be read ({exc})")
        existing = set()
    report.conflicts = [name for name in report.tables if name in existing]
    if report.conflicts and strategy == STRATEGY_FAIL:
        return _stop(report, "conflicts", "Target already has tables: " + ", ".join(report.conflicts) + ". Nothing was dropped.")
    if report.conflicts and strategy == STRATEGY_REPLACE and not confirm_replace:
        return _stop(report, "conflicts", "Replacing existing PostgreSQL tables requires explicit confirmation. Nothing was dropped.")
    if report.conflicts and strategy == STRATEGY_SKIP:
        report.checks.append(Check("conflicts", "warning", "Existing PostgreSQL tables will be left unchanged: " + ", ".join(report.conflicts)))
    elif report.conflicts:
        report.checks.append(Check("conflicts", "warning", "Existing PostgreSQL tables will be dropped and recreated because that was explicitly confirmed: " + ", ".join(report.conflicts)))
    else:
        report.checks.append(Check("conflicts", "pass", "The selected objects are not already on the PostgreSQL target."))

    report.unsupported = [
        "views, routines, triggers, users, and non-default schemas are not copied",
    ]
    if source_kind != "mongodb":
        report.unsupported.append("foreign keys and secondary indexes are not copied")
    report.checks.append(Check("unsupported", "warning", "These objects will not be migrated: " + "; ".join(report.unsupported) + "."))
    if report.conversions:
        report.checks.append(Check("conversions", "warning", "Lossy or engine-specific conversions: " + " ".join(report.conversions)))
    else:
        report.checks.append(Check("conversions", "pass", "No lossy type conversions were required for the selected columns."))

    readiness = inspect_backup_file(None)
    report.backup = readiness
    report.checks.append(Check("backup_file", "warning", readiness.file_summary))
    report.checks.append(Check("backup_format", "warning", readiness.format_summary))
    report.checks.append(Check("restore_verified", "warning", "Restore verification is false. A completed table copy is not a tested PostgreSQL restore."))
    if scope != SCOPE_SCHEMA and not accept_unproven_backup:
        return _stop(report, "backup", "Restore has not been proven. Confirm that you accept this before copying data.")
    if scope == SCOPE_DATA and strategy == STRATEGY_REPLACE:
        return _stop(report, "strategy", "Data-only migration does not replace target tables.")
    report.ready = True
    return report


def _source_identity(kind: str, source_query, fallback: str = "") -> tuple[str, str, str]:
    if kind == "mongodb":
        return fallback, "", ""
    try:
        if kind == "mysql":
            rows = source_query("SELECT DATABASE() AS database_name, VERSION() AS version")
        elif kind == "mssql":
            rows = source_query("SELECT DB_NAME() AS database_name, @@VERSION AS version")
        elif kind == "oracle":
            rows = source_query("SELECT sys_context('USERENV','CURRENT_SCHEMA') AS database_name, BANNER AS version FROM v$version WHERE ROWNUM = 1")
        else:
            rows = [{}]
    except Exception as exc:
        return "", "", f"Source identity could not be read ({exc})"
    row = (rows or [{}])[0]
    return str(row.get("database_name") or row.get("DATABASE_NAME") or ""), str(row.get("version") or row.get("VERSION") or row.get("banner") or ""), ""


def _pg_database_exists(target_query, name: str) -> bool:
    try:
        rows = target_query(
            "SELECT 1 AS ok FROM pg_database WHERE datname = '" + str(name).replace("'", "''") + "'"
        )
    except Exception:
        return False
    return bool(rows)


def _stop(report: ToPgPrecheck, code: str, summary: str) -> ToPgPrecheck:
    text = redact_text(summary) or summary
    report.checks.append(Check(code, "fail", text))
    report.stop_reason = text
    report.ready = False
    return report
