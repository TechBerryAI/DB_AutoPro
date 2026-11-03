"""Map source column types to PostgreSQL types for a reviewed copy."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal

MYSQL_TYPES = {
    "tinyint": "SMALLINT",
    "smallint": "SMALLINT",
    "mediumint": "INTEGER",
    "int": "INTEGER",
    "integer": "INTEGER",
    "bigint": "BIGINT",
    "float": "REAL",
    "double": "DOUBLE PRECISION",
    "double precision": "DOUBLE PRECISION",
    "decimal": "NUMERIC",
    "numeric": "NUMERIC",
    "bit": "BOOLEAN",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    "time": "TIME",
    "year": "SMALLINT",
    "char": "CHAR",
    "varchar": "VARCHAR",
    "tinytext": "TEXT",
    "text": "TEXT",
    "mediumtext": "TEXT",
    "longtext": "TEXT",
    "json": "JSONB",
    "tinyblob": "BYTEA",
    "blob": "BYTEA",
    "mediumblob": "BYTEA",
    "longblob": "BYTEA",
    "binary": "BYTEA",
    "varbinary": "BYTEA",
    "uuid": "UUID",
    "enum": "TEXT",
    "set": "TEXT",
}

MSSQL_TYPES = {
    "bit": "BOOLEAN",
    "tinyint": "SMALLINT",
    "smallint": "SMALLINT",
    "int": "INTEGER",
    "bigint": "BIGINT",
    "real": "REAL",
    "float": "DOUBLE PRECISION",
    "decimal": "NUMERIC",
    "numeric": "NUMERIC",
    "money": "NUMERIC",
    "smallmoney": "NUMERIC",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP",
    "datetimeoffset": "TIMESTAMPTZ",
    "time": "TIME",
    "char": "CHAR",
    "varchar": "VARCHAR",
    "nchar": "CHAR",
    "nvarchar": "VARCHAR",
    "text": "TEXT",
    "ntext": "TEXT",
    "uniqueidentifier": "UUID",
    "binary": "BYTEA",
    "varbinary": "BYTEA",
    "image": "BYTEA",
    "xml": "TEXT",
    "sysname": "VARCHAR",
}

ORACLE_TYPES = {
    "number": "NUMERIC",
    "float": "DOUBLE PRECISION",
    "binary_float": "REAL",
    "binary_double": "DOUBLE PRECISION",
    "varchar2": "VARCHAR",
    "nvarchar2": "VARCHAR",
    "char": "CHAR",
    "nchar": "CHAR",
    "clob": "TEXT",
    "nclob": "TEXT",
    "long": "TEXT",
    "blob": "BYTEA",
    "raw": "BYTEA",
    "long raw": "BYTEA",
    "date": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMPTZ",
    "timestamp with local time zone": "TIMESTAMPTZ",
    "xmltype": "TEXT",
}


def quote_pg(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("Invalid PostgreSQL identifier")
    return '"' + name.replace('"', '""') + '"'


def map_column(kind: str, data_type: str, *, length=None, precision=None, scale=None) -> tuple[str | None, str | None, str | None]:
    """Return PostgreSQL type, lossy note, and a blocking error."""
    from backend.services.migration_framework.registry import _norm

    kind = _norm(kind)
    key = str(data_type or "").strip().lower().split("(")[0].strip()
    table = {"mysql": MYSQL_TYPES, "mssql": MSSQL_TYPES, "oracle": ORACLE_TYPES}.get(kind, {})
    if key not in table:
        return None, None, f"{data_type or 'unknown'} cannot be mapped to a PostgreSQL column type"
    pg_type = table[key]
    lossy = None
    if key in {"tinyint", "year"} and kind == "mysql":
        lossy = f"{key} becomes {pg_type}."
    if pg_type in {"VARCHAR", "CHAR"} and length:
        try:
            size = int(length)
        except (TypeError, ValueError):
            size = 0
        if size > 0:
            pg_type = f"{pg_type}({size})"
    if pg_type == "NUMERIC" and precision:
        try:
            prec = int(precision)
            sc = int(scale or 0)
            pg_type = f"NUMERIC({prec},{sc})"
        except (TypeError, ValueError):
            pass
    if key in {"datetimeoffset", "timestamp with time zone"}:
        lossy = "Time zone values are stored as TIMESTAMPTZ."
    if key in {"xml", "xmltype"}:
        lossy = "XML is stored as TEXT."
    if key in {"enum", "set"}:
        lossy = f"{key} is stored as TEXT."
    return pg_type, lossy, None


def adapt_value(value: object, pg_type: str) -> object:
    if value is None:
        return None
    if isinstance(value, (memoryview, bytearray)):
        return bytes(value)
    if pg_type.startswith("JSONB"):
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("utf-8")
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str)
    if pg_type == "UUID" and isinstance(value, uuid.UUID):
        return str(value)
    if pg_type == "BOOLEAN":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int,)):
            return bool(value)
        if isinstance(value, (bytes, bytearray)) and value:
            return value[0] != 0
    if isinstance(value, Decimal):
        return value
    if isinstance(value, dt.datetime) and value.tzinfo is not None and pg_type == "TIMESTAMP":
        return value.replace(tzinfo=None)
    return value
