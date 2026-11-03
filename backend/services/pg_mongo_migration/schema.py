"""Read PostgreSQL public tables that become MongoDB collections."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CollectionSpec:
    name: str
    columns: list[str] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    blocked: str = ""
    conversions: list[str] = field(default_factory=list)


def load_collection(source_query, name: str) -> CollectionSpec:
    literal = "'" + name.replace("'", "''") + "'"
    rows = source_query(
        "SELECT column_name, data_type, udt_name FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = {literal} ORDER BY ordinal_position"
    )
    spec = CollectionSpec(name=name)
    if not rows:
        spec.blocked = f"Table {name} has no columns in schema public."
        return spec
    for row in rows:
        column = str(row.get("column_name") or "")
        data_type = str(row.get("data_type") or "").lower()
        udt = str(row.get("udt_name") or "").lower()
        if not column:
            spec.blocked = f"Table {name} has an unnamed column."
            return spec
        if data_type in {"user-defined"} or (udt.startswith("_") and data_type not in {"array", "json", "jsonb"}):
            spec.blocked = f"{name}.{column} ({data_type or udt}) cannot be stored as a MongoDB document field."
            return spec
        if data_type in {"json", "jsonb"}:
            spec.conversions.append(f"{name}.{column}: {data_type} is stored as a MongoDB document field.")
        if data_type == "uuid":
            spec.conversions.append(f"{name}.{column}: UUID is stored as a string.")
        if data_type in {"bytea"}:
            spec.conversions.append(f"{name}.{column}: bytea is stored as binary.")
        spec.columns.append(column)
    spec.primary_key = _primary_key(source_query, name)
    if spec.primary_key:
        spec.conversions.append(f"{name}: primary key becomes MongoDB _id.")
    else:
        spec.conversions.append(f"{name}: no primary key. MongoDB will assign _id for each document.")
    return spec


def _primary_key(source_query, table: str) -> list[str]:
    literal = "'" + table.replace("'", "''") + "'"
    rows = source_query(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
        f"AND kcu.table_name = tc.table_name WHERE tc.table_schema = 'public' AND tc.table_name = {literal} "
        "AND tc.constraint_type = 'PRIMARY KEY' ORDER BY kcu.ordinal_position"
    )
    return [str(row.get("column_name")) for row in rows or [] if row.get("column_name")]
