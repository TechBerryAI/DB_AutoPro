"""Database vendor metadata for the connection dialog."""

from __future__ import annotations

from backend.models.schemas import DatabaseType

DB_ICON_META: dict[DatabaseType, dict[str, str]] = {
    DatabaseType.POSTGRESQL: {"abbrev": "PG", "color": "#336791", "label": "PostgreSQL"},
    DatabaseType.MYSQL: {"abbrev": "MY", "color": "#00758F", "label": "MySQL"},
    DatabaseType.ORACLE: {"abbrev": "OR", "color": "#C74634", "label": "Oracle"},
    DatabaseType.MONGODB: {"abbrev": "MG", "color": "#47A248", "label": "MongoDB"},
    DatabaseType.MSSQL: {"abbrev": "MS", "color": "#CC2927", "label": "MSSQL"},
}
