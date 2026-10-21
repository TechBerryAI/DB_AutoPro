"""PostgreSQL-to-MySQL checks. Target SQL is MySQL only."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.backup_proof import BackupReadiness, inspect_backup_file
from backend.services.pg_migration.precheck import (
    SCOPE_DATA,
    SCOPE_SCHEMA,
    SCOPE_SCHEMA_AND_DATA,
    SCOPES,
    STRATEGY_FAIL,
    STRATEGY_REPLACE,
    STRATEGY_SKIP,
    PUBLIC_TABLES_SQL,
)
from backend.services.migration_framework.destination import destination_name_error, mysql_destination_exists
from backend.services.pg_mysql_migration.mapping import quote_mysql
from backend.services.pg_mysql_migration.schema import TableSpec, load_table, order_tables

TARGET_IDENTITY_SQL = (
    "SELECT VERSION() AS version, @@version_comment AS version_comment, "
    "DATABASE() AS database_name, @@server_uuid AS server_uuid"
)
TARGET_IDENTITY_FALLBACK_SQL = (
    "SELECT VERSION() AS version, @@version_comment AS version_comment, DATABASE() AS database_name"
)
SOURCE_IDENTITY_SQL = "SELECT version() AS version, current_database() AS database_name"


@dataclass
class Check:
    code: str
    status: str
    summary: str


@dataclass
class PgMysqlPrecheck:
    ready: bool
    checks: list[Check] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    table_specs: list[TableSpec] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    conversions: list[str] = field(default_factory=list)
    stop_reason: str = ""
    backup: BackupReadiness | None = None
    backup_proven: bool = False
    source_database: str = ""
    target_database: str = ""
    source_version: str = ""
    target_version: str = ""
    target_server_id: str = ""
    dest_will_create: bool = False


def assess_pg_mysql(
    source_query,
    target_query,
    *,
    scope: str,
    selected_tables: list[str] | None,
    strategy: str,
    confirm_replace: bool,
    accept_unproven_backup: bool,
    backup: BackupReadiness | None = None,
    batch_size: int,
    statement_timeout_ms: int,
    expected_database: str | None = None,
) -> PgMysqlPrecheck:
    """Read PostgreSQL and MySQL. A PostgreSQL dump is not treated as a MySQL restore."""
    report = PgMysqlPrecheck(ready=False)
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

    target_rows, target_error = _target_identity(target_query)
    if target_error:
        return _stop(report, "target_identity", target_error)
    target_version = str(target_rows.get("version") or "")
    comment = str(target_rows.get("version_comment") or "")
    if "postgresql" in target_version.lower() or "mysql" not in comment.lower() and "mariadb" not in comment.lower():
        return _stop(report, "target_type", "The target connection is not MySQL or MariaDB. No schema or data was copied.")
    report.target_version = target_version
    observed_database = str(target_rows.get("database_name") or "")
    wanted = (expected_database or "").strip()
    name_error = destination_name_error(wanted, "mysql") if wanted else None
    if name_error:
        return _stop(report, "target_database", name_error)
    if wanted:
        report.target_database = wanted
        if observed_database and observed_database != wanted:
            return _stop(
                report,
                "target_database",
                f"The connected MySQL database is {observed_database}, not {wanted}. Nothing was copied.",
            )
        dest_exists = mysql_destination_exists(target_query, wanted)
        report.dest_will_create = not dest_exists
        if dest_exists:
            report.checks.append(Check("target_identity", "pass", f"MySQL server will receive the copy in existing database {wanted}."))
        elif scope == SCOPE_DATA:
            return _stop(report, "target_database", f"MySQL database {wanted} does not exist. Data-only migration does not create it.")
        else:
            report.checks.append(
                Check(
                    "target_identity",
                    "warning",
                    f"MySQL database {wanted} is not on this server yet. Start will create an empty database "
                    f"and copy public tables from {report.source_database}. Users, roles, files, views, "
                    "and routines are not copied.",
                )
            )
    else:
        report.target_database = observed_database
        if not report.target_database:
            return _stop(report, "target_database", "The MySQL connection has no database selected.")
        report.checks.append(Check("target_identity", "pass", f"MySQL database {report.target_database}."))
    report.target_server_id = str(target_rows.get("server_uuid") or report.target_database)

    try:
        grants = target_query("SHOW GRANTS FOR CURRENT_USER")
    except Exception as exc:
        return _stop(report, "privileges", f"Target privileges could not be read ({exc})")
    grant_text = " ".join(" ".join(str(value) for value in row.values()) for row in grants).upper()
    if "ALL PRIVILEGES" not in grant_text and not all(word in grant_text for word in ("CREATE", "INSERT", "SELECT", "DROP")):
        return _stop(report, "privileges", "The MySQL account needs CREATE, INSERT, SELECT, and DROP, or ALL PRIVILEGES.")
    report.checks.append(Check("privileges", "pass", "The MySQL account can create tables and insert rows."))

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
        spec = load_table(source_query, name)
        if spec.blocked:
            return _stop(report, "mapping", spec.blocked + " Nothing was copied.")
        specs.append(spec)
        report.conversions.extend(spec.conversions)
    ordered, cycle = order_tables(specs)
    if cycle:
        return _stop(report, "foreign_keys", cycle + " Nothing was copied.")
    report.table_specs = ordered
    report.tables = [spec.name for spec in ordered]

    try:
        existing = {
            str(row.get("table_name") or row.get("TABLE_NAME"))
            for row in target_query(
                "SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = {_sql_string(report.target_database)} AND table_type = 'BASE TABLE'"
            )
        }
    except Exception as exc:
        return _stop(report, "target_tables", f"Target tables could not be read ({exc})")
    report.conflicts = [name for name in report.tables if name in existing]
    if scope == SCOPE_DATA:
        for name in report.tables:
            if name not in existing:
                return _stop(report, "data_only", f"Target table {name} does not exist. Data-only migration does not create it.")
            count = _target_count(target_query, name)
            if count is None:
                return _stop(report, "data_only", f"Row count for target table {name} could not be read.")
            if count > 0:
                return _stop(report, "data_only", f"Target table {name} already contains rows. Those rows were not deleted.")
        report.checks.append(Check("conflicts", "pass", "Target tables exist and are empty. Rows will be appended."))
    elif report.conflicts and strategy == STRATEGY_FAIL:
        return _stop(report, "conflicts", "Target already has tables: " + ", ".join(report.conflicts) + ". Nothing was dropped.")
    elif report.conflicts and strategy == STRATEGY_REPLACE and not confirm_replace:
        return _stop(report, "conflicts", "Replacing existing MySQL tables requires explicit confirmation. Nothing was dropped.")
    elif report.conflicts and strategy == STRATEGY_SKIP:
        report.checks.append(Check("conflicts", "warning", "Existing MySQL tables will be left unchanged: " + ", ".join(report.conflicts)))
    elif report.conflicts:
        report.checks.append(Check("conflicts", "warning", "Existing MySQL tables will be dropped and recreated because that was explicitly confirmed: " + ", ".join(report.conflicts)))
    else:
        report.checks.append(Check("conflicts", "pass", "The selected tables are not already on the MySQL target."))

    found, inventory_errors = _inventory(source_query)
    report.unsupported.extend(found)
    report.unsupported.extend(f"schema {name}" for name in _other_schemas(source_query))
    report.unsupported.extend(inventory_errors)
    if report.unsupported:
        report.checks.append(Check("unsupported", "warning", "These objects will not be migrated: " + ", ".join(report.unsupported)))
    else:
        report.checks.append(Check(
            "unsupported",
            "pass",
            "No views, materialized views, foreign tables, partitioned tables, sequences, routines, "
            "triggers, policies, enums, domains, composite types, extensions, or non-public schemas were found.",
        ))
    if report.conversions:
        report.checks.append(Check("conversions", "warning", "Lossy or engine-specific conversions: " + " ".join(report.conversions)))
    else:
        report.checks.append(Check("conversions", "pass", "No lossy type conversions were required for the selected columns."))

    readiness = backup if backup is not None else inspect_backup_file(None)
    report.backup = readiness
    report.backup_proven = False
    report.checks.append(Check("backup_file", "pass" if readiness.file_exists else "warning", readiness.file_summary))
    report.checks.append(Check("backup_format", "pass" if readiness.format_checked else "warning", readiness.format_summary))
    report.checks.append(Check(
        "restore_verified",
        "warning",
        "Restore verification is false. A backup file and a recognized dump header are not a completed restore. "
        "A PostgreSQL backup has not been restored into this MySQL target.",
    ))
    if scope != SCOPE_SCHEMA and not accept_unproven_backup:
        return _stop(report, "backup", "Restore has not been proven. Confirm that you accept this before copying data.")
    if statement_timeout_ms:
        report.checks.append(Check("statement_timeout", "pass", f"MySQL max_execution_time will be set to {statement_timeout_ms} ms for this session."))
    report.ready = True
    return report


def _target_identity(target_query) -> tuple[dict, str]:
    try:
        rows = target_query(TARGET_IDENTITY_SQL)
    except Exception:
        try:
            rows = target_query(TARGET_IDENTITY_FALLBACK_SQL)
        except Exception as exc:
            return {}, f"Target identity could not be read ({exc})"
    if not rows:
        return {}, "Target identity could not be read."
    return rows[0], ""


def _target_count(target_query, table: str) -> int | None:
    try:
        rows = target_query(f"SELECT COUNT(*) AS row_count FROM {quote_mysql(table)}")
    except Exception:
        return None
    if not rows:
        return None
    value = rows[0].get("row_count")
    if value is None:
        value = rows[0].get("COUNT(*)")
    return int(value)


_RELKINDS = {
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
    "p": "partitioned table",
    "S": "sequence",
}
_ROUTINES = {"f": "function", "p": "procedure", "a": "aggregate", "w": "window function"}
_TYPES = {"e": "enum", "d": "domain", "c": "composite type"}


def _inventory(source_query) -> tuple[list[str], list[str]]:
    """List PostgreSQL objects the MySQL migration does not copy."""
    found: list[str] = []
    errors: list[str] = []

    def read(sql: str, label: str):
        try:
            return source_query(sql)
        except Exception as exc:
            errors.append(f"{label} could not be listed ({redact_text(str(exc)) or 'catalog query failed'})")
            return []

    for row in read(
        "-- db_autopro_inventory\n"
        "SELECT c.relkind AS object_kind, c.relname AS object_name FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm', 'f', 'p', 'S') ORDER BY 2",
        "relations",
    ):
        kind = _RELKINDS.get(str(row.get("object_kind")), "relation")
        name = row.get("object_name")
        if name:
            found.append(f"{kind} {name}")
    for row in read(
        "-- db_autopro_inventory\n"
        "SELECT p.proname AS object_name, p.prokind AS object_kind FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' ORDER BY 1",
        "routines",
    ):
        kind = _ROUTINES.get(str(row.get("object_kind")), "routine")
        name = row.get("object_name")
        if name:
            found.append(f"{kind} {name}")
    for row in read(
        "-- db_autopro_inventory\n"
        "SELECT t.tgname AS object_name, c.relname AS table_name FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND NOT t.tgisinternal ORDER BY 1",
        "triggers",
    ):
        if row.get("object_name"):
            found.append(f"trigger {row['object_name']} on {row.get('table_name')}")
    for row in read(
        "-- db_autopro_inventory\n"
        "SELECT pol.polname AS object_name, c.relname AS table_name FROM pg_policy pol "
        "JOIN pg_class c ON c.oid = pol.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY 1",
        "policies",
    ):
        if row.get("object_name"):
            found.append(f"policy {row['object_name']} on {row.get('table_name')}")
    for row in read(
        "-- db_autopro_inventory\n"
        "SELECT t.typname AS object_name, t.typtype AS object_kind FROM pg_type t "
        "JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd', 'c') ORDER BY 1",
        "types",
    ):
        kind = _TYPES.get(str(row.get("object_kind")), "type")
        name = row.get("object_name")
        if name:
            found.append(f"{kind} {name}")
    for row in read(
        "-- db_autopro_inventory\n"
        "SELECT extname AS object_name FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1",
        "extensions",
    ):
        if row.get("object_name"):
            found.append(f"extension {row['object_name']}")
    return found, errors


def _names(source_query, relkind: str, key: str) -> list[str]:
    try:
        rows = source_query(
            "SELECT c.relname AS relation_name FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = 'public' AND c.relkind = '{relkind}' ORDER BY 1"
        )
    except Exception:
        return []
    return [str(row.get(key)) for row in rows if row.get(key)]


def _other_schemas(source_query) -> list[str]:
    try:
        rows = source_query(
            "SELECT n.nspname AS schema_name FROM pg_namespace n "
            "WHERE n.nspname NOT IN ('public', 'pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_temp%' AND n.nspname NOT LIKE 'pg_toast%' "
            "AND EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid)"
        )
    except Exception:
        return []
    return [str(row.get("schema_name")) for row in rows if row.get("schema_name")]


def _sql_string(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _stop(report: PgMysqlPrecheck, code: str, summary: str) -> PgMysqlPrecheck:
    text = redact_text(summary) or summary
    report.checks.append(Check(code, "fail", text))
    report.stop_reason = text
    report.ready = False
    return report
