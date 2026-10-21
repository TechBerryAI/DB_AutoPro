"""List accessible databases on a live connection. This is not a migration."""

from __future__ import annotations

from backend.services.migration_framework.registry import MYSQL, POSTGRESQL, _norm

_MYSQL_SYSTEM = {"information_schema", "performance_schema", "mysql", "sys"}
_PG_SYSTEM = {"template0", "template1"}


def list_accessible_databases(db_type: str, query) -> list[str]:
    """Return database names the current connection can see.

    `query` is a read callback(sql) -> list[dict]. Failures return an empty list.
    """
    kind = _norm(db_type)
    try:
        if kind == POSTGRESQL:
            rows = query(
                "SELECT datname AS name FROM pg_database "
                "WHERE datistemplate = false ORDER BY 1"
            )
            return _names(rows, skip=_PG_SYSTEM)
        if kind == MYSQL:
            rows = query("SHOW DATABASES")
            return _names(rows, skip=_MYSQL_SYSTEM)
    except Exception:
        return []
    return []


def _names(rows: list[dict] | None, *, skip: set[str]) -> list[str]:
    found: list[str] = []
    for row in rows or []:
        value = ""
        for key in ("name", "datname", "Database", "database", "SCHEMA_NAME", "schema_name"):
            if row.get(key):
                value = str(row.get(key)).strip()
                break
        if not value and len(row) == 1:
            value = str(next(iter(row.values())) or "").strip()
        if value and value not in skip and value not in found:
            found.append(value)
    return found
