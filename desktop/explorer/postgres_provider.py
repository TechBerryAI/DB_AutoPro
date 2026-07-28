"""PostgreSQL metadata provider for the Database Explorer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.connectors.base import get_connector_from_info
from backend.models.schemas import BackupMethod, BackupMode, BackupRequest, DatabaseType
from backend.services.backup_chain import load_chain
from backend.services.backup_service import BackupService
from desktop.connection import ConnectionResult
from desktop.explorer.provider import (
    BackupInfo,
    ColumnInfo,
    ConfigInfo,
    DatabaseSummary,
    DbStatistics,
    ExtensionInfo,
    MaintenanceInfo,
    ObjectRef,
    SchemaNode,
    StorageSummary,
    TableDetails,
)

_OBJECT_KIND_MAP = {
    "tables": "r",
    "views": "v",
    "materialized_views": "m",
    "sequences": "S",
    "indexes": "i",
}


class PostgresExplorerProvider:
    """Load PostgreSQL metadata from system catalogs (no hardcoded values)."""

    supports_object_explorer = True

    def __init__(self, connection: ConnectionResult) -> None:
        self.connection = connection
        self._server_version: str | None = None

    def _scoped(self, database: str):
        scoped = replace(self.connection, database=database)
        return get_connector_from_info(scoped.db_type, scoped.to_connection_info())

    def _query(self, database: str, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        connector = self._scoped(database)
        return connector.execute_query(sql, params)

    def _server_query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        info = self.connection.to_connection_info()
        connector = get_connector_from_info(self.connection.db_type, info)
        return connector.execute_query(sql, params)

    def _fmt(self, value: Any, default: str = "—") -> str:
        if value is None or value == "":
            return default
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return str(value)

    def _get_server_version(self) -> str:
        if self._server_version is not None:
            return self._server_version
        try:
            rows = self._server_query("SELECT current_setting('server_version') AS v")
            self._server_version = self._fmt(rows[0]["v"]) if rows else "—"
        except Exception:
            self._server_version = "—"
        return self._server_version

    def list_databases(self) -> list[DatabaseSummary]:
        version = self._get_server_version()
        rows = self._server_query(
            """
            SELECT
                d.datname AS name,
                pg_catalog.pg_get_userbyid(d.datdba) AS owner,
                pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(d.datname)) AS size,
                pg_catalog.pg_encoding_to_char(d.encoding) AS encoding,
                d.datcollate AS collation,
                d.datctype AS character_type,
                COALESCE(ts.spcname, 'pg_default') AS tablespace,
                CASE
                    WHEN d.datconnlimit < 0 THEN 'unlimited'
                    ELSE d.datconnlimit::text
                END AS connection_limit,
                CASE
                    WHEN d.datallowconn THEN 'online'
                    ELSE 'restricted'
                END AS status
            FROM pg_catalog.pg_database d
            LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = d.dattablespace
            WHERE NOT d.datistemplate
            ORDER BY d.datname
            """
        )
        out: list[DatabaseSummary] = []
        for row in rows:
            created = "—"
            try:
                # Best-effort; may fail without privileges on data files.
                crows = self._server_query(
                    """
                    SELECT (pg_stat_file('base/' || oid || '/PG_VERSION', true)).modification AS created
                    FROM pg_database
                    WHERE datname = %s
                    """,
                    (row["name"],),
                )
                if crows and crows[0].get("created"):
                    created = self._fmt(crows[0]["created"])
            except Exception:
                created = "—"

            out.append(
                DatabaseSummary(
                    name=str(row["name"]),
                    size=self._fmt(row.get("size")),
                    owner=self._fmt(row.get("owner")),
                    encoding=self._fmt(row.get("encoding")),
                    collation=self._fmt(row.get("collation")),
                    character_type=self._fmt(row.get("character_type")),
                    tablespace=self._fmt(row.get("tablespace")),
                    connection_limit=self._fmt(row.get("connection_limit")),
                    created_date=created,
                    last_connected="—",
                    server_version=version,
                    status=self._fmt(row.get("status"), "online"),
                    raw=dict(row),
                )
            )
        return out

    def get_general_info(self, database: str) -> dict[str, str]:
        rows = self._server_query(
            """
            SELECT
                d.datname AS name,
                pg_catalog.pg_get_userbyid(d.datdba) AS owner,
                pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(d.datname)) AS size,
                pg_catalog.pg_encoding_to_char(d.encoding) AS encoding,
                d.datcollate AS collation,
                d.datctype AS character_type,
                COALESCE(ts.spcname, 'pg_default') AS tablespace,
                CASE
                    WHEN d.datconnlimit < 0 THEN 'unlimited'
                    ELSE d.datconnlimit::text
                END AS connection_limit
            FROM pg_catalog.pg_database d
            LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = d.dattablespace
            WHERE d.datname = %s
            """,
            (database,),
        )
        row = rows[0] if rows else {}
        backup = self.get_backup_info(database)
        return {
            "Database Name": database,
            "Owner": self._fmt(row.get("owner")),
            "Database Size": self._fmt(row.get("size")),
            "Encoding": self._fmt(row.get("encoding")),
            "Collation": self._fmt(row.get("collation")),
            "Character Type": self._fmt(row.get("character_type")),
            "Tablespace": self._fmt(row.get("tablespace")),
            "Connection Limit": self._fmt(row.get("connection_limit")),
            "Creation Date": "—",
            "Last Backup": backup.last_backup,
            "Last Restore": backup.last_restore,
            "PostgreSQL Version": self._get_server_version(),
        }

    def list_schemas(self, database: str) -> list[SchemaNode]:
        rows = self._query(
            database,
            """
            SELECT
                n.nspname AS name,
                COUNT(*) FILTER (WHERE c.relkind = 'r') AS table_count,
                COUNT(*) FILTER (WHERE c.relkind = 'v') AS view_count,
                COUNT(*) FILTER (WHERE c.relkind = 'm') AS matview_count,
                COUNT(*) FILTER (WHERE c.relkind = 'S') AS sequence_count,
                COUNT(*) FILTER (WHERE c.relkind = 'i') AS index_count
            FROM pg_catalog.pg_namespace n
            LEFT JOIN pg_catalog.pg_class c
                ON c.relnamespace = n.oid
               AND c.relkind IN ('r', 'v', 'm', 'S', 'i')
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname !~ '^pg_toast'
              AND n.nspname !~ '^pg_temp_'
            GROUP BY n.nspname
            ORDER BY n.nspname
            """,
        )
        nodes: list[SchemaNode] = []
        for row in rows:
            schema = str(row["name"])
            fn = self._count_functions(database, schema)
            proc = self._count_procedures(database, schema)
            trig = self._count_triggers(database, schema)
            fks = self._count_foreign_keys(database, schema)
            nodes.append(
                SchemaNode(
                    name=schema,
                    table_count=int(row.get("table_count") or 0),
                    view_count=int(row.get("view_count") or 0),
                    matview_count=int(row.get("matview_count") or 0),
                    function_count=fn,
                    procedure_count=proc,
                    sequence_count=int(row.get("sequence_count") or 0),
                    trigger_count=trig,
                    index_count=int(row.get("index_count") or 0),
                    foreign_key_count=fks,
                )
            )
        return nodes

    def _count_functions(self, database: str, schema: str) -> int:
        rows = self._query(
            database,
            """
            SELECT COUNT(*) AS c
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s
              AND p.prokind = 'f'
            """,
            (schema,),
        )
        return int(rows[0]["c"]) if rows else 0

    def _count_procedures(self, database: str, schema: str) -> int:
        rows = self._query(
            database,
            """
            SELECT COUNT(*) AS c
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s
              AND p.prokind = 'p'
            """,
            (schema,),
        )
        return int(rows[0]["c"]) if rows else 0

    def _count_triggers(self, database: str, schema: str) -> int:
        rows = self._query(
            database,
            """
            SELECT COUNT(*) AS c
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND NOT t.tgisinternal
            """,
            (schema,),
        )
        return int(rows[0]["c"]) if rows else 0

    def _count_foreign_keys(self, database: str, schema: str) -> int:
        rows = self._query(
            database,
            """
            SELECT COUNT(*) AS c
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND con.contype = 'f'
            """,
            (schema,),
        )
        return int(rows[0]["c"]) if rows else 0

    def list_objects(self, database: str, schema: str, object_type: str) -> list[ObjectRef]:
        kind = object_type.lower().replace(" ", "_")
        if kind in ("tables", "views", "materialized_views", "sequences", "indexes"):
            return self._list_relations(database, schema, kind)
        if kind == "functions":
            return self._list_routines(database, schema, "f", "Function")
        if kind == "procedures":
            return self._list_routines(database, schema, "p", "Procedure")
        if kind == "triggers":
            return self._list_triggers(database, schema)
        if kind == "foreign_keys":
            return self._list_foreign_keys(database, schema)
        return []

    def _list_relations(self, database: str, schema: str, kind: str) -> list[ObjectRef]:
        relkind = _OBJECT_KIND_MAP[kind]
        type_label = {
            "tables": "Table",
            "views": "View",
            "materialized_views": "Materialized View",
            "sequences": "Sequence",
            "indexes": "Index",
        }[kind]
        rows = self._query(
            database,
            """
            SELECT
                c.relname AS name,
                pg_catalog.pg_size_pretty(pg_catalog.pg_total_relation_size(c.oid)) AS size,
                COALESCE(s.n_live_tup::text, '—') AS row_count
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_stat_user_tables s
                ON s.relid = c.oid
            WHERE n.nspname = %s
              AND c.relkind = %s
            ORDER BY c.relname
            """,
            (schema, relkind),
        )
        out: list[ObjectRef] = []
        for row in rows:
            name = str(row["name"])
            out.append(
                ObjectRef(
                    name=name,
                    object_type=type_label,
                    schema=schema,
                    size=self._fmt(row.get("size")),
                    row_count=self._fmt(row.get("row_count")) if kind == "tables" else "—",
                    last_modified="—",
                    qualified_name=f"{schema}.{name}",
                )
            )
        return out

    def _list_routines(
        self, database: str, schema: str, prokind: str, type_label: str
    ) -> list[ObjectRef]:
        rows = self._query(
            database,
            """
            SELECT p.proname AS name
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s
              AND p.prokind = %s
            ORDER BY p.proname
            """,
            (schema, prokind),
        )
        return [
            ObjectRef(
                name=str(row["name"]),
                object_type=type_label,
                schema=schema,
                qualified_name=f"{schema}.{row['name']}",
            )
            for row in rows
        ]

    def _list_triggers(self, database: str, schema: str) -> list[ObjectRef]:
        rows = self._query(
            database,
            """
            SELECT t.tgname AS name, c.relname AS table_name
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND NOT t.tgisinternal
            ORDER BY t.tgname
            """,
            (schema,),
        )
        return [
            ObjectRef(
                name=str(row["name"]),
                object_type="Trigger",
                schema=schema,
                qualified_name=f"{schema}.{row['table_name']}.{row['name']}",
            )
            for row in rows
        ]

    def _list_foreign_keys(self, database: str, schema: str) -> list[ObjectRef]:
        rows = self._query(
            database,
            """
            SELECT con.conname AS name, c.relname AS table_name
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND con.contype = 'f'
            ORDER BY con.conname
            """,
            (schema,),
        )
        return [
            ObjectRef(
                name=str(row["name"]),
                object_type="Foreign Key",
                schema=schema,
                qualified_name=f"{schema}.{row['table_name']}.{row['name']}",
            )
            for row in rows
        ]

    def get_table_details(self, database: str, schema: str, table: str) -> TableDetails:
        size_rows = self._query(
            database,
            """
            SELECT
                pg_catalog.pg_size_pretty(pg_catalog.pg_total_relation_size(c.oid)) AS size,
                COALESCE(s.n_live_tup::text, '—') AS rows
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_stat_user_tables s ON s.relid = c.oid
            WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'
            """,
            (schema, table),
        )
        size = self._fmt(size_rows[0].get("size")) if size_rows else "—"
        rows_est = self._fmt(size_rows[0].get("rows")) if size_rows else "—"

        pk_rows = self._query(
            database,
            """
            SELECT a.attname AS column_name
            FROM pg_catalog.pg_index i
            JOIN pg_catalog.pg_attribute a
              ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
            ORDER BY a.attnum
            """,
            (schema, table),
        )
        pk_cols = [str(r["column_name"]) for r in pk_rows]
        pk_set = set(pk_cols)

        fk_rows = self._query(
            database,
            """
            SELECT
                con.conname AS name,
                pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND con.contype = 'f'
            ORDER BY con.conname
            """,
            (schema, table),
        )
        foreign_keys = [
            f"{r['name']}: {r.get('definition') or ''}".strip() for r in fk_rows
        ]

        idx_rows = self._query(
            database,
            """
            SELECT indexname || ' — ' || indexdef AS idx
            FROM pg_catalog.pg_indexes
            WHERE schemaname = %s AND tablename = %s
            ORDER BY indexname
            """,
            (schema, table),
        )
        indexes = [str(r["idx"]) for r in idx_rows]

        col_rows = self._query(
            database,
            """
            SELECT
                column_name,
                data_type,
                is_nullable,
                column_default,
                CASE
                    WHEN is_identity = 'YES' THEN identity_generation
                    ELSE NULL
                END AS identity
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        columns = [
            ColumnInfo(
                name=str(r["column_name"]),
                data_type=str(r.get("data_type") or "—"),
                nullable=str(r.get("is_nullable") or "—"),
                default_value=self._fmt(r.get("column_default")),
                identity=self._fmt(r.get("identity")),
                is_primary_key=str(r["column_name"]) in pk_set,
            )
            for r in col_rows
        ]

        return TableDetails(
            name=table,
            schema=schema,
            estimated_rows=rows_est,
            table_size=size,
            primary_key=", ".join(pk_cols) if pk_cols else "—",
            foreign_keys=foreign_keys,
            indexes=indexes,
            columns=columns,
        )

    def get_storage(self, database: str) -> StorageSummary:
        used_rows = self._server_query(
            "SELECT pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(%s)) AS used",
            (database,),
        )
        used = self._fmt(used_rows[0].get("used")) if used_rows else "—"

        size_rows = self._query(
            database,
            """
            SELECT
                pg_catalog.pg_size_pretty(SUM(pg_catalog.pg_indexes_size(c.oid))) AS index_size,
                pg_catalog.pg_size_pretty(
                    SUM(COALESCE(pg_catalog.pg_relation_size(c.reltoastrelid), 0))
                ) AS toast_size
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND c.relkind = 'r'
            """,
        )
        index_size = self._fmt(size_rows[0].get("index_size")) if size_rows else "—"
        toast_size = self._fmt(size_rows[0].get("toast_size")) if size_rows else "—"

        top = self._query(
            database,
            """
            SELECT
                n.nspname || '.' || c.relname AS name,
                pg_catalog.pg_size_pretty(pg_catalog.pg_total_relation_size(c.oid)) AS size,
                pg_catalog.pg_total_relation_size(c.oid) AS size_bytes
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND c.relkind IN ('r', 'm', 'i')
            ORDER BY pg_catalog.pg_total_relation_size(c.oid) DESC
            LIMIT 10
            """,
        )
        largest_tables = [
            {"name": str(r["name"]), "size": self._fmt(r.get("size"))}
            for r in top
            if "." in str(r.get("name", ""))
        ]
        # Prefer tables only for "Largest Tables"
        table_top = self._query(
            database,
            """
            SELECT
                n.nspname || '.' || c.relname AS name,
                pg_catalog.pg_size_pretty(pg_catalog.pg_total_relation_size(c.oid)) AS size
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND c.relkind = 'r'
            ORDER BY pg_catalog.pg_total_relation_size(c.oid) DESC
            LIMIT 10
            """,
        )
        largest_tables = [
            {"name": str(r["name"]), "size": self._fmt(r.get("size"))} for r in table_top
        ]
        top_objects = [
            {"name": str(r["name"]), "size": self._fmt(r.get("size"))} for r in top
        ]

        return StorageSummary(
            used_space=used,
            free_space="—",
            index_size=index_size,
            toast_size=toast_size,
            largest_tables=largest_tables,
            top_objects=top_objects,
            growth_note="Historical growth requires monitoring history (not shown here).",
        )

    def get_statistics(self, database: str) -> DbStatistics:
        schemas = self.list_schemas(database)
        return DbStatistics(
            schemas=len(schemas),
            tables=sum(s.table_count for s in schemas),
            views=sum(s.view_count for s in schemas),
            functions=sum(s.function_count for s in schemas),
            procedures=sum(s.procedure_count for s in schemas),
            indexes=sum(s.index_count for s in schemas),
            sequences=sum(s.sequence_count for s in schemas),
            triggers=sum(s.trigger_count for s in schemas),
            materialized_views=sum(s.matview_count for s in schemas),
        )

    def get_configuration(self, database: str) -> ConfigInfo:
        keys = [
            ("PostgreSQL Version", "server_version"),
            ("Port", "port"),
            ("Data Directory", "data_directory"),
            ("Archive Mode", "archive_mode"),
            ("Max Connections", "max_connections"),
            ("Shared Buffers", "shared_buffers"),
            ("Work Memory", "work_mem"),
            ("Maintenance Work Memory", "maintenance_work_mem"),
            ("Checkpoint Timeout", "checkpoint_timeout"),
            ("Timezone", "TimeZone"),
        ]
        items: list[tuple[str, str]] = []
        for label, setting in keys:
            try:
                rows = self._query(
                    database,
                    "SELECT current_setting(%s) AS v",
                    (setting,),
                )
                items.append((label, self._fmt(rows[0].get("v")) if rows else "—"))
            except Exception:
                items.append((label, "—"))
        return ConfigInfo(items=items)

    def list_extensions(self, database: str) -> list[ExtensionInfo]:
        rows = self._query(
            database,
            """
            SELECT
                e.extname AS name,
                e.extversion AS version,
                COALESCE(a.comment, '—') AS description
            FROM pg_catalog.pg_extension e
            LEFT JOIN pg_catalog.pg_available_extensions a ON a.name = e.extname
            ORDER BY e.extname
            """,
        )
        return [
            ExtensionInfo(
                name=str(r["name"]),
                version=self._fmt(r.get("version")),
                description=self._fmt(r.get("description")),
            )
            for r in rows
        ]

    def get_maintenance(self, database: str) -> MaintenanceInfo:
        try:
            av = self._query(database, "SELECT current_setting('autovacuum') AS v")
            av_status = self._fmt(av[0].get("v")) if av else "—"
        except Exception:
            av_status = "—"

        rows = self._query(
            database,
            """
            SELECT
                schemaname || '.' || relname AS name,
                last_vacuum::text AS last_vacuum,
                last_autovacuum::text AS last_autovacuum,
                last_analyze::text AS last_analyze,
                last_autoanalyze::text AS last_autoanalyze
            FROM pg_catalog.pg_stat_user_tables
            ORDER BY GREATEST(
                COALESCE(last_vacuum, '-infinity'::timestamp),
                COALESCE(last_autovacuum, '-infinity'::timestamp),
                COALESCE(last_analyze, '-infinity'::timestamp),
                COALESCE(last_autoanalyze, '-infinity'::timestamp)
            ) DESC
            LIMIT 20
            """,
        )
        details = [
            {
                "name": str(r["name"]),
                "last_vacuum": self._fmt(r.get("last_vacuum")),
                "last_autovacuum": self._fmt(r.get("last_autovacuum")),
                "last_analyze": self._fmt(r.get("last_analyze")),
            }
            for r in rows
        ]
        last_vacuum = details[0]["last_vacuum"] if details else "—"
        last_analyze = details[0]["last_analyze"] if details else "—"
        last_autovacuum = details[0]["last_autovacuum"] if details else "—"
        # Prefer first non-dash values across rows
        for d in details:
            if last_vacuum == "—" and d["last_vacuum"] != "—":
                last_vacuum = d["last_vacuum"]
            if last_analyze == "—" and d["last_analyze"] != "—":
                last_analyze = d["last_analyze"]
            if last_autovacuum == "—" and d["last_autovacuum"] != "—":
                last_autovacuum = d["last_autovacuum"]

        return MaintenanceInfo(
            last_vacuum=last_vacuum,
            last_analyze=last_analyze,
            last_autovacuum=last_autovacuum,
            last_reindex="—",
            autovacuum_status=av_status,
            details=details,
        )

    def get_backup_info(self, database: str) -> BackupInfo:
        service = BackupService()
        backups = service.list_backups(profile="default", list_remote=False)
        matched = [
            b
            for b in backups
            if database.lower() in str(b.get("name", "")).lower()
            or database.lower() in str(b.get("path", "")).lower()
        ]
        matched.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)

        request = BackupRequest(
            profile="default",
            db_type=DatabaseType.POSTGRESQL,
            method=BackupMethod.PG_DUMP,
            backup_mode=BackupMode.FULL,
            database=database,
        )
        chain = load_chain(Path(service.output_dir), request)
        last_entry = chain.get("last") or chain.get("full") or {}

        if matched:
            latest = matched[0]
            size_bytes = int(latest.get("size_bytes") or 0)
            if size_bytes >= 1024 * 1024:
                size_text = f"{size_bytes / (1024 * 1024):.1f} MB"
            elif size_bytes > 0:
                size_text = f"{size_bytes:,} bytes"
            else:
                size_text = "—"
            created = self._fmt(latest.get("created_at"))
            name = str(latest.get("name") or "")
            fmt = "—"
            if name.endswith(".dump"):
                fmt = "Custom Format"
            elif name.endswith(".sql"):
                fmt = "Plain SQL"
            elif name.endswith(".tar"):
                fmt = "Tar Format"
            method = self._fmt(last_entry.get("method"), "pg_dump")
            mode = self._fmt(last_entry.get("mode"), "full")
            return BackupInfo(
                last_backup=created,
                backup_type=mode,
                logical_physical="Logical" if method != "pg_basebackup" else "Physical",
                backup_method=method,
                backup_scope="—",
                backup_format=fmt,
                backup_size=size_text,
                duration="—",
                compression="—" if fmt == "Plain SQL" else "enabled",
                status="available",
                last_restore="—",
            )

        if last_entry:
            return BackupInfo(
                last_backup=self._fmt(last_entry.get("timestamp")),
                backup_type=self._fmt(last_entry.get("mode")),
                logical_physical="Logical",
                backup_method=self._fmt(last_entry.get("method")),
                backup_format="—",
                backup_size="—",
                status="recorded",
                last_restore="—",
            )

        return BackupInfo(status="no backups found")
