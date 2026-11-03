"""Convert PostgreSQL row values into BSON-safe document fields."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal


def adapt_mongo(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (memoryview, bytearray)):
        value = bytes(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"MongoDB cannot store the PostgreSQL value {value}.")
        try:
            from bson.decimal128 import Decimal128

            return Decimal128(value)
        except Exception:
            return str(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(f"MongoDB cannot store the PostgreSQL value {value}.")
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            return value.astimezone(dt.timezone.utc)
        return value
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            from bson.binary import Binary

            return Binary(value)
        except Exception:
            return value
    return value


def row_document(columns: list[str], row, primary_key: list[str]) -> dict:
    document = {name: adapt_mongo(value) for name, value in zip(columns, row)}
    if len(primary_key) == 1:
        key = primary_key[0]
        if key in document and document[key] is not None:
            document["_id"] = document[key]
    elif len(primary_key) > 1:
        ident = {name: document.get(name) for name in primary_key if name in document}
        if ident and all(value is not None for value in ident.values()):
            document["_id"] = ident
    return document
