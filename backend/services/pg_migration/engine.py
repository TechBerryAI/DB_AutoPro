"""Copy public tables between two PostgreSQL databases without holding every row in memory."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.precheck import (
    PUBLIC_TABLES_SQL,
    SCOPE_DATA,
    SCOPE_SCHEMA,
    STRATEGY_REPLACE,
    STRATEGY_SKIP,
    MigrationPrecheck,
    quote_ident,
)

class CopyCancelled(Exception):
    """An in-progress COPY stopped. rolled_back is true only after the target count matches the pre-copy count."""

    def __init__(self, table: str, rolled_back: bool) -> None:
        self.table = table
        self.rolled_back = rolled_back
        super().__init__(table)


COPY_CANCEL_LIMITATION = (
    "An in-progress COPY can be cancelled safely only when the target connection is inside a transaction. "
    "The target query is cancelled and that transaction is rolled back when the row count matches the pre-copy count. "
    "Earlier committed tables are not rolled back. "
    "A connection in autocommit mode cannot undo a COPY, so cancellation waits until the current COPY returns."
)
Query = Callable[[str], list[dict]]
Execute = Callable[[str], None]


@dataclass
class ObjectOutcome:
    name: str
    action: str
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None


@dataclass
class MigrationRun:
    status: str
    summary: str
    outcomes: list[ObjectOutcome] = field(default_factory=list)
    migration_id: str = ""


def build_create_table(table: str, columns: list[dict]) -> str | None:
    """Return CREATE TABLE, or None when a column type is not supported."""
    parts: list[str] = []
    for column in columns:
        name = str(column.get("column_name") or "")
        data_type = str(column.get("data_type") or "").upper()
        if not name or data_type in {"ARRAY", "USER-DEFINED"}:
            return None
        sql_type = str(column.get("udt_name") or column.get("data_type") or "")
        if not sql_type or sql_type.upper() in {"ARRAY", "USER-DEFINED"}:
            return None
        length = column.get("character_maximum_length")
        rendered = sql_type
        if length:
            rendered = f"{sql_type}({int(length)})"
        null_sql = "NULL" if str(column.get("is_nullable") or "YES").upper() == "YES" else "NOT NULL"
        identity = str(column.get("identity_generation") or "").upper()
        if identity in {"ALWAYS", "BY DEFAULT"}:
            parts.append(f"{quote_ident(name)} {rendered} {null_sql} GENERATED {identity} AS IDENTITY")
            continue
        default = column.get("column_default")
        default_sql = f" DEFAULT {default}" if default else ""
        parts.append(f"{quote_ident(name)} {rendered} {null_sql}{default_sql}")
    if not parts:
        return None
    return f"CREATE TABLE IF NOT EXISTS public.{quote_ident(table)} ({', '.join(parts)})"


def run_migration(
    precheck: MigrationPrecheck,
    *,
    scope: str,
    strategy: str,
    batch_size: int,
    source_query: Query,
    target_query: Query,
    target_execute: Execute,
    copy_rows: CopyRows,
    cancel: threading.Event,
    migration_id: str,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> MigrationRun:
    """Apply a reviewed precheck. Source rows are not updated."""
    if not precheck.ready:
        return MigrationRun("Failed", precheck.stop_reason or "Prechecks did not pass.", migration_id=migration_id)
    outcomes: list[ObjectOutcome] = []
    for name in precheck.unsupported:
        outcomes.append(ObjectOutcome(name, "unsupported", "Reported and not migrated."))
    copied: list[str] = []
    total = len(precheck.tables)
    foreign_keys: list[str] = []
    tables = order_by_foreign_keys(list(precheck.tables), _foreign_key_edges(source_query, precheck.tables))
    sequences = _sequence_rows(source_query, tables)
    try:
        if scope != SCOPE_DATA:
            for statement in _sequence_ddl(sequences):
                target_execute(statement)
        for index, table in enumerate(tables, start=1):
            if on_progress:
                on_progress(index - 1, total, f"Working on {table}")
            if cancel.is_set():
                return MigrationRun(
                    "Cancelled",
                    _partial_summary(outcomes, "Cancellation was requested. Completed objects were not rolled back."),
                    outcomes,
                    migration_id,
                )
            if table in precheck.conflicts and strategy == STRATEGY_SKIP and scope != SCOPE_DATA:
                outcomes.append(ObjectOutcome(table, "skipped", "The target table already exists and was left unchanged."))
                continue
            if table in precheck.conflicts and strategy == STRATEGY_REPLACE:
                target_execute(f"DROP TABLE public.{quote_ident(table)} CASCADE")
                outcomes.append(ObjectOutcome(table, "replaced", "The existing target table was dropped because replacement was confirmed."))
            if scope != SCOPE_DATA and (table not in precheck.conflicts or strategy == STRATEGY_REPLACE):
                columns = _columns(source_query, table)
                statement = build_create_table(table, columns)
                if statement is None:
                    outcomes.append(ObjectOutcome(table, "unsupported", "A column type cannot be recreated safely."))
                    continue
                target_execute(statement)
                for constraint, kind in _constraint_sql(source_query, table):
                    if kind == "f":
                        foreign_keys.append(constraint)
                    else:
                        target_execute(constraint)
                outcomes.append(ObjectOutcome(table, "schema_created", "Table and supported constraints were created."))
            if scope == SCOPE_SCHEMA:
                continue
            copied_rows = copy_rows(table, batch_size, cancel)
            outcomes.append(ObjectOutcome(table, "data_copied", f"Copied {copied_rows} rows.", source_rows=copied_rows))
            copied.append(table)
            if cancel.is_set():
                return MigrationRun(
                    "Cancelled",
                    _partial_summary(outcomes, "Cancellation was requested after the current table finished. Completed objects were not rolled back."),
                    outcomes,
                    migration_id,
                )
            if on_progress:
                on_progress(index, total, f"Finished {table}")
        if scope != SCOPE_DATA:
            for statement in _index_ddl(source_query, tables):
                target_execute(statement)
            for statement in _sequence_ownership(sequences):
                target_execute(statement)
        for statement in _sequence_state(sequences):
            target_execute(statement)
        for constraint in foreign_keys:
            target_execute(constraint)
    except CopyCancelled as exc:
        action = "copy_rolled_back" if exc.rolled_back else "uncertain"
        detail = (
            "The in-progress COPY was rolled back in its transaction. Earlier committed tables were not rolled back."
            if exc.rolled_back
            else "The in-progress COPY could not be verified as rolled back. Treat that table as uncertain."
        )
        outcomes.append(ObjectOutcome(exc.table, action, detail))
        return MigrationRun("Cancelled", _partial_summary(outcomes, detail), outcomes, migration_id)
    except Exception as exc:
        outcomes.append(ObjectOutcome("migration", "failed", redact_text(str(exc)) or "Migration failed."))
        return MigrationRun("Partially completed", _partial_summary(outcomes, "The failure was not rolled back."), outcomes, migration_id)

    if scope == SCOPE_SCHEMA:
        present = {str(row.get("table_name")) for row in target_query(PUBLIC_TABLES_SQL)}
        missing = [item.name for item in outcomes if item.action == "schema_created" and item.name not in present]
        if missing:
            return MigrationRun(
                "Verification failed",
                "These tables were not found on the target after creation: " + ", ".join(missing),
                outcomes,
                migration_id,
            )
        if any(item.action in {"unsupported", "skipped", "replaced"} for item in outcomes):
            return MigrationRun(
                "Completed with warnings",
                "Schema objects were created. Data was not copied. Some objects were skipped or unsupported.",
                outcomes,
                migration_id,
            )
        return MigrationRun(
            "Completed and verified",
            "The selected tables exist on the target. Data was not copied.",
            outcomes,
            migration_id,
        )
    mismatches = []
    for table in copied:
        source_count = _count(source_query, table)
        target_count = _count(target_query, table)
        for item in outcomes:
            if item.name == table and item.action == "data_copied":
                item.source_rows = source_count
                item.target_rows = target_count
        if source_count != target_count:
            mismatches.append(f"{table}: source {source_count}, target {target_count}")
    if mismatches:
        return MigrationRun(
            "Verification failed",
            "Row counts do not match. " + "; ".join(mismatches),
            outcomes,
            migration_id,
        )
    if any(item.action in {"unsupported", "skipped", "replaced"} for item in outcomes):
        return MigrationRun(
            "Completed with warnings",
            "The migration finished and verified copied tables. Some objects were skipped, replaced, or unsupported.",
            outcomes,
            migration_id,
        )
    return MigrationRun(
        "Completed and verified",
        "Copied tables exist and their row counts match. This does not prove every column value is identical.",
        outcomes,
        migration_id,
    )


def order_by_foreign_keys(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Parents before children. The original order is kept when the references cycle."""
    incoming = {table: set() for table in tables}
    for child, parent in edges:
        if child in incoming and parent in incoming and child != parent:
            incoming[child].add(parent)
    ordered: list[str] = []
    remaining = list(tables)
    while remaining:
        ready = [table for table in remaining if incoming[table].issubset(ordered)]
        if not ready:
            return list(tables)
        ordered.extend(ready)
        remaining = [table for table in remaining if table not in ready]
    return ordered


def _foreign_key_edges(query: Query, tables: list[str]) -> list[tuple[str, str]]:
    try:
        rows = query(
            "SELECT child.relname AS child_name, parent.relname AS parent_name "
            "FROM pg_constraint c "
            "JOIN pg_class child ON child.oid = c.conrelid "
            "JOIN pg_class parent ON parent.oid = c.confrelid "
            "WHERE c.contype = 'f'"
        )
    except Exception:
        return []
    selected = set(tables)
    return [
        (str(row.get("child_name")), str(row.get("parent_name")))
        for row in rows
        if str(row.get("child_name")) in selected and str(row.get("parent_name")) in selected
    ]


def _sequence_rows(query: Query, tables: list[str]) -> list[dict]:
    try:
        rows = query(
            "SELECT c.relname AS sequence_name, t.relname AS table_name, a.attname AS column_name, "
            "s.seqstart AS start_value, s.seqincrement AS increment_by, "
            "pg_sequence_last_value(c.oid) AS last_value, a.attidentity AS identity_kind "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_sequence s ON s.seqrelid = c.oid "
            "LEFT JOIN pg_depend d ON d.objid = c.oid AND d.deptype IN ('a', 'i') "
            "LEFT JOIN pg_class t ON t.oid = d.refobjid "
            "LEFT JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
            "WHERE n.nspname = 'public' AND c.relkind = 'S'"
        )
    except Exception:
        return []
    selected = set(tables)
    return [row for row in rows if str(row.get("table_name") or "") in selected]


def _sequence_ddl(rows: list[dict]) -> list[str]:
    statements = []
    seen = set()
    for row in rows:
        name = str(row.get("sequence_name") or "")
        if not name or name in seen or str(row.get("identity_kind") or "") in {"a", "d"}:
            continue
        seen.add(name)
        start = int(row.get("start_value") or 1)
        increment = int(row.get("increment_by") or 1)
        statements.append(
            f"CREATE SEQUENCE IF NOT EXISTS public.{quote_ident(name)} START {start} INCREMENT {increment}"
        )
    return statements


def _sequence_ownership(rows: list[dict]) -> list[str]:
    statements = []
    for row in rows:
        name = str(row.get("sequence_name") or "")
        table = str(row.get("table_name") or "")
        column = str(row.get("column_name") or "")
        if str(row.get("identity_kind") or "") in {"a", "d"}:
            continue
        if name and table and column:
            statements.append(
                f"ALTER SEQUENCE public.{quote_ident(name)} OWNED BY public.{quote_ident(table)}.{quote_ident(column)}"
            )
    return statements


def _sequence_state(rows: list[dict]) -> list[str]:
    statements = []
    seen = set()
    for row in rows:
        name = str(row.get("sequence_name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        qualified = f"public.{quote_ident(name)}"
        last = row.get("last_value")
        if last is None:
            statements.append(f"SELECT setval('{qualified}', {int(row.get('start_value') or 1)}, false)")
        else:
            statements.append(f"SELECT setval('{qualified}', {int(last)}, true)")
    return statements


def _index_ddl(query: Query, tables: list[str]) -> list[str]:
    try:
        rows = query(
            "SELECT i.tablename AS table_name, i.indexdef AS index_sql FROM pg_indexes i "
            "WHERE i.schemaname = 'public' AND NOT EXISTS ("
            "SELECT 1 FROM pg_constraint c JOIN pg_class cls ON cls.oid = c.conindid "
            "WHERE cls.relname = i.indexname)"
        )
    except Exception:
        return []
    selected = set(tables)
    return [
        str(row.get("index_sql"))
        for row in rows
        if str(row.get("table_name") or "") in selected and row.get("index_sql")
    ]


def _columns(query: Query, table: str) -> list[dict]:
    rows = query(
        "SELECT column_name, data_type, udt_name, character_maximum_length, is_nullable, column_default, identity_generation, table_name "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY ordinal_position"
    )
    return [row for row in rows if str(row.get("table_name")) == table]


def _constraint_sql(query: Query, table: str) -> list[tuple[str, str]]:
    try:
        rows = query(
            "SELECT conrelid::regclass::text AS table_name, pg_get_constraintdef(oid) AS definition, contype "
            "FROM pg_constraint WHERE contype IN ('p', 'u', 'f')"
        )
    except Exception:
        return []
    statements = []
    for row in rows:
        if str(row.get("table_name")) not in {table, f"public.{table}", f'public."{table}"'}:
            continue
        definition = str(row.get("definition") or "")
        if definition:
            kind = str(row.get("contype") or "p")
            statements.append((f"ALTER TABLE public.{quote_ident(table)} ADD {definition}", kind))
    return statements


def _count(query: Query, table: str) -> int:
    rows = query(f"SELECT count(*) AS row_count FROM public.{quote_ident(table)}")
    return int(rows[0].get("row_count") or 0)


def _partial_summary(outcomes: list[ObjectOutcome], suffix: str) -> str:
    done = [f"{item.name}:{item.action}" for item in outcomes]
    return "Completed objects: " + (", ".join(done) or "none") + ". " + suffix


def stream_copy_table(source_connection, target_connection, table: str, batch_size: int, cancel: threading.Event) -> int:
    """Stream one table. batch_size is not a memory buffer of all rows.

    Cancellation before COPY starts copies nothing. During COPY, the target query is cancelled and its
    transaction is rolled back when the connection is not in autocommit and the row count returns to the
    pre-copy count. Autocommit cannot undo a COPY; see COPY_CANCEL_LIMITATION.
    """
    del batch_size
    if cancel.is_set():
        raise CopyCancelled(table, rolled_back=True)
    if getattr(target_connection, "autocommit", False):
        return _copy_pipe(source_connection, target_connection, table)
    before = _connection_count(target_connection, table)
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.05):
            if cancel.is_set():
                for connection in (source_connection, target_connection):
                    try:
                        connection.cancel()
                    except Exception:
                        pass
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        copied = _copy_pipe(source_connection, target_connection, table)
    except Exception as exc:
        stop.set()
        watcher.join()
        rolled_back = _rollback_matches(target_connection, table, before)
        if cancel.is_set():
            raise CopyCancelled(table, rolled_back) from exc
        raise
    finally:
        stop.set()
    watcher.join()
    if cancel.is_set():
        try:
            target_connection.rollback()
        except Exception:
            pass
        raise CopyCancelled(table, _rollback_matches(target_connection, table, before))
    return copied


def _copy_pipe(source_connection, target_connection, table: str) -> int:
    qualified = f"public.{quote_ident(table)}"
    read_fd, write_fd = os.pipe()
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            with os.fdopen(write_fd, "wb") as writer:
                with source_connection.cursor() as cursor:
                    cursor.copy_expert(f"COPY {qualified} TO STDOUT", writer)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    try:
        with os.fdopen(read_fd, "rb") as reader:
            with target_connection.cursor() as cursor:
                cursor.copy_expert(f"COPY {qualified} FROM STDIN", reader)
        target_connection.commit()
    finally:
        worker.join()
    if errors:
        raise errors[0]
    return _connection_count(target_connection, table)


def _connection_count(connection, table: str) -> int:
    qualified = f"public.{quote_ident(table)}"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {qualified}")
        row = cursor.fetchone()
        return int(row[0] if not isinstance(row, dict) else next(iter(row.values())))


def _rollback_matches(connection, table: str, before: int) -> bool:
    try:
        connection.rollback()
    except Exception:
        return False
    try:
        return _connection_count(connection, table) == before
    except Exception:
        return False
