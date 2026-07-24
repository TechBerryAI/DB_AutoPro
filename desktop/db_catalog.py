"""List databases available on a connected database server."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import get_connector_from_info
from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult
from desktop.ssh_tunnel import resolve_active_connection


def fetch_server_databases(connection: ConnectionResult) -> list[dict[str, Any]]:
    """Return databases on the server with name, owner, size, and status."""
    connection = resolve_active_connection(connection)
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
        rows = connector.execute_query(
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
        return [
            {
                "name": row["name"],
                "owner": row.get("encoding") or "—",
                "size": f"{row['size_mb']:.1f} MB" if row.get("size_mb") else "—",
                "status": "online",
            }
            for row in rows
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
        rows = connector.execute_query(
            """
            SELECT username AS name, created, account_status AS status
            FROM all_users
            ORDER BY username
            """
        )
        return [
            {
                "name": row["name"],
                "owner": "—",
                "size": "—",
                "status": (row.get("status") or "online").lower(),
            }
            for row in rows
        ]

    return []


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
