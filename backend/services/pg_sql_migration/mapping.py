"""Map PostgreSQL columns onto SQL Server or Oracle types."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal

from backend.services.migration_framework.registry import MSSQL, ORACLE, _norm

_MSSQL_SIMPLE = {
    "smallint": "SMALLINT",
    "int2": "SMALLINT",
    "integer": "INT",
    "int": "INT",
    "int4": "INT",
    "bigint": "BIGINT",
    "int8": "BIGINT",
    "real": "REAL",
    "float4": "REAL",
    "double precision": "FLOAT",
    "float8": "FLOAT",
    "boolean": "BIT",
    "bool": "BIT",
    "text": "NVARCHAR(MAX)",
    "bytea": "VARBINARY(MAX)",
    "date": "DATE",
    "uuid": "UNIQUEIDENTIFIER",
    "json": "NVARCHAR(MAX)",
    "jsonb": "NVARCHAR(MAX)",
    "timestamp without time zone": "DATETIME2(6)",
    "timestamp": "DATETIME2(6)",
    "time without time zone": "TIME(6)",
    "time": "TIME(6)",
}

_ORACLE_SIMPLE = {
    "smallint": "NUMBER(5)",
    "int2": "NUMBER(5)",
    "integer": "NUMBER(10)",
    "int": "NUMBER(10)",
    "int4": "NUMBER(10)",
    "bigint": "NUMBER(19)",
    "int8": "NUMBER(19)",
    "real": "BINARY_FLOAT",
    "float4": "BINARY_FLOAT",
    "double precision": "BINARY_DOUBLE",
    "float8": "BINARY_DOUBLE",
    "boolean": "NUMBER(1)",
    "bool": "NUMBER(1)",
    "text": "CLOB",
    "bytea": "BLOB",
    "date": "DATE",
    "uuid": "VARCHAR2(36)",
    "json": "CLOB",
    "jsonb": "CLOB",
    "timestamp without time zone": "TIMESTAMP(6)",
    "timestamp": "TIMESTAMP(6)",
    "time without time zone": "VARCHAR2(32)",
    "time": "VARCHAR2(32)",
}

_LOSSY = {
    (MSSQL, "boolean"): "BOOLEAN is stored as BIT.",
    (MSSQL, "bool"): "BOOLEAN is stored as BIT.",
    (MSSQL, "json"): "JSON is stored as NVARCHAR(MAX).",
    (MSSQL, "jsonb"): "JSONB is stored as NVARCHAR(MAX).",
    (MSSQL, "timestamp with time zone"): "Values are stored as DATETIMEOFFSET(6).",
    (MSSQL, "timestamptz"): "Values are stored as DATETIMEOFFSET(6).",
    (ORACLE, "boolean"): "BOOLEAN is stored as NUMBER(1).",
    (ORACLE, "bool"): "BOOLEAN is stored as NUMBER(1).",
    (ORACLE, "json"): "JSON is stored as CLOB.",
    (ORACLE, "jsonb"): "JSONB is stored as CLOB.",
    (ORACLE, "uuid"): "UUID values are stored as VARCHAR2(36).",
    (ORACLE, "time without time zone"): "Oracle has no TIME type. Values are stored as VARCHAR2(32).",
    (ORACLE, "time"): "Oracle has no TIME type. Values are stored as VARCHAR2(32).",
}


def quote_ident(kind: str, name: str) -> str:
    text = str(name or "")
    if not text or "\x00" in text:
        raise ValueError("Invalid identifier")
    if _norm(kind) == MSSQL:
        return "[" + text.replace("]", "]]") + "]"
    return '"' + text.replace('"', '""') + '"'


def placeholder(kind: str, index: int) -> str:
    if _norm(kind) == MSSQL:
        return "?"
    return f":{index}"


def map_column(kind: str, column: dict) -> tuple[str | None, str | None, str | None]:
    kind = _norm(kind)
    data_type = str(column.get("data_type") or "").lower()
    udt = str(column.get("udt_name") or "").lower()
    if data_type in {"array", "user-defined"} or udt.startswith("_"):
        return None, None, f"{data_type or udt} cannot be mapped to a {kind} column type"
    key = data_type or udt
    if kind == MSSQL and key in {"timestamp with time zone", "timestamptz"}:
        return "DATETIMEOFFSET(6)", _LOSSY[(MSSQL, key)], None
    if kind == ORACLE and key in {"timestamp with time zone", "timestamptz"}:
        return "TIMESTAMP(6) WITH TIME ZONE", None, None
    table = _MSSQL_SIMPLE if kind == MSSQL else _ORACLE_SIMPLE
    if key in table:
        return table[key], _LOSSY.get((kind, key)), None
    if key in {"character varying", "varchar"}:
        length = column.get("character_maximum_length")
        if length is None:
            return ("NVARCHAR(MAX)" if kind == MSSQL else "CLOB"), None, None
        length = int(length)
        if length < 1:
            return None, None, "character varying length is invalid"
        if kind == MSSQL:
            if length > 4000:
                return "NVARCHAR(MAX)", f"VARCHAR({length}) is mapped to NVARCHAR(MAX).", None
            return f"NVARCHAR({length})", None, None
        if length > 4000:
            return "CLOB", f"VARCHAR({length}) is mapped to CLOB.", None
        return f"VARCHAR2({length})", None, None
    if key in {"character", "char", "bpchar"}:
        length = int(column.get("character_maximum_length") or 1)
        if length < 1:
            return None, None, f"CHAR({length}) is not mapped"
        if kind == MSSQL:
            if length > 4000:
                return None, None, f"CHAR({length}) is not mapped"
            return f"NCHAR({length})", None, None
        if length > 2000:
            return None, None, f"CHAR({length}) is not mapped"
        return f"CHAR({length})", None, None
    if key in {"numeric", "decimal"}:
        precision = column.get("numeric_precision")
        scale = column.get("numeric_scale")
        if precision is None:
            return ("DECIMAL(38,10)" if kind == MSSQL else "NUMBER"), "Unbounded NUMERIC is stored with a fixed precision.", None
        precision = int(precision)
        scale = int(scale or 0)
        if precision < 1 or scale < 0 or scale > precision:
            return None, None, f"DECIMAL({precision},{scale}) is outside the supported range"
        if kind == MSSQL:
            if precision > 38:
                return None, None, f"DECIMAL({precision},{scale}) exceeds SQL Server DECIMAL precision"
            return f"DECIMAL({precision},{scale})", None, None
        if precision > 38:
            return None, None, f"NUMBER({precision},{scale}) exceeds Oracle precision"
        return f"NUMBER({precision},{scale})", None, None
    return None, None, f"{key or 'unknown'} cannot be mapped to a {kind} column type"


def adapt_value(value: object, target_type: str) -> object:
    if value is None:
        return None
    kind = target_type.upper()
    if isinstance(value, (memoryview, bytearray)):
        value = bytes(value)
    if kind in {"BIT", "NUMBER(1)"} and isinstance(value, bool):
        return 1 if value else 0
    if kind == "UNIQUEIDENTIFIER" and isinstance(value, uuid.UUID):
        return str(value)
    if kind.startswith("VARCHAR2") and isinstance(value, uuid.UUID):
        return str(value)
    if kind in {"NVARCHAR(MAX)", "CLOB"} and not isinstance(value, (bytes, bytearray)):
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str)
    if kind in {"VARBINARY(MAX)", "BLOB"} and isinstance(value, str):
        return value.encode("utf-8")
    if "FLOAT" in kind or kind in {"REAL", "BINARY_FLOAT", "BINARY_DOUBLE"}:
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError(f"{target_type} cannot store the PostgreSQL value {value}.")
    if kind.startswith(("DECIMAL", "NUMBER")) and isinstance(value, Decimal) and not value.is_finite():
        raise ValueError(f"{target_type} cannot store the PostgreSQL value {value}.")
    if kind.startswith("DATETIME2") and isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    if kind.startswith("VARCHAR2") and isinstance(value, dt.time):
        return value.isoformat()
    return value
