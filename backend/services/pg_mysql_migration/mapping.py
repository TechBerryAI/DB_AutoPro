"""Explicit PostgreSQL-to-MySQL type and identifier mapping."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from decimal import Decimal

_SIMPLE_TYPES = {
    "smallint": "SMALLINT",
    "int2": "SMALLINT",
    "integer": "INT",
    "int": "INT",
    "int4": "INT",
    "bigint": "BIGINT",
    "int8": "BIGINT",
    "real": "FLOAT",
    "float4": "FLOAT",
    "double precision": "DOUBLE",
    "float8": "DOUBLE",
    "boolean": "TINYINT(1)",
    "bool": "TINYINT(1)",
    "text": "TEXT",
    "bytea": "BLOB",
    "date": "DATE",
    "uuid": "CHAR(36)",
    "json": "JSON",
    "jsonb": "JSON",
    "timestamp without time zone": "DATETIME(6)",
    "timestamp": "DATETIME(6)",
    "time without time zone": "TIME(6)",
}

_LOSSY = {
    "boolean": "MySQL stores BOOLEAN as TINYINT(1); true and false become 1 and 0.",
    "bool": "MySQL stores BOOLEAN as TINYINT(1); true and false become 1 and 0.",
    "timestamp with time zone": "The time zone is not stored. Values are written as UTC DATETIME(6).",
    "timestamptz": "The time zone is not stored. Values are written as UTC DATETIME(6).",
    "jsonb": "JSONB is written as MySQL JSON. Key order and duplicate-key handling can differ.",
    "uuid": "UUID values are written as 36-character strings.",
}

_STRING_DEFAULT = re.compile(r"^'(?P<body>(?:''|[^'])*)'(?:::[\w ]+)?$")
_NUMERIC_DEFAULT = re.compile(r"^-?\d+(?:\.\d+)?$")


def quote_mysql(name: str) -> str:
    """Quote a MySQL identifier. The result contains only the escaped name."""
    if not isinstance(name, str) or not name or "\x00" in name or len(name) > 64:
        raise ValueError("Invalid MySQL identifier")
    return "`" + name.replace("`", "``") + "`"


def map_column(column: dict) -> tuple[str | None, str | None, str | None]:
    """Return MySQL type, lossy note, and a blocking error. One of type or error is set."""
    data_type = str(column.get("data_type") or "").lower()
    udt = str(column.get("udt_name") or "").lower()
    if data_type in {"array", "user-defined"} or udt.startswith("_"):
        return None, None, f"{data_type or udt} cannot be mapped to a MySQL column type"
    key = data_type or udt
    if key in {"timestamp with time zone", "timestamptz"}:
        return "DATETIME(6)", _LOSSY[key], None
    if key in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[key], _LOSSY.get(key), None
    if key in {"character varying", "varchar"}:
        length = column.get("character_maximum_length")
        if length is None:
            return "TEXT", None, None
        length = int(length)
        if length < 1:
            return None, None, "character varying length is invalid"
        if length > 16383:
            return "TEXT", f"VARCHAR({length}) exceeds the MySQL row limit and is mapped to TEXT.", None
        return f"VARCHAR({length})", None, None
    if key in {"character", "char", "bpchar"}:
        length = int(column.get("character_maximum_length") or 1)
        if length < 1 or length > 255:
            return None, None, f"CHAR({length}) is not mapped"
        return f"CHAR({length})", None, None
    if key in {"numeric", "decimal"}:
        precision = column.get("numeric_precision")
        scale = column.get("numeric_scale")
        if precision is None:
            return None, None, "numeric without precision is not mapped, because MySQL DECIMAL cannot hold an unbounded value"
        precision = int(precision)
        scale = int(scale or 0)
        if precision < 1 or precision > 65 or scale < 0 or scale > 30 or scale > precision:
            return None, None, f"DECIMAL({precision},{scale}) is outside the MySQL DECIMAL range"
        return f"DECIMAL({precision},{scale})", None, None
    return None, None, f"{key or 'unknown'} cannot be mapped to a MySQL column type"


def translate_default(default: object, *, mysql_type: str, auto_increment: bool) -> tuple[str | None, str | None]:
    """Return a MySQL default expression, or an error when the expression would be dropped."""
    if auto_increment or default is None:
        return None, None
    text = str(default).strip()
    if text.lower().startswith("nextval("):
        return None, None
    if text.lower() in {"true", "false"}:
        return ("1" if text.lower() == "true" else "0"), None
    if text.lower() in {"now()", "current_timestamp"}:
        if mysql_type.startswith(("DATETIME", "TIME", "DATE")):
            return "CURRENT_TIMESTAMP", None
        return None, f"default {text} is not valid for {mysql_type}"
    numeric = _NUMERIC_DEFAULT.fullmatch(text)
    if numeric:
        return text, None
    literal = _STRING_DEFAULT.fullmatch(text)
    if literal:
        body = literal.group("body").replace("''", "'").replace("\\", "\\\\").replace("'", "''")
        return f"'{body}'", None
    return None, f"default {text} cannot be translated to MySQL"


def adapt_value(value: object, mysql_type: str) -> object:
    """Convert one Python value for a parameterized MySQL insert."""
    if value is None:
        return None
    if mysql_type == "TINYINT(1)" and isinstance(value, bool):
        return 1 if value else 0
    if mysql_type == "JSON":
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)
    if mysql_type == "CHAR(36)" and isinstance(value, uuid.UUID):
        return str(value)
    if mysql_type.startswith("DATETIME") and isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    if isinstance(value, Decimal):
        return value
    return value
