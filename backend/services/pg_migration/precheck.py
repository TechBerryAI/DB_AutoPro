"""PostgreSQL-to-PostgreSQL migration checks. No SQL is executed here."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.backup_proof import BackupReadiness, inspect_backup_file
from backend.services.postgres_patch.identity import DatabaseIdentityError, read_database_identity

SCOPE_SCHEMA_AND_DATA = "schema_and_data"
SCOPE_SCHEMA = "schema_only"
SCOPE_DATA = "data_only"
SCOPES = {SCOPE_SCHEMA_AND_DATA, SCOPE_SCHEMA, SCOPE_DATA}

STRATEGY_SKIP = "skip_existing"
STRATEGY_FAIL = "fail_on_conflict"
STRATEGY_REPLACE = "replace_existing"


@dataclass
class MigrationCheck:
    code: str
    status: str
    summary: str


@dataclass
class MigrationPrecheck:
    ready: bool
    checks: list[MigrationCheck] = field(default_factory=list)
    source_identity: object | None = None
    target_identity: object | None = None
    tables: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    estimated_rows: dict[str, int] = field(default_factory=dict)
    stop_reason: str = ""
    backup_proven: bool = False
    backup: BackupReadiness | None = None


def assess_migration(
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
) -> MigrationPrecheck:
    """Read both servers and refuse an unsafe plan. Queries are read-only."""
    report = MigrationPrecheck(ready=False)
    if scope not in SCOPES:
        return _stop(report, "scope", f"Migration scope {scope} is not supported.")
    if strategy not in {STRATEGY_SKIP, STRATEGY_FAIL, STRATEGY_REPLACE}:
        return _stop(report, "strategy", f"Conflict strategy {strategy} is not supported.")
    if batch_size < 1:
        return _stop(report, "batch_size", "Batch size must be at least 1.")
    if statement_timeout_ms < 0:
        return _stop(report, "statement_timeout", "Statement timeout cannot be negative.")
    report.checks.append(MigrationCheck("scope", "pass", f"Scope is {scope}."))

    try:
        source = read_database_identity(source_query)
        target = read_database_identity(target_query)
    except DatabaseIdentityError as exc:
        return _stop(report, "identity", str(exc))
    report.source_identity = source
    report.target_identity = target
    report.checks.append(MigrationCheck("source_identity", "pass", source.summary()))
    report.checks.append(MigrationCheck("target_identity", "pass", target.summary()))
    if source.system_identifier == target.system_identifier and source.database_oid == target.database_oid:
        return _stop(report, "same_database", "Source and target are the same database. The migration was not started.")

    source_version = _version(source_query)
    target_version = _version(target_query)
    if source_version is None or target_version is None:
        return _stop(report, "version", "PostgreSQL version could not be confirmed on both servers.")
    report.checks.append(
        MigrationCheck("version", "pass", f"Source {source_version} and target {target_version} were read.")
    )
    if target_version < source_version:
        return _stop(
            report,
            "version",
            f"Target PostgreSQL {target_version} is older than source {source_version}.",
        )

    if not _can_create(target_query):
        return _stop(report, "privileges", "The target role cannot create objects in schema public.")
    report.checks.append(MigrationCheck("privileges", "pass", "The target role can create objects in schema public."))

    try:
        missing_extensions = sorted(set(_extensions(source_query)) - set(_extensions(target_query)) - {"plpgsql"})
    except DatabaseIdentityError as exc:
        return _stop(report, "extensions", str(exc))
    if missing_extensions:
        report.unsupported.extend(f"extension {name}" for name in missing_extensions)
        return _stop(
            report,
            "extensions",
            "The target is missing extensions: " + ", ".join(missing_extensions) + ". They were not installed.",
        )
    report.checks.append(MigrationCheck("extensions", "pass", "No extra source extensions are missing on the target."))

    source_tables = _tables(source_query)
    chosen = [name for name in (selected_tables or source_tables) if name]
    unknown = [name for name in chosen if name not in source_tables]
    if unknown:
        return _stop(report, "tables", "These tables are not in the source public schema: " + ", ".join(unknown))
    if not chosen:
        return _stop(report, "tables", "No public tables were selected.")
    report.tables = chosen
    target_tables = set(_tables(target_query))
    report.conflicts = [name for name in chosen if name in target_tables]
    if scope == SCOPE_DATA:
        missing = [name for name in chosen if name not in target_tables]
        if missing:
            return _stop(report, "tables", "Data-only migration requires these target tables to already exist: " + ", ".join(missing))
        if strategy == STRATEGY_REPLACE:
            return _stop(report, "strategy", "Data-only migration does not replace tables. Choose schema and data for a confirmed replacement.")
        for name in chosen:
            rows = target_query(f"SELECT count(*) AS row_count FROM public.{quote_ident(name)}")
            if int(rows[0].get("row_count") or 0) > 0:
                return _stop(
                    report,
                    "conflicts",
                    f"Target table {name} already contains rows. They were not deleted.",
                )
    if report.conflicts and strategy == STRATEGY_FAIL and scope != SCOPE_DATA:
        return _stop(report, "conflicts", "The target already has: " + ", ".join(report.conflicts))
    if report.conflicts and strategy == STRATEGY_REPLACE and not confirm_replace:
        return _stop(
            report,
            "conflicts",
            "Replacing existing target tables requires explicit confirmation. Nothing was dropped.",
        )
    if report.conflicts and strategy == STRATEGY_SKIP and scope == SCOPE_DATA:
        report.checks.append(
            MigrationCheck("conflicts", "warning", "The target tables exist and are empty. Rows will be appended, not used to replace a schema.")
        )
    elif report.conflicts and strategy == STRATEGY_SKIP:
        report.checks.append(
            MigrationCheck("conflicts", "warning", "Existing target tables will be left unchanged: " + ", ".join(report.conflicts))
        )
    elif report.conflicts:
        report.checks.append(
            MigrationCheck(
                "conflicts",
                "warning",
                "Existing target tables will be replaced because that was explicitly confirmed: " + ", ".join(report.conflicts),
            )
        )
    else:
        report.checks.append(MigrationCheck("conflicts", "pass", "The selected tables are not already on the target."))

    report.unsupported.extend(f"view {name}" for name in _views(source_query))
    report.unsupported.extend(f"materialized view {name}" for name in _relations(source_query, "m", "relation_name"))
    view_names = [item.removeprefix("view ") for item in report.unsupported if item.startswith("view ")]
    matview_names = [item.removeprefix("materialized view ") for item in report.unsupported if item.startswith("materialized view ")]
    if view_names:
        report.checks.append(MigrationCheck(
            "views",
            "unsupported",
            "Views were not migrated. They can depend on tables, functions, search_path, and privileges: " + ", ".join(view_names),
        ))
    else:
        report.checks.append(MigrationCheck("views", "pass", "No public views were found."))
    if matview_names:
        report.checks.append(MigrationCheck(
            "materialized_views",
            "unsupported",
            "Materialized views were not migrated. They need a refresh, underlying relations, and the privileges of their owner: " + ", ".join(matview_names),
        ))
    else:
        report.checks.append(MigrationCheck("materialized_views", "pass", "No public materialized views were found."))
    other_schemas = _non_public_schemas(source_query)
    if other_schemas:
        report.unsupported.extend(f"schema {name}" for name in other_schemas)
        report.checks.append(MigrationCheck(
            "schemas",
            "unsupported",
            "Non-public schemas were not migrated. Ownership, CREATE privilege, schema-qualified names, and dependency order are not handled: " + ", ".join(other_schemas),
        ))
    else:
        report.checks.append(MigrationCheck("schemas", "pass", "No non-public user schemas with relations were found."))
    for item in _sequence_links(source_query):
        if item["table_name"] not in chosen:
            report.unsupported.append(f"sequence {item['sequence_name']}")
    for item in _secondary_indexes(source_query):
        if item["table_name"] not in chosen:
            report.unsupported.append(f"index {item['index_name']}")
    if report.unsupported:
        report.checks.append(
            MigrationCheck("unsupported", "warning", "Unsupported objects will be reported and not migrated: " + ", ".join(report.unsupported))
        )
    report.estimated_rows = _estimates(source_query, chosen)
    report.checks.append(
        MigrationCheck(
            "volume",
            "pass",
            "Row counts below are planner estimates, not exact counts: "
            + ", ".join(f"{name}~{report.estimated_rows.get(name, 0)}" for name in chosen),
        )
    )
    report.checks.append(
        MigrationCheck(
            "storage",
            "not_applicable",
            "Free disk space is not available from the database session, so no free-space figure is reported.",
        )
    )
    readiness = backup if backup is not None else inspect_backup_file(None)
    report.backup = readiness
    report.checks.append(MigrationCheck("backup_file", "pass" if readiness.file_exists else "warning", readiness.file_summary))
    report.checks.append(MigrationCheck("backup_format", "pass" if readiness.format_checked else "warning", readiness.format_summary))
    if readiness.restore_verified:
        report.backup_proven = True
        report.checks.append(MigrationCheck("restore_verified", "pass", readiness.restore_summary))
    else:
        report.backup_proven = False
        report.checks.append(MigrationCheck("restore_not_proven", "warning", readiness.restore_summary))
        if scope != SCOPE_SCHEMA and not accept_unproven_backup:
            return _stop(
                report,
                "backup",
                "Restore has not been proven. Confirm that you accept this before copying data.",
            )
    report.ready = True
    return report


def quote_ident(name: str) -> str:
    if not name or "\x00" in name:
        raise ValueError("Invalid SQL identifier")
    return '"' + name.replace('"', '""') + '"'


def _stop(report: MigrationPrecheck, code: str, summary: str) -> MigrationPrecheck:
    report.checks.append(MigrationCheck(code, "fail", redact_text(summary) or summary))
    report.stop_reason = report.checks[-1].summary
    report.ready = False
    return report


def _version(query) -> int | None:
    try:
        rows = query("SELECT current_setting('server_version_num') AS version_num")
    except Exception:
        return None
    if not rows:
        return None
    try:
        return int(rows[0].get("version_num") or rows[0].get("VERSION_NUM"))
    except (TypeError, ValueError):
        return None


def _can_create(query) -> bool:
    try:
        rows = query("SELECT has_schema_privilege(current_user, 'public', 'CREATE') AS can_create")
    except Exception:
        return False
    if not rows:
        return False
    return bool(rows[0].get("can_create"))


def _extensions(query) -> list[str]:
    try:
        rows = query("SELECT extname FROM pg_extension")
    except Exception as exc:
        raise DatabaseIdentityError(redact_text(f"Extensions could not be read ({exc})") or "Extensions could not be read.") from exc
    return [str(row.get("extname") or "") for row in rows if row.get("extname")]


PUBLIC_TABLES_SQL = (
    "SELECT c.relname AS table_name FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
)


def _tables(query) -> list[str]:
    rows = query(PUBLIC_TABLES_SQL)
    return [str(row.get("table_name")) for row in rows]


def _relations(query, relkind: str, key: str) -> list[str]:
    try:
        rows = query(
            "SELECT c.relname AS relation_name FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = 'public' AND c.relkind = '{relkind}' ORDER BY 1"
        )
    except Exception:
        return []
    return [str(row.get(key)) for row in rows if row.get(key)]


def _sequence_links(query) -> list[dict]:
    """Public sequences and the table that owns them, when ownership is known."""
    try:
        rows = query(
            "SELECT c.relname AS sequence_name, t.relname AS table_name "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_depend d ON d.objid = c.oid AND d.deptype IN ('a', 'i') "
            "LEFT JOIN pg_class t ON t.oid = d.refobjid "
            "WHERE n.nspname = 'public' AND c.relkind = 'S'"
        )
    except Exception:
        return [{"sequence_name": "unlisted", "table_name": ""}]
    links = []
    for row in rows:
        name = str(row.get("sequence_name") or "")
        if name:
            links.append({"sequence_name": name, "table_name": str(row.get("table_name") or "")})
    return links


def _secondary_indexes(query) -> list[dict]:
    try:
        rows = query(
            "SELECT i.indexname AS index_name, i.tablename AS table_name FROM pg_indexes i "
            "WHERE i.schemaname = 'public' AND NOT EXISTS ("
            "SELECT 1 FROM pg_constraint c JOIN pg_class cls ON cls.oid = c.conindid "
            "WHERE cls.relname = i.indexname)"
        )
    except Exception:
        return [{"index_name": "unlisted", "table_name": ""}]
    return [
        {"index_name": str(row.get("index_name")), "table_name": str(row.get("table_name") or "")}
        for row in rows
        if row.get("index_name")
    ]


def _non_public_schemas(query) -> list[str]:
    try:
        rows = query(
            "SELECT n.nspname AS schema_name FROM pg_namespace n "
            "WHERE n.nspname NOT IN ('public', 'pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_temp%' AND n.nspname NOT LIKE 'pg_toast%' "
            "AND EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid)"
        )
    except Exception:
        return ["unlisted"]
    return [str(row.get("schema_name")) for row in rows if row.get("schema_name")]


def _views(query) -> list[str]:
    try:
        rows = query(
            "SELECT c.relname AS view_name FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'v' ORDER BY 1"
        )
    except Exception:
        return []
    return [str(row.get("view_name")) for row in rows]


def _estimates(query, tables: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    try:
        rows = query(
            "SELECT c.relname AS table_name, c.reltuples AS estimate FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )
    except Exception:
        return {name: 0 for name in tables}
    for row in rows:
        name = str(row.get("table_name"))
        if name in tables:
            try:
                found[name] = max(int(float(row.get("estimate") or 0)), 0)
            except (TypeError, ValueError):
                found[name] = 0
    return found
