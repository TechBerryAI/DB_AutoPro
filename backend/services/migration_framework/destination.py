"""Name and create the destination copy. This is not a user/role/file migration."""

from __future__ import annotations

import re

from backend.services.migration_framework.registry import MYSQL, POSTGRESQL, _norm
from backend.services.pg_mysql_migration.mapping import quote_mysql

_MYSQL_SAFE = re.compile(r"^[A-Za-z0-9_$]{1,64}$")
_MYSQL_SYSTEM = {"information_schema", "mysql", "performance_schema", "sys"}
_PG_SYSTEM = {"template0", "template1"}


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
    return None


def server_probe_database(db_type: str) -> str:
    """Catalog used only to reach the server. It is not the destination copy."""
    if _norm(db_type) == MYSQL:
        return ""
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
