"""Name and create the destination copy. This is not a user/role/file migration."""

from __future__ import annotations

import re

from backend.services.migration_framework.registry import MONGODB, MSSQL, MYSQL, ORACLE, POSTGRESQL, _norm
from backend.services.pg_mysql_migration.mapping import quote_mysql

_MYSQL_SAFE = re.compile(r"^[A-Za-z0-9_$]{1,64}$")
_MSSQL_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ORACLE_SERVICE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#.]{0,127}$")
_MONGO_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MYSQL_SYSTEM = {"information_schema", "mysql", "performance_schema", "sys"}
_PG_SYSTEM = {"template0", "template1"}
_MSSQL_SYSTEM = {"master", "tempdb", "model", "msdb"}
_MONGO_SYSTEM = {"admin", "local", "config"}


def destination_copy_name(source_database: str, target_type: str) -> str:
    """Return the name the target server will use for a converted copy of the source."""
    name = (source_database or "").strip()
    if not name:
        return ""
    kind = _norm(target_type)
    if kind == MYSQL:
        cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", name)
        if not cleaned:
            return ""
        if cleaned[0].isdigit():
            cleaned = f"db_{cleaned}"
        cleaned = cleaned[:64]
        if cleaned.lower() in _MYSQL_SYSTEM:
            cleaned = f"{cleaned}_copy"[:64]
        return cleaned
    if kind == MSSQL:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if not cleaned:
            return ""
        if cleaned[0].isdigit():
            cleaned = f"db_{cleaned}"
        cleaned = cleaned[:128]
        if cleaned.lower() in _MSSQL_SYSTEM:
            cleaned = f"{cleaned}_copy"[:128]
        return cleaned
    if kind == MONGODB:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if not cleaned:
            return ""
        if cleaned[0].isdigit():
            cleaned = f"db_{cleaned}"
        cleaned = cleaned[:64]
        if cleaned.lower() in _MONGO_SYSTEM:
            cleaned = f"{cleaned}_copy"[:64]
        return cleaned
    return name


def destination_name_error(name: str, target_type: str) -> str | None:
    text = (name or "").strip()
    if not text:
        return "Enter the destination copy name. It is created on the target server from the source database."
    kind = _norm(target_type)
    if kind == MYSQL:
        if not _MYSQL_SAFE.match(text):
            return (
                "The MySQL destination name may contain only letters, digits, underscore, and $. "
                "No database was created."
            )
        if text.lower() in _MYSQL_SYSTEM:
            return f"{text} is a MySQL system database and cannot be the destination copy."
    if kind == POSTGRESQL and text.lower() in _PG_SYSTEM:
        return f"{text} is a PostgreSQL template database and cannot be the destination copy."
    if kind == MSSQL:
        if not _MSSQL_SAFE.match(text):
            return (
                "The SQL Server destination name may contain only letters, digits, and underscore, "
                "and must start with a letter or underscore. No database was created."
            )
        if text.lower() in _MSSQL_SYSTEM:
            return f"{text} is a SQL Server system database and cannot be the destination copy."
    if kind == ORACLE and not _ORACLE_SERVICE.match(text):
        return (
            "Enter the Oracle service name. This migration writes into the connected schema "
            "and does not create a PDB or service."
        )
    if kind == MONGODB:
        if not _MONGO_SAFE.match(text):
            return (
                "The MongoDB destination name may contain only letters, digits, and underscore, "
                "and must start with a letter or underscore. No database was created."
            )
        if text.lower() in _MONGO_SYSTEM:
            return f"{text} is a MongoDB system database and cannot be the destination copy."
    return None


def server_probe_database(db_type: str) -> str:
    """Catalog used only to reach the server. It is not the destination copy."""
    kind = _norm(db_type)
    if kind in {MYSQL, MONGODB, ORACLE}:
        return ""
    if kind == MSSQL:
        return "master"
    return "postgres"


def mysql_destination_exists(query, name: str) -> bool:
    wanted = (name or "").strip().casefold()
    if not wanted:
        return False
    try:
        rows = query("SHOW DATABASES")
    except Exception:
        return False
    for row in rows or []:
        value = str(next(iter(row.values()), "") or "").strip()
        if value.casefold() == wanted:
            return True
    return False


def create_mysql_destination(execute, name: str) -> str:
    """Create an empty utf8mb4 database. Does not copy users, files, views, or roles."""
    error = destination_name_error(name, MYSQL)
    if error:
        raise ValueError(error)
    execute(
        f"CREATE DATABASE IF NOT EXISTS {quote_mysql(name)} "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    return name


def quote_mssql(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("Invalid SQL Server identifier")
    return "[" + name.replace("]", "]]") + "]"


def mssql_destination_exists(query, name: str) -> bool:
    wanted = (name or "").strip()
    if not wanted:
        return False
    try:
        rows = query("SELECT name FROM sys.databases WHERE name = '" + wanted.replace("'", "''") + "'")
    except Exception:
        return False
    return bool(rows)


def create_mssql_destination(execute, name: str) -> str:
    """Create an empty SQL Server database. Does not copy users, files, views, or roles."""
    error = destination_name_error(name, MSSQL)
    if error:
        raise ValueError(error)
    escaped = name.replace("'", "''")
    execute(f"IF DB_ID(N'{escaped}') IS NULL CREATE DATABASE {quote_mssql(name)}")
    return name


def create_postgresql_destination(execute, name: str) -> str:
    error = destination_name_error(name, POSTGRESQL)
    if error:
        raise ValueError(error)
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError("The PostgreSQL destination name is not a safe identifier. No database was created.")
    execute(f'CREATE DATABASE "{name.replace(chr(34), "")}"')
    return name


def destination_identity_error(
    expected: str,
    observed: str,
    *,
    dest_exists: bool | None,
    discovered: list[str] | None = None,
) -> str | None:
    """Fail only when the opened database is the wrong existing destination."""
    wanted = (expected or "").strip()
    seen = (observed or "").strip()
    names = {item.strip() for item in (discovered or []) if item and item.strip()}
    if not wanted:
        return "Enter the destination copy name. No schema or data was copied."
    if dest_exists is False:
        return None
    if wanted in names:
        return None
    if seen and seen != wanted:
        return (
            f"The connected target database is {seen}, not {wanted}. "
            "The migration was not started."
        )
    return None
