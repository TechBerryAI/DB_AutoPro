"""Read a PostgreSQL public schema and render MySQL DDL. Catalog SQL stays on the source."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.services.pg_migration.precheck import quote_ident
from backend.services.pg_mysql_migration.mapping import map_column, quote_mysql, translate_default


@dataclass
class ForeignKey:
    name: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    on_delete: str
    on_update: str


@dataclass
class ColumnSpec:
    name: str
    mysql_type: str
    nullable: bool
    default_sql: str | None
    auto_increment: bool
    lossy: str | None = None


@dataclass
class TableSpec:
    name: str
    columns: list[ColumnSpec] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    uniques: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    indexes: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    blocked: str = ""
    conversions: list[str] = field(default_factory=list)


def quote_pg_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_table_sql(table: TableSpec, *, compressed: bool = False) -> str:
    parts: list[str] = []
    for column in table.columns:
        null_sql = "NULL" if column.nullable else "NOT NULL"
        default_sql = ""
        if column.default_sql:
            expression = column.default_sql
            # MySQL accepts a default on TEXT, BLOB, and JSON only as a parenthesized expression.
            if _is_large_type(column.mysql_type) and not expression.startswith("("):
                expression = f"({expression})"
            default_sql = f" DEFAULT {expression}"
        auto_sql = " AUTO_INCREMENT" if column.auto_increment else ""
        parts.append(f"{quote_mysql(column.name)} {column.mysql_type} {null_sql}{default_sql}{auto_sql}")
    if table.primary_key:
        parts.append("PRIMARY KEY (" + ", ".join(quote_mysql(name) for name in table.primary_key) + ")")
    for name, columns in table.uniques:
        parts.append(
            f"UNIQUE KEY {quote_mysql(name)} (" + ", ".join(quote_mysql(column) for column in columns) + ")"
        )
    return (
        f"CREATE TABLE {quote_mysql(table.name)} ("
        + ", ".join(parts)
        + ") ENGINE=InnoDB"
        + (" ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8" if compressed else "")
    )


def _is_large_type(mysql_type: str) -> bool:
    return mysql_type.upper().split("(")[0] in {"TEXT", "BLOB", "JSON"}


_FIXED_BYTES = {"SMALLINT": 2, "INT": 4, "BIGINT": 8, "FLOAT": 4, "DOUBLE": 8, "TINYINT": 1, "DATE": 3}


def _column_bytes(mysql_type: str) -> int:
    """Worst-case stored bytes for the MySQL row-size and index-size limits (utf8mb4 is 4 bytes per character)."""
    kind = mysql_type.upper().split("(")[0]
    size = re.search(r"\((\d+)", mysql_type)
    if kind in {"TEXT", "BLOB", "JSON"}:
        return 12
    if kind == "VARCHAR":
        return int(size.group(1)) * 4 + 2 if size else 2
    if kind == "CHAR":
        return int(size.group(1)) * 4 if size else 4
    if kind in {"DATETIME", "TIME"}:
        return 8
    if kind == "DECIMAL":
        return 32
    return _FIXED_BYTES.get(kind, 8)


MYSQL_ROW_LIMIT = 65535 - 512
MYSQL_INDEX_LIMIT = 3072


def _fit_row_size(spec: TableSpec) -> None:
    """Map the widest VARCHAR columns to TEXT until the row fits MySQL's 65,535-byte row limit."""
    def total() -> int:
        return sum(_column_bytes(column.mysql_type) for column in spec.columns) + len(spec.columns) // 8 + 1

    while total() > MYSQL_ROW_LIMIT:
        candidates = [column for column in spec.columns if column.mysql_type.upper().startswith(("VARCHAR", "CHAR"))]
        if not candidates:
            spec.blocked = f"{spec.name} has a row wider than the MySQL row limit and no text column can be converted."
            return
        widest = max(candidates, key=lambda column: _column_bytes(column.mysql_type))
        spec.conversions.append(
            f"{spec.name}.{widest.name}: {widest.mysql_type} is mapped to TEXT because the combined row would exceed the MySQL row size limit."
        )
        widest.mysql_type = "TEXT"
        widest.lossy = "mapped to TEXT for the MySQL row size limit"


def _check_keys(spec: TableSpec) -> None:
    """Block a table whose key columns MySQL cannot index, before anything is created."""
    by_name = {column.name: column for column in spec.columns}
    keys = [("primary key", tuple(spec.primary_key))]
    keys += [(f"unique key {name}", columns) for name, columns in spec.uniques]
    keys += [(f"index {name}", columns) for name, columns in spec.indexes]
    for label, columns in keys:
        used = 0
        for name in columns:
            column = by_name.get(name)
            if column is None:
                continue
            if _is_large_type(column.mysql_type):
                spec.blocked = (
                    f"{spec.name}.{name} is part of {label}, but it maps to {column.mysql_type}, "
                    "which MySQL cannot index without a prefix length."
                )
                return
            used += _column_bytes(column.mysql_type)
        if used > MYSQL_INDEX_LIMIT:
            spec.blocked = f"{spec.name} {label} is {used} bytes, above the MySQL index limit of {MYSQL_INDEX_LIMIT} bytes."
            return


def create_index_sql(table: str, name: str, columns: tuple[str, ...]) -> str:
    return (
        f"CREATE INDEX {quote_mysql(name)} ON {quote_mysql(table)} ("
        + ", ".join(quote_mysql(column) for column in columns)
        + ")"
    )


def foreign_key_sql(table: str, foreign_key: ForeignKey) -> str:
    columns = ", ".join(quote_mysql(column) for column in foreign_key.columns)
    refs = ", ".join(quote_mysql(column) for column in foreign_key.ref_columns)
    return (
        f"ALTER TABLE {quote_mysql(table)} ADD CONSTRAINT {quote_mysql(foreign_key.name)} "
        f"FOREIGN KEY ({columns}) REFERENCES {quote_mysql(foreign_key.ref_table)} ({refs}) "
        f"ON DELETE {foreign_key.on_delete} ON UPDATE {foreign_key.on_update}"
    )


def insert_sql(table: TableSpec) -> str:
    columns = ", ".join(quote_mysql(column.name) for column in table.columns)
    placeholders = ", ".join(["%s"] * len(table.columns))
    return f"INSERT INTO {quote_mysql(table.name)} ({columns}) VALUES ({placeholders})"


def load_table(source_query, name: str) -> TableSpec:
    """Load one public table. source_query must be a PostgreSQL connection."""
    literal = quote_pg_literal(name)
    columns = source_query(
        "SELECT column_name, data_type, udt_name, character_maximum_length, "
        "numeric_precision, numeric_scale, is_nullable, column_default, identity_generation "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = {literal} ORDER BY ordinal_position"
    )
    spec = TableSpec(name=name)
    if not columns:
        spec.blocked = f"Table {name} has no columns in schema public."
        return spec
    for column in columns:
        column_name = str(column.get("column_name") or "")
        mysql_type, lossy, error = map_column(column)
        identity = str(column.get("identity_generation") or "").upper()
        default = column.get("column_default")
        auto = identity in {"ALWAYS", "BY DEFAULT"} or (
            isinstance(default, str) and default.lower().startswith("nextval(")
        )
        if auto and mysql_type not in {"SMALLINT", "INT", "BIGINT"}:
            spec.blocked = f"{name}.{column_name} uses a sequence, and MySQL can apply AUTO_INCREMENT only to an integer column."
            return spec
        if error or not mysql_type:
            spec.blocked = f"{name}.{column_name} was not mapped: {error}"
            return spec
        default_sql, default_error = translate_default(default, mysql_type=mysql_type, auto_increment=auto)
        if default_error:
            spec.blocked = f"{name}.{column_name} {default_error}"
            return spec
        if lossy:
            spec.conversions.append(f"{name}.{column_name}: {lossy}")
        spec.columns.append(
            ColumnSpec(
                name=column_name,
                mysql_type=mysql_type,
                nullable=str(column.get("is_nullable") or "YES").upper() == "YES",
                default_sql=default_sql,
                auto_increment=auto,
                lossy=lossy,
            )
        )
    autos = [column.name for column in spec.columns if column.auto_increment]
    if len(autos) > 1:
        spec.blocked = f"{name} has more than one identity column. MySQL allows one AUTO_INCREMENT column."
        return spec
    spec.primary_key = _key_columns(source_query, name, "PRIMARY KEY")
    if autos and autos[0] not in spec.primary_key:
        spec.blocked = (
            f"{name}.{autos[0]} is an identity column but not part of the primary key. "
            "MySQL AUTO_INCREMENT was not applied, and the column was not changed."
        )
        return spec
    spec.uniques = _grouped_keys(source_query, name, "UNIQUE")
    spec.indexes = _secondary_indexes(source_query, name, spec)
    spec.foreign_keys = _foreign_keys(source_query, name)
    if spec.blocked:
        return spec
    _fit_row_size(spec)
    if spec.blocked:
        return spec
    _check_keys(spec)
    if spec.blocked:
        return spec
    if any(column.auto_increment for column in spec.columns):
        spec.conversions.append(f"{name}: identity or serial column becomes AUTO_INCREMENT.")
    return spec


def order_tables(tables: list[TableSpec]) -> tuple[list[TableSpec], str]:
    """Parents first. A cycle blocks the migration instead of dropping foreign keys."""
    pending = {table.name: table for table in tables}
    dependencies = {table.name: set() for table in tables}
    for table in tables:
        for foreign_key in table.foreign_keys:
            if foreign_key.ref_table in pending and foreign_key.ref_table != table.name:
                dependencies[table.name].add(foreign_key.ref_table)
    ordered: list[TableSpec] = []
    while pending:
        ready = sorted(name for name, deps in dependencies.items() if name in pending and not deps)
        if not ready:
            return [], "Foreign key cycle is not supported: " + ", ".join(sorted(pending))
        name = ready[0]
        ordered.append(pending.pop(name))
        for deps in dependencies.values():
            deps.discard(name)
    return ordered, ""


def _key_columns(source_query, table: str, kind: str) -> list[str]:
    grouped = _grouped_keys(source_query, table, kind)
    if not grouped:
        return []
    return list(grouped[0][1])


def _grouped_keys(source_query, table: str, kind: str) -> list[tuple[str, tuple[str, ...]]]:
    literal = quote_pg_literal(table)
    rows = source_query(
        "SELECT tc.constraint_name, kcu.column_name, kcu.ordinal_position "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
        f"AND kcu.table_name = tc.table_name WHERE tc.table_schema = 'public' AND tc.table_name = {literal} "
        f"AND tc.constraint_type = {quote_pg_literal(kind)} ORDER BY tc.constraint_name, kcu.ordinal_position"
    )
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("constraint_name")), []).append(str(row.get("column_name")))
    return [(name, tuple(columns)) for name, columns in grouped.items()]


def _secondary_indexes(source_query, table: str, spec: TableSpec) -> list[tuple[str, tuple[str, ...]]]:
    literal = quote_pg_literal(table)
    expressions = source_query(
        "SELECT i.relname AS index_name FROM pg_index ix "
        "JOIN pg_class i ON i.oid = ix.indexrelid "
        "JOIN pg_class t ON t.oid = ix.indrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        f"WHERE n.nspname = 'public' AND t.relname = {literal} AND ix.indexprs IS NOT NULL"
    )
    if expressions:
        spec.blocked = f"{table} has an expression index. It was not dropped, and the table was not migrated."
        return []
    rows = source_query(
        "SELECT i.relname AS index_name, a.attname AS column_name, ix.indisunique, "
        "(ix.indexprs IS NOT NULL) AS expression_index, k.ordinality "
        "FROM pg_index ix "
        "JOIN pg_class i ON i.oid = ix.indexrelid "
        "JOIN pg_class t ON t.oid = ix.indrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "JOIN LATERAL unnest(ix.indkey::smallint[]) WITH ORDINALITY AS k(attnum, ordinality) ON true "
        "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum "
        f"WHERE n.nspname = 'public' AND t.relname = {literal} AND NOT ix.indisprimary "
        "ORDER BY i.relname, k.ordinality"
    )
    if any(row.get("expression_index") for row in rows):
        spec.blocked = f"{table} has an expression index. It was not dropped, and the table was not migrated."
        return []
    covered = {tuple(spec.primary_key)}
    covered.update(columns for _name, columns in spec.uniques)
    grouped: dict[str, list[str]] = {}
    unique_groups: dict[str, list[str]] = {}
    unique_names = {name for name, _columns in spec.uniques}
    for row in rows:
        index_name = str(row.get("index_name") or "")
        if row.get("indisunique"):
            if index_name not in unique_names:
                unique_groups.setdefault(index_name, []).append(str(row.get("column_name")))
            continue
        grouped.setdefault(index_name, []).append(str(row.get("column_name")))
    for name, columns in unique_groups.items():
        spec.uniques.append((name, tuple(columns)))
        covered.add(tuple(columns))
    indexes = []
    for name, columns in grouped.items():
        key = tuple(columns)
        if key in covered:
            continue
        indexes.append((name, key))
    return indexes


def _foreign_keys(source_query, table: str) -> list[ForeignKey]:
    literal = quote_pg_literal(table)
    rows = source_query(
        "SELECT tc.constraint_name, kcu.column_name, kcu.ordinal_position, "
        "ccu.table_name AS foreign_table, ccu.column_name AS foreign_column, "
        "rc.delete_rule, rc.update_rule "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
        "JOIN information_schema.constraint_column_usage ccu "
        "ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema "
        "JOIN information_schema.referential_constraints rc "
        "ON rc.constraint_name = tc.constraint_name AND rc.constraint_schema = tc.table_schema "
        f"WHERE tc.table_schema = 'public' AND tc.table_name = {literal} "
        "AND tc.constraint_type = 'FOREIGN KEY' "
        "ORDER BY tc.constraint_name, kcu.ordinal_position"
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        item = grouped.setdefault(
            str(row.get("constraint_name")),
            {
                "columns": [],
                "ref_table": str(row.get("foreign_table") or ""),
                "ref_columns": [],
                "on_delete": _rule(row.get("delete_rule")),
                "on_update": _rule(row.get("update_rule")),
            },
        )
        item["columns"].append(str(row.get("column_name")))
        item["ref_columns"].append(str(row.get("foreign_column")))
    return [
        ForeignKey(name, tuple(item["columns"]), item["ref_table"], tuple(item["ref_columns"]), item["on_delete"], item["on_update"])
        for name, item in grouped.items()
    ]


def _rule(value: object) -> str:
    text = str(value or "NO ACTION").upper()
    if text in {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}:
        return text
    return "NO ACTION"


def source_count_sql(table: str) -> str:
    return f"SELECT count(*) AS row_count FROM public.{quote_ident(table)}"
