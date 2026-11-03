"""PostgreSQL-to-SQL Server or Oracle checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.migration_framework.destination import destination_name_error
from backend.services.migration_framework.registry import MSSQL, ORACLE, _norm
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
from backend.services.pg_sql_migration.mapping import quote_ident
from backend.services.pg_sql_migration.schema import TableSpec, load_table

SOURCE_IDENTITY_SQL = "SELECT version() AS version, current_database() AS database_name"


@dataclass
class Check:
    code: str
    status: str
    summary: str


@dataclass
class PgSqlPrecheck:
    ready: bool
    checks: list[Check] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    table_specs: list[TableSpec] = field(default_factory=list)
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
    target_kind: str = ""
    engine: str = ""


def assess_pg_sql(
    source_query,
    target_query,
    *,
    target_kind: str,
    scope: str,
    selected_tables: list[str] | None,
    strategy: str,
    confirm_replace: bool,
    accept_unproven_backup: bool,
    batch_size: int,
    statement_timeout_ms: int,
    expected_database: str | None = None,
) -> PgSqlPrecheck:
    kind = _norm(target_kind)
    label = "SQL Server" if kind == MSSQL else "Oracle"
    report = PgSqlPrecheck(ready=False, target_kind=kind, engine=f"postgresql_to_{kind}")
    if kind not in {MSSQL, ORACLE}:
        return _stop(report, "target_type", f"{target_kind} is not a supported SQL target.")
    if scope not in SCOPES:
        return _stop(report, "scope", f"Migration scope {scope} is not supported.")
    if strategy not in {STRATEGY_SKIP, STRATEGY_FAIL, STRATEGY_REPLACE}:
        return _stop(report, "strategy", f"Conflict strategy {strategy} is not supported.")
    if batch_size < 1:
        return _stop(report, "batch_size", "Batch size must be at least 1.")
    if statement_timeout_ms < 0:
        return _stop(report, "statement_timeout", "Statement timeout cannot be negative.")
    if scope == SCOPE_DATA and strategy == STRATEGY_REPLACE:
        return _stop(report, "strategy", "Data-only migration does not replace target tables.")
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

    identity_error = _target_identity(kind, target_query, expected_database, report, label)
    if identity_error:
        return _stop(report, "target_identity", identity_error)

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

    specs: list[TableSpec] = []
    for name in chosen:
        spec = load_table(kind, source_query, name)
        if spec.blocked:
            return _stop(report, "mapping", spec.blocked + " Nothing was copied.")
        specs.append(spec)
        report.conversions.extend(spec.conversions)
    report.table_specs = specs
    report.tables = [spec.name for spec in specs]

    existing = _existing_tables(kind, target_query, report)
    if existing is None and not report.dest_will_create:
        return report
    report.conflicts = [name for name in report.tables if name in (existing or set())]
    if scope == SCOPE_DATA:
        for name in report.tables:
            if name not in (existing or set()):
                return _stop(report, "data_only", f"Target table {name} does not exist. Data-only migration does not create it.")
        report.checks.append(Check("conflicts", "pass", f"Target {label} tables exist. Rows will be inserted."))
    elif report.conflicts and strategy == STRATEGY_FAIL:
        return _stop(report, "conflicts", f"Target already has tables: {', '.join(report.conflicts)}. Nothing was dropped.")
    elif report.conflicts and strategy == STRATEGY_REPLACE and not confirm_replace:
        return _stop(report, "conflicts", f"Replacing existing {label} tables requires explicit confirmation. Nothing was dropped.")
    elif report.conflicts and strategy == STRATEGY_SKIP:
        report.checks.append(Check("conflicts", "warning", f"Existing {label} tables will be left unchanged: " + ", ".join(report.conflicts)))
    elif report.conflicts:
        report.checks.append(Check("conflicts", "warning", f"Existing {label} tables will be dropped and recreated because that was explicitly confirmed: " + ", ".join(report.conflicts)))
    else:
        report.checks.append(Check("conflicts", "pass", f"The selected tables are not already on the {label} target."))

    report.unsupported = [
        "views, routines, triggers, users, foreign keys, and secondary indexes are not copied",
    ]
    report.checks.append(Check("unsupported", "warning", "These objects will not be migrated: " + "; ".join(report.unsupported) + "."))
    if report.conversions:
        report.checks.append(Check("conversions", "warning", "Lossy or engine-specific conversions: " + " ".join(report.conversions)))
    else:
        report.checks.append(Check("conversions", "pass", "No lossy type conversions were required for the selected columns."))

    readiness = inspect_backup_file(None)
    report.backup = readiness
    report.checks.append(Check("backup_file", "warning", readiness.file_summary))
    report.checks.append(Check("backup_format", "warning", readiness.format_summary))
    report.checks.append(Check("restore_verified", "warning", f"Restore verification is false. A completed table copy is not a tested {label} restore."))
    if scope != SCOPE_SCHEMA and not accept_unproven_backup:
        return _stop(report, "backup", "Restore has not been proven. Confirm that you accept this before copying data.")
    report.ready = True
    return report


def _target_identity(kind: str, target_query, expected_database: str | None, report: PgSqlPrecheck, label: str) -> str:
    wanted = (expected_database or "").strip()
    name_error = destination_name_error(wanted, kind) if wanted else None
    if name_error:
        return name_error
    try:
        if kind == MSSQL:
            rows = target_query("SELECT @@VERSION AS version, DB_NAME() AS database_name")
        else:
            rows = target_query(
                "SELECT banner AS version, sys_context('USERENV','CURRENT_SCHEMA') AS database_name "
                "FROM v$version WHERE ROWNUM = 1"
            )
    except Exception as exc:
        return f"Target identity could not be read ({exc})"
    row = (rows or [{}])[0]
    version = str(row.get("version") or row.get("VERSION") or row.get("banner") or "")
    observed = str(row.get("database_name") or row.get("DATABASE_NAME") or "")
    if kind == MSSQL and "microsoft sql server" not in version.lower() and "sql server" not in version.lower() and "azure sql" not in version.lower():
        if not version:
            return "The target connection is not SQL Server. Nothing was copied."
    if kind == ORACLE and version and "oracle" not in version.lower() and "banner" not in str(row).lower():
        pass
    report.target_version = version
    dest_exists = bool(wanted) and (wanted.casefold() == observed.casefold() or _database_exists(kind, target_query, wanted))
    report.target_database = wanted or observed
    report.target_server_id = report.target_database
    if kind == ORACLE:
        report.dest_will_create = False
        if not report.target_database:
            return "Enter the Oracle service name. This migration does not create a PDB or service."
        report.checks.append(
            Check(
                "target_identity",
                "pass",
                f"Oracle service {report.target_database} will receive public tables in the connected schema "
                f"{observed or '(current)'}. Users, roles, and packages are not copied.",
            )
        )
        return ""
    if dest_exists:
        report.dest_will_create = False
        report.checks.append(Check("target_identity", "pass", f"SQL Server will receive the copy in existing database {report.target_database}."))
        return ""
    if not wanted:
        return "Enter the SQL Server destination database name."
    report.dest_will_create = True
    report.checks.append(
        Check(
            "target_identity",
            "warning",
            f"SQL Server database {wanted} is not on this server yet. Start will create it and copy "
            f"public tables from {report.source_database}. Users, roles, files, views, and routines are not copied.",
        )
    )
    return ""


def _database_exists(kind: str, target_query, name: str) -> bool:
    escaped = name.replace("'", "''")
    try:
        if kind == MSSQL:
            rows = target_query(f"SELECT name FROM sys.databases WHERE name = '{escaped}'")
        else:
            rows = target_query(
                "SELECT sys_context('USERENV','SERVICE_NAME') AS name FROM dual"
            )
            return any(str(row.get("name") or "").casefold() == name.casefold() for row in rows or [])
    except Exception:
        return False
    return bool(rows)


def _existing_tables(kind: str, target_query, report: PgSqlPrecheck) -> set[str] | None:
    if report.dest_will_create:
        return set()
    try:
        if kind == MSSQL:
            catalog = f"{quote_ident(MSSQL, report.target_database)}." if report.target_database else ""
            rows = target_query(
                f"SELECT TABLE_NAME AS table_name FROM {catalog}INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = 'dbo'"
            )
        else:
            rows = target_query("SELECT table_name FROM user_tables")
    except Exception as exc:
        _stop(report, "target_tables", f"Target tables could not be read ({exc})")
        return None
    names = set()
    for row in rows or []:
        value = str(row.get("table_name") or row.get("TABLE_NAME") or "")
        if value:
            names.add(value)
            names.add(value.lower())
    return names


def _stop(report: PgSqlPrecheck, code: str, summary: str) -> PgSqlPrecheck:
    text = redact_text(summary) or summary
    report.checks.append(Check(code, "fail", text))
    report.stop_reason = text
    report.ready = False
    return report
