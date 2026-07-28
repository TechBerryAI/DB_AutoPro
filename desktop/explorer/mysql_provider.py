"""MySQL metadata provider for the Database Explorer."""

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

class MySQLExplorerProvider:
    """Load MySQL metadata from information_schema / SHOW (no hardcoded values)."""

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

    def _server_connector(self):
        """Server-level connection (not scoped to the login database)."""
        # Prefer information_schema so SHOW DATABASES / SCHEMATA see the full server.
        scoped = replace(self.connection, database="information_schema")
        return get_connector_from_info(scoped.db_type, scoped.to_connection_info())

    def _server_query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        try:
            return self._server_connector().execute_query(sql, params)
        except Exception:
            # Fallback: connect with no default database selected.
            bare = replace(self.connection, database="")
            connector = get_connector_from_info(bare.db_type, bare.to_connection_info())
            return connector.execute_query(sql, params)

    def _fmt(self, value: Any, default: str = "—") -> str:
        if value is None or value == "":
            return default
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return str(value)

    def _fmt_bytes(self, size_bytes: Any) -> str:
        try:
            n = float(size_bytes or 0)
        except (TypeError, ValueError):
            return "—"
        if n <= 0:
            return "0 B"
        if n >= 1024 ** 3:
            return f"{n / (1024 ** 3):.2f} GB"
        if n >= 1024 ** 2:
            return f"{n / (1024 ** 2):.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{int(n)} B"

    def _get_server_version(self) -> str:
        if self._server_version is not None:
            return self._server_version
        try:
            rows = self._server_query("SELECT VERSION() AS v")
            self._server_version = self._fmt(rows[0]["v"]) if rows else "—"
        except Exception:
            self._server_version = "—"
        return self._server_version

    def list_databases(self) -> list[DatabaseSummary]:
        """Return every database on the MySQL server (not only the login DB grants)."""
        from desktop.explorer.mysql_inventory import (
            fetch_schema_sizes_via_ssh,
            list_database_names_via_ssh,
        )

        version = self._get_server_version()

        # Account-scoped SHOW DATABASES (limited by MySQL grants).
        names: list[str] = []
        try:
            show_rows = self._server_query("SHOW DATABASES")
            for r in show_rows:
                name = r.get("Database") or r.get("database")
                if name is None and r:
                    name = next(iter(r.values()), None)
                if name is not None and str(name).strip():
                    names.append(str(name).strip())
        except Exception:
            names = []

        # When SSH is enabled, list via privileged local mysql on the Linux host
        # so inventory matches `SHOW DATABASES` in an admin shell (full server).
        ssh_names = list_database_names_via_ssh(self.connection)
        if ssh_names and len(ssh_names) >= len(names):
            names = ssh_names

        # Enrich with charset / collation from the SQL session (may be grant-limited).
        meta_by_name: dict[str, dict[str, Any]] = {}
        try:
            meta_rows = self._server_query(
                """
                SELECT
                    SCHEMA_NAME AS name,
                    DEFAULT_CHARACTER_SET_NAME AS encoding,
                    DEFAULT_COLLATION_NAME AS collation
                FROM information_schema.SCHEMATA
                ORDER BY SCHEMA_NAME
                """
            )
            for row in meta_rows:
                key = str(row.get("name") or "").strip()
                if key:
                    meta_by_name[key] = dict(row)
        except Exception:
            pass

        if not names:
            names = sorted(meta_by_name.keys())

        size_by_name: dict[str, Any] = {}
        try:
            size_rows = self._server_query(
                """
                SELECT
                    TABLE_SCHEMA AS name,
                    COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) AS size_bytes
                FROM information_schema.TABLES
                GROUP BY TABLE_SCHEMA
                """
            )
            for row in size_rows:
                key = str(row.get("name") or "").strip()
                if key:
                    size_by_name[key] = row.get("size_bytes")
        except Exception:
            pass

        # Prefer SSH sizes when they cover schemas the login user cannot see.
        ssh_sizes = fetch_schema_sizes_via_ssh(self.connection)
        if ssh_sizes:
            size_by_name.update(ssh_sizes)

        seen: set[str] = set()
        ordered: list[str] = []
        for name in names:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        for name in sorted(meta_by_name.keys()):
            if name not in seen:
                seen.add(name)
                ordered.append(name)

        out: list[DatabaseSummary] = []
        for name in ordered:
            meta = meta_by_name.get(name, {})
            out.append(
                DatabaseSummary(
                    name=name,
                    size=self._fmt_bytes(size_by_name.get(name)),
                    owner="—",
                    encoding=self._fmt(meta.get("encoding")),
                    collation=self._fmt(meta.get("collation")),
                    character_type=self._fmt(meta.get("encoding")),
                    tablespace="—",
                    connection_limit="—",
                    created_date="—",
                    last_connected="—",
                    server_version=version,
                    status="online",
                    raw={**meta, "size_bytes": size_by_name.get(name)},
                )
            )
        return out

    def get_general_info(self, database: str) -> dict[str, str]:
        rows = self._server_query(
            """
            SELECT
                s.SCHEMA_NAME AS name,
                s.DEFAULT_CHARACTER_SET_NAME AS encoding,
                s.DEFAULT_COLLATION_NAME AS collation,
                COALESCE(ROUND(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0), 0) AS size_bytes
            FROM information_schema.SCHEMATA s
            LEFT JOIN information_schema.TABLES t
                ON t.TABLE_SCHEMA = s.SCHEMA_NAME
            WHERE s.SCHEMA_NAME = %s
            GROUP BY
                s.SCHEMA_NAME,
                s.DEFAULT_CHARACTER_SET_NAME,
                s.DEFAULT_COLLATION_NAME
            """,
            (database,),
        )
        row = rows[0] if rows else {}
        backup = self.get_backup_info(database)
        return {
            "Database Name": database,
            "Owner": "—",
            "Database Size": self._fmt_bytes(row.get("size_bytes")),
            "Encoding": self._fmt(row.get("encoding")),
            "Collation": self._fmt(row.get("collation")),
            "Character Type": self._fmt(row.get("encoding")),
            "Tablespace": "—",
            "Connection Limit": "—",
            "Creation Date": "—",
            "Last Backup": backup.last_backup,
            "Last Restore": backup.last_restore,
            "MySQL Version": self._get_server_version(),
        }

    def list_schemas(self, database: str) -> list[SchemaNode]:
        """MySQL treats DATABASE and SCHEMA as the same; expose one schema node."""
        table_count = self._count(
            database,
            """
            SELECT COUNT(*) AS c FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            """,
            (database,),
        )
        view_count = self._count(
            database,
            """
            SELECT COUNT(*) AS c FROM information_schema.VIEWS
            WHERE TABLE_SCHEMA = %s
            """,
            (database,),
        )
        function_count = self._count(
            database,
            """
            SELECT COUNT(*) AS c FROM information_schema.ROUTINES
            WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = 'FUNCTION'
            """,
            (database,),
        )
        procedure_count = self._count(
            database,
            """
            SELECT COUNT(*) AS c FROM information_schema.ROUTINES
            WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = 'PROCEDURE'
            """,
            (database,),
        )
        trigger_count = self._count(
            database,
            """
            SELECT COUNT(*) AS c FROM information_schema.TRIGGERS
            WHERE TRIGGER_SCHEMA = %s
            """,
            (database,),
        )
        index_count = self._count(
            database,
            """
            SELECT COUNT(DISTINCT INDEX_NAME) AS c
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
            """,
            (database,),
        )
        fk_count = self._count(
            database,
            """
            SELECT COUNT(*) AS c
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """,
            (database,),
        )
        return [
            SchemaNode(
                name=database,
                table_count=table_count,
                view_count=view_count,
                matview_count=0,
                function_count=function_count,
                procedure_count=procedure_count,
                sequence_count=0,
                trigger_count=trigger_count,
                index_count=index_count,
                foreign_key_count=fk_count,
            )
        ]

    def _count(self, database: str, sql: str, params: tuple[Any, ...]) -> int:
        try:
            rows = self._query(database, sql, params)
            return int(rows[0]["c"]) if rows else 0
        except Exception:
            return 0

    def list_objects(self, database: str, schema: str, object_type: str) -> list[ObjectRef]:
        kind = object_type.lower().replace(" ", "_")
        schema_name = schema or database

        if kind == "tables":
            rows = self._query(
                database,
                """
                SELECT
                    TABLE_NAME AS name,
                    COALESCE(TABLE_ROWS, 0) AS row_count,
                    COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) AS size_bytes,
                    UPDATE_TIME AS last_modified
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
                """,
                (schema_name,),
            )
            return [
                ObjectRef(
                    name=str(r["name"]),
                    object_type="Table",
                    schema=schema_name,
                    size=self._fmt_bytes(r.get("size_bytes")),
                    row_count=self._fmt(r.get("row_count"), "0"),
                    last_modified=self._fmt(r.get("last_modified")),
                    qualified_name=f"{schema_name}.{r['name']}",
                )
                for r in rows
            ]

        if kind == "views":
            rows = self._query(
                database,
                """
                SELECT TABLE_NAME AS name
                FROM information_schema.VIEWS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME
                """,
                (schema_name,),
            )
            return [
                ObjectRef(
                    name=str(r["name"]),
                    object_type="View",
                    schema=schema_name,
                    qualified_name=f"{schema_name}.{r['name']}",
                )
                for r in rows
            ]

        if kind == "functions":
            return self._list_routines(database, schema_name, "FUNCTION", "Function")
        if kind == "procedures":
            return self._list_routines(database, schema_name, "PROCEDURE", "Procedure")

        if kind == "triggers":
            rows = self._query(
                database,
                """
                SELECT TRIGGER_NAME AS name, EVENT_OBJECT_TABLE AS table_name
                FROM information_schema.TRIGGERS
                WHERE TRIGGER_SCHEMA = %s
                ORDER BY TRIGGER_NAME
                """,
                (schema_name,),
            )
            return [
                ObjectRef(
                    name=str(r["name"]),
                    object_type="Trigger",
                    schema=schema_name,
                    qualified_name=f"{schema_name}.{r.get('table_name')}.{r['name']}",
                )
                for r in rows
            ]

        if kind == "indexes":
            rows = self._query(
                database,
                """
                SELECT DISTINCT INDEX_NAME AS name, TABLE_NAME AS table_name
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = %s
                ORDER BY INDEX_NAME
                """,
                (schema_name,),
            )
            return [
                ObjectRef(
                    name=str(r["name"]),
                    object_type="Index",
                    schema=schema_name,
                    qualified_name=f"{schema_name}.{r.get('table_name')}.{r['name']}",
                )
                for r in rows
            ]

        if kind == "foreign_keys":
            rows = self._query(
                database,
                """
                SELECT CONSTRAINT_NAME AS name, TABLE_NAME AS table_name
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'
                ORDER BY CONSTRAINT_NAME
                """,
                (schema_name,),
            )
            return [
                ObjectRef(
                    name=str(r["name"]),
                    object_type="Foreign Key",
                    schema=schema_name,
                    qualified_name=f"{schema_name}.{r.get('table_name')}.{r['name']}",
                )
                for r in rows
            ]

        # MySQL has no native materialized views / sequences in classic form
        if kind in ("materialized_views", "sequences"):
            return []
        return []

    def _list_routines(
        self, database: str, schema: str, routine_type: str, label: str
    ) -> list[ObjectRef]:
        rows = self._query(
            database,
            """
            SELECT ROUTINE_NAME AS name
            FROM information_schema.ROUTINES
            WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = %s
            ORDER BY ROUTINE_NAME
            """,
            (schema, routine_type),
        )
        return [
            ObjectRef(
                name=str(r["name"]),
                object_type=label,
                schema=schema,
                qualified_name=f"{schema}.{r['name']}",
            )
            for r in rows
        ]

    def get_table_details(self, database: str, schema: str, table: str) -> TableDetails:
        schema_name = schema or database
        size_rows = self._query(
            database,
            """
            SELECT
                COALESCE(TABLE_ROWS, 0) AS row_count,
                COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) AS size_bytes
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND TABLE_TYPE = 'BASE TABLE'
            """,
            (schema_name, table),
        )
        size = self._fmt_bytes(size_rows[0].get("size_bytes")) if size_rows else "—"
        rows_est = self._fmt(size_rows[0].get("row_count"), "0") if size_rows else "—"

        pk_rows = self._query(
            database,
            """
            SELECT COLUMN_NAME AS column_name
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND CONSTRAINT_NAME = 'PRIMARY'
            ORDER BY ORDINAL_POSITION
            """,
            (schema_name, table),
        )
        pk_cols = [str(r["column_name"]) for r in pk_rows]
        pk_set = set(pk_cols)

        fk_rows = self._query(
            database,
            """
            SELECT
                CONSTRAINT_NAME AS name,
                COLUMN_NAME AS column_name,
                REFERENCED_TABLE_SCHEMA AS ref_schema,
                REFERENCED_TABLE_NAME AS ref_table,
                REFERENCED_COLUMN_NAME AS ref_column
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION
            """,
            (schema_name, table),
        )
        foreign_keys = [
            (
                f"{r['name']}: {r['column_name']} → "
                f"{r.get('ref_schema')}.{r.get('ref_table')}({r.get('ref_column')})"
            )
            for r in fk_rows
        ]

        idx_rows = self._query(
            database,
            """
            SELECT
                INDEX_NAME AS name,
                NON_UNIQUE AS non_unique,
                GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            GROUP BY INDEX_NAME, NON_UNIQUE
            ORDER BY INDEX_NAME
            """,
            (schema_name, table),
        )
        indexes = [
            (
                f"{r['name']} "
                f"({'UNIQUE ' if not int(r.get('non_unique') or 0) else ''}"
                f"[{r.get('columns') or ''}])"
            )
            for r in idx_rows
        ]

        col_rows = self._query(
            database,
            """
            SELECT
                COLUMN_NAME AS column_name,
                COLUMN_TYPE AS data_type,
                IS_NULLABLE AS is_nullable,
                COLUMN_DEFAULT AS column_default,
                EXTRA AS extra
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (schema_name, table),
        )
        columns = []
        for r in col_rows:
            extra = str(r.get("extra") or "").lower()
            identity = "—"
            if "auto_increment" in extra:
                identity = "auto_increment"
            columns.append(
                ColumnInfo(
                    name=str(r["column_name"]),
                    data_type=str(r.get("data_type") or "—"),
                    nullable=str(r.get("is_nullable") or "—"),
                    default_value=self._fmt(r.get("column_default")),
                    identity=identity,
                    is_primary_key=str(r["column_name"]) in pk_set,
                )
            )

        return TableDetails(
            name=table,
            schema=schema_name,
            estimated_rows=rows_est,
            table_size=size,
            primary_key=", ".join(pk_cols) if pk_cols else "—",
            foreign_keys=foreign_keys,
            indexes=indexes,
            columns=columns,
        )

    def get_storage(self, database: str) -> StorageSummary:
        used_rows = self._query(
            database,
            """
            SELECT
                COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) AS used_bytes,
                COALESCE(SUM(DATA_LENGTH), 0) AS data_bytes,
                COALESCE(SUM(INDEX_LENGTH), 0) AS index_bytes,
                COALESCE(SUM(DATA_FREE), 0) AS free_bytes
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
            """,
            (database,),
        )
        used = self._fmt_bytes(used_rows[0].get("used_bytes")) if used_rows else "—"
        free = self._fmt_bytes(used_rows[0].get("free_bytes")) if used_rows else "—"
        index_size = self._fmt_bytes(used_rows[0].get("index_bytes")) if used_rows else "—"

        top = self._query(
            database,
            """
            SELECT
                TABLE_NAME AS name,
                COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) AS size_bytes
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY (DATA_LENGTH + INDEX_LENGTH) DESC
            LIMIT 10
            """,
            (database,),
        )
        largest = [
            {"name": f"{database}.{r['name']}", "size": self._fmt_bytes(r.get("size_bytes"))}
            for r in top
        ]
        return StorageSummary(
            used_space=used,
            free_space=free,
            index_size=index_size,
            toast_size="—",
            largest_tables=largest,
            top_objects=largest,
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
            sequences=0,
            triggers=sum(s.trigger_count for s in schemas),
            materialized_views=0,
        )

    def get_configuration(self, database: str) -> ConfigInfo:
        # Expressions are fixed constants only (not user input).
        keys = [
            ("MySQL Version", "VERSION()"),
            ("Port", "@@port"),
            ("Data Directory", "@@datadir"),
            ("Character Set Server", "@@character_set_server"),
            ("Collation Server", "@@collation_server"),
            ("Max Connections", "@@max_connections"),
            ("InnoDB Buffer Pool Size", "@@innodb_buffer_pool_size"),
            ("Tmp Table Size", "@@tmp_table_size"),
            ("Max Allowed Packet", "@@max_allowed_packet"),
            ("Timezone", "@@system_time_zone"),
        ]
        items: list[tuple[str, str]] = []
        for label, expr in keys:
            try:
                rows = self._query(database, f"SELECT {expr} AS v")
                items.append((label, self._fmt(rows[0].get("v")) if rows else "—"))
            except Exception:
                items.append((label, "—"))
        return ConfigInfo(items=items)

    def list_extensions(self, database: str) -> list[ExtensionInfo]:
        """Map MySQL plugins to the Extensions panel."""
        try:
            rows = self._query(database, "SHOW PLUGINS")
        except Exception:
            try:
                rows = self._query(
                    database,
                    """
                    SELECT
                        PLUGIN_NAME AS name,
                        PLUGIN_STATUS AS status,
                        PLUGIN_TYPE AS type,
                        PLUGIN_DESCRIPTION AS description
                    FROM information_schema.PLUGINS
                    ORDER BY PLUGIN_NAME
                    """
                )
                return [
                    ExtensionInfo(
                        name=str(r.get("name") or "—"),
                        version=self._fmt(r.get("status")),
                        description=self._fmt(r.get("description") or r.get("type")),
                    )
                    for r in rows
                ]
            except Exception:
                return []

        out: list[ExtensionInfo] = []
        for r in rows:
            # SHOW PLUGINS columns: Name, Status, Type, Library, License
            name = r.get("Name") or r.get("name") or "—"
            status = r.get("Status") or r.get("status") or "—"
            ptype = r.get("Type") or r.get("type") or "—"
            out.append(
                ExtensionInfo(
                    name=str(name),
                    version=str(status),
                    description=str(ptype),
                )
            )
        return out

    def get_maintenance(self, database: str) -> MaintenanceInfo:
        rows = self._query(
            database,
            """
            SELECT
                TABLE_NAME AS name,
                UPDATE_TIME AS update_time,
                CHECK_TIME AS check_time,
                CREATE_TIME AS create_time,
                ENGINE AS engine
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY COALESCE(UPDATE_TIME, CREATE_TIME) DESC
            LIMIT 20
            """,
            (database,),
        )
        details = [
            {
                "name": f"{database}.{r['name']}",
                "last_vacuum": self._fmt(r.get("check_time")),
                "last_autovacuum": "—",
                "last_analyze": self._fmt(r.get("update_time")),
            }
            for r in rows
        ]
        last_analyze = details[0]["last_analyze"] if details else "—"
        last_check = details[0]["last_vacuum"] if details else "—"
        return MaintenanceInfo(
            last_vacuum=last_check,
            last_analyze=last_analyze,
            last_autovacuum="—",
            last_reindex="—",
            autovacuum_status="N/A (MySQL uses InnoDB purge / event scheduler)",
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
            db_type=DatabaseType.MYSQL,
            method=BackupMethod.MYSQLDUMP,
            backup_mode=BackupMode.FULL,
            database=database,
        )
        chain = load_chain(Path(service.output_dir), request)
        last_entry = chain.get("last") or chain.get("full") or {}

        if matched:
            latest = matched[0]
            size_bytes = int(latest.get("size_bytes") or 0)
            size_text = self._fmt_bytes(size_bytes)
            return BackupInfo(
                last_backup=self._fmt(latest.get("created_at")),
                backup_type=self._fmt(last_entry.get("mode"), "full"),
                logical_physical="Logical",
                backup_method=self._fmt(last_entry.get("method"), "mysqldump"),
                backup_scope="—",
                backup_format="SQL",
                backup_size=size_text,
                duration="—",
                compression="—",
                status="available",
                last_restore="—",
            )

        if last_entry:
            return BackupInfo(
                last_backup=self._fmt(last_entry.get("timestamp")),
                backup_type=self._fmt(last_entry.get("mode")),
                logical_physical="Logical",
                backup_method=self._fmt(last_entry.get("method")),
                backup_format="SQL",
                status="recorded",
            )

        return BackupInfo(status="no backups found")
