"""List databases available on a connected database server."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import get_connector_from_info
from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult


def fetch_server_databases(connection: ConnectionResult) -> list[dict[str, Any]]:
    """Return databases on the server with name, owner, size, and status."""
    info = connection.to_connection_info()
    connector = get_connector_from_info(connection.db_type, info)
    db_type = connection.db_type

    if db_type == DatabaseType.POSTGRESQL:
        rows = connector.execute_query(
            """
            SELECT
                d.datname AS name,
                pg_catalog.pg_get_userbyid(d.datdba) AS owner,
                pg_catalog.pg_size_pretty(
                    pg_catalog.pg_database_size(d.datname)
                ) AS size,
                CASE
                    WHEN d.datallowconn THEN 'online'
                    ELSE 'restricted'
                END AS status
            FROM pg_catalog.pg_database d
            WHERE NOT d.datistemplate
            ORDER BY d.datname
            """
        )
        return [_normalize_row(row) for row in rows]

    if db_type == DatabaseType.MYSQL:
        from dataclasses import replace

        from desktop.explorer.mysql_inventory import (
            fetch_schema_sizes_via_ssh,
            list_database_names_via_ssh,
        )

        # List against information_schema so inventory is server-wide,
        # not limited to the login database name on the connection.
        catalog = replace(connection, database="information_schema")
        connector = get_connector_from_info(catalog.db_type, catalog.to_connection_info())

        names: list[str] = []
        try:
            show_rows = connector.execute_query("SHOW DATABASES")
            for r in show_rows:
                name = r.get("Database") or r.get("database")
                if name is None and r:
                    name = next(iter(r.values()), None)
                if name is not None and str(name).strip():
                    names.append(str(name).strip())
        except Exception:
            names = []

        ssh_names = list_database_names_via_ssh(connection)
        if ssh_names and len(ssh_names) >= len(names):
            names = ssh_names

        size_by_name: dict[str, float] = {}
        encoding_by_name: dict[str, str] = {}
        try:
            meta_rows = connector.execute_query(
                """
                SELECT
                    s.SCHEMA_NAME AS name,
                    s.DEFAULT_CHARACTER_SET_NAME AS encoding,
                    COALESCE(
                        ROUND(SUM(t.DATA_LENGTH + t.INDEX_LENGTH) / 1024 / 1024, 2),
                        0
                    ) AS size_mb
                FROM information_schema.SCHEMATA s
                LEFT JOIN information_schema.TABLES t
                    ON t.TABLE_SCHEMA = s.SCHEMA_NAME
                GROUP BY s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME
                ORDER BY s.SCHEMA_NAME
                """
            )
            for row in meta_rows:
                key = str(row.get("name") or "").strip()
                if not key:
                    continue
                encoding_by_name[key] = str(row.get("encoding") or "")
                try:
                    size_by_name[key] = float(row.get("size_mb") or 0)
                except (TypeError, ValueError):
                    size_by_name[key] = 0.0
                if key not in names:
                    names.append(key)
        except Exception:
            pass

        ssh_sizes = fetch_schema_sizes_via_ssh(connection)
        for key, size_bytes in ssh_sizes.items():
            size_by_name[key] = round(float(size_bytes) / 1024 / 1024, 2)

        return [
            {
                "name": name,
                "owner": encoding_by_name.get(name) or "—",
                "size": (
                    f"{size_by_name[name]:.1f} MB"
                    if name in size_by_name and size_by_name[name]
                    else "—"
                ),
                "status": "online",
            }
            for name in names
        ]

    if db_type == DatabaseType.MSSQL:
        rows = connector.execute_query(
            """
            SELECT
                name,
                SUSER_SNAME(owner_sid) AS owner,
                state_desc AS status
            FROM sys.databases
            ORDER BY name
            """
        )
        return [
            {
                "name": row["name"],
                "owner": row.get("owner") or "—",
                "size": "—",
                "status": (row.get("status") or "online").lower(),
            }
            for row in rows
        ]

    if db_type == DatabaseType.MONGODB:
        return _list_mongodb_databases(connector)

    if db_type == DatabaseType.ORACLE:
        return _list_oracle_databases(connector, connection)

    return []


def _list_oracle_databases(connector, connection: ConnectionResult) -> list[dict[str, str]]:
    """List Oracle PDBs/containers or schemas without relying on ALL_USERS.ACCOUNT_STATUS.

    ACCOUNT_STATUS exists on DBA_USERS, not ALL_USERS — querying it on ALL_USERS
    raises ORA-00904 for typical application users (e.g. BANKING).
    """
    results: list[dict[str, str]] = []

    # Prefer pluggable databases / containers when the login can see them.
    for sql in (
        """
        SELECT name AS name, open_mode AS status
        FROM v$pdbs
        ORDER BY name
        """,
        """
        SELECT name AS name, open_mode AS status
        FROM v$containers
        WHERE con_id > 0
        ORDER BY name
        """,
    ):
        try:
            rows = connector.execute_query(sql)
        except Exception:
            continue
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            results.append(
                {
                    "name": name,
                    "owner": "container",
                    "size": "—",
                    "status": _oracle_status(row.get("status")),
                }
            )
        if results:
            return results

    # Privileged inventory of schema users.
    try:
        rows = connector.execute_query(
            """
            SELECT username AS name, account_status AS status
            FROM dba_users
            ORDER BY username
            """
        )
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            results.append(
                {
                    "name": name,
                    "owner": "schema",
                    "size": "—",
                    "status": _oracle_status(row.get("status")),
                }
            )
        if results:
            return results
    except Exception:
        pass

    # Application users can always see ALL_USERS (no ACCOUNT_STATUS column).
    try:
        rows = connector.execute_query(
            """
            SELECT username AS name
            FROM all_users
            ORDER BY username
            """
        )
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            results.append(
                {
                    "name": name,
                    "owner": "schema",
                    "size": "—",
                    "status": "online",
                }
            )
    except Exception:
        pass

    # Always surface the three standard containers for CDB architecture.
    standard = [
        ("CDB$ROOT", "read_write", "container"),
        ("PDB$SEED", "read_only", "container"),
    ]
    service = (connection.database or "ORCLPDB1").strip().upper() or "ORCLPDB1"
    standard.append((service, "online", "service"))

    existing = {r["name"].upper(): r for r in results}
    merged: list[dict[str, str]] = []
    for name, status, owner in standard:
        if name in existing:
            merged.append(existing.pop(name))
        else:
            merged.append(
                {
                    "name": name,
                    "owner": owner,
                    "size": "—",
                    "status": status,
                }
            )
    for row in results:
        key = row["name"].upper()
        if key not in {m["name"].upper() for m in merged}:
            merged.append(row)
    return merged


def _oracle_status(raw: Any) -> str:
    text = str(raw or "online").strip().lower().replace(" ", "_")
    if text in {"read_write", "open", "unlocked", ""}:
        return "online"
    if text in {"read_only", "mounted", "locked", "expired", "expired_&_locked"}:
        return text.replace("&", "and")
    if "locked" in text or "expired" in text:
        return "locked"
    return text or "online"


def _normalize_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(row.get("name", "—")),
        "owner": str(row.get("owner") or "—"),
        "size": str(row.get("size") or "—"),
        "status": str(row.get("status") or "online").lower(),
    }


def _list_mongodb_databases(connector) -> list[dict[str, str]]:
    """MongoDB list via pymongo driver."""
    try:
        client = connector._get_client()
        try:
            result = []
            for db_info in client.list_databases():
                name = db_info.get("name", "")
                size_bytes = db_info.get("sizeOnDisk", 0)
                size = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes else "—"
                result.append({
                    "name": name,
                    "owner": "—",
                    "size": size,
                    "status": "online",
                })
            return sorted(result, key=lambda r: r["name"])
        finally:
            client.close()
    except Exception:
        return []


def fetch_mongodb_database_size_bytes(connection: ConnectionResult, database_name: str) -> int:
    """On-disk size of one MongoDB database, matching the Databases page."""
    if connection.db_type != DatabaseType.MONGODB or not database_name.strip():
        return 0
    info = connection.to_connection_info()
    connector = get_connector_from_info(connection.db_type, info)
    client = connector._get_client()
    try:
        target = database_name.strip()
        for db_info in client.list_databases():
            if db_info.get("name") == target:
                return int(db_info.get("sizeOnDisk") or 0)
        return 0
    finally:
        client.close()


def fetch_mongodb_database_overview(
    connection: ConnectionResult, database_name: str
) -> dict[str, Any]:
    """dbStats plus per-collection counts for the Databases page."""
    if connection.db_type != DatabaseType.MONGODB:
        raise ValueError("A MongoDB connection is required.")
    name = database_name.strip()
    if not name:
        raise ValueError("Database name is required.")

    info = connection.to_connection_info()
    connector = get_connector_from_info(connection.db_type, info)
    client = connector._get_client()
    try:
        db = client[name]
        stats = db.command("dbStats")
        version = ""
        try:
            version = str(client.server_info().get("version") or "")
        except Exception:
            version = ""
        collections: list[dict[str, Any]] = []
        try:
            for doc in db.aggregate([{"$collStats": {"storageStats": {}, "count": {}}}]):
                storage = doc.get("storageStats") or {}
                count_doc = doc.get("count") or {}
                ns = str(doc.get("ns") or "")
                coll_name = ns.split(".", 1)[1] if "." in ns else ns
                collections.append(
                    {
                        "name": coll_name or "—",
                        "count": int(count_doc.get("count") or storage.get("count") or 0),
                        "size": int(storage.get("size") or storage.get("storageSize") or 0),
                        "indexes": int(storage.get("nindexes") or 0),
                    }
                )
        except Exception:
            for coll_name in sorted(db.list_collection_names()):
                try:
                    count = int(db[coll_name].estimated_document_count())
                except Exception:
                    count = 0
                collections.append(
                    {"name": coll_name, "count": count, "size": 0, "indexes": 0}
                )
        collections.sort(key=lambda row: (-int(row["size"]), str(row["name"]).lower()))
        return {"stats": stats, "collections": collections, "version": version}
    finally:
        client.close()


def fetch_mongodb_collections(
    connection: ConnectionResult, database_name: str
) -> list[dict[str, Any]]:
    """Return collections in a MongoDB database with estimated document counts."""
    if connection.db_type != DatabaseType.MONGODB:
        return []
    if not database_name.strip():
        raise ValueError("Database name is required to list collections.")

    info = connection.to_connection_info()
    connector = get_connector_from_info(connection.db_type, info)
    return connector.list_collections(database_name.strip())


def fetch_postgresql_schemas(
    connection: ConnectionResult, database_name: str
) -> list[str]:
    """Return non-system schema names from a PostgreSQL database."""
    if connection.db_type != DatabaseType.POSTGRESQL:
        return []
    db = database_name.strip()
    if not db:
        raise ValueError("Database name is required to list schemas.")

    from dataclasses import replace

    scoped = replace(connection, database=db)
    connector = get_connector_from_info(scoped.db_type, scoped.to_connection_info())
    rows = connector.execute_query(
        """
        SELECT nspname AS name
        FROM pg_catalog.pg_namespace
        WHERE nspname NOT IN ('pg_catalog', 'information_schema')
          AND nspname NOT LIKE 'pg_toast%'
          AND nspname NOT LIKE 'pg_temp_%'
        ORDER BY nspname
        """
    )
    return [str(row["name"]) for row in rows if row.get("name")]


def fetch_postgresql_tables(
    connection: ConnectionResult,
    database_name: str,
    schema: str | None = None,
) -> list[str]:
    """Return table names from a PostgreSQL database.

    When ``schema`` is set, returns unqualified table names in that schema.
    Otherwise returns schema-qualified ``schema.table`` names.
    """
    if connection.db_type != DatabaseType.POSTGRESQL:
        return []
    db = database_name.strip()
    if not db:
        raise ValueError("Database name is required to list tables.")

    from dataclasses import replace

    scoped = replace(connection, database=db)
    connector = get_connector_from_info(scoped.db_type, scoped.to_connection_info())
    schema_name = (schema or "").strip()
    if schema_name:
        rows = connector.execute_query(
            """
            SELECT tablename AS name
            FROM pg_catalog.pg_tables
            WHERE schemaname = %s
            ORDER BY tablename
            """,
            (schema_name,),
        )
        return [str(row["name"]) for row in rows if row.get("name")]

    rows = connector.execute_query(
        """
        SELECT schemaname || '.' || tablename AS name
        FROM pg_catalog.pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY schemaname, tablename
        """
    )
    return [str(row["name"]) for row in rows if row.get("name")]


def fetch_mysql_tables(connection: ConnectionResult, database_name: str) -> list[str]:
    """Return table names from a MySQL database for backup scope selection."""
    if connection.db_type != DatabaseType.MYSQL:
        return []
    db = database_name.strip()
    if not db:
        raise ValueError("Database name is required to list tables.")

    from dataclasses import replace

    scoped = replace(connection, database=db)
    try:
        connector = get_connector_from_info(scoped.db_type, scoped.to_connection_info())
        rows = connector.execute_query(
            """
            SELECT TABLE_NAME AS name
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """,
            (db,),
        )
        names = [str(row["name"]) for row in rows if row.get("name")]
        if names:
            return names
    except Exception:
        names = []

    # Fallback: privileged SHOW TABLES over SSH (same path as full DB inventory).
    if connection.ssh_enabled and connection.linux_username:
        import shlex

        from backend.services.ssh_client import run_ssh_command
        from desktop.explorer.mysql_inventory import _ssh_params

        params = _ssh_params(connection)
        if not params:
            return names
        db_q = shlex.quote(db)
        commands = [
            f"mysql -Nse {shlex.quote(f'SHOW TABLES FROM {db}')}",
            f"sudo -n mysql -Nse {shlex.quote(f'SHOW TABLES FROM {db}')}",
        ]
        if params.get("password"):
            pw = shlex.quote(params["password"])
            commands.append(
                f"printf '%s\\n' {pw} | sudo -S -p '' mysql -Nse "
                f"{shlex.quote(f'SHOW TABLES FROM {db}')}"
            )
        for command in commands:
            try:
                code, out, _err = run_ssh_command(
                    ssh_host=params["ssh_host"],
                    fallback_host=params["fallback_host"],
                    ssh_port=params["ssh_port"],
                    username=params["username"],
                    password=params["password"],
                    command=command,
                    timeout=45,
                )
            except Exception:
                continue
            if code != 0 or not (out or "").strip():
                continue
            found = [
                line.strip()
                for line in out.splitlines()
                if line.strip() and " " not in line.strip()
            ]
            if found:
                return found
    return names
