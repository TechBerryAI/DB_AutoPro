"""Copy mapped tables into PostgreSQL with parameterized inserts."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.precheck import SCOPE_DATA, SCOPE_SCHEMA, STRATEGY_REPLACE, STRATEGY_SKIP
from backend.services.to_pg_migration.mapping import adapt_value, quote_pg
from backend.services.to_pg_migration.schema import TableSpec, create_table_sql, drop_table_sql, insert_sql


@dataclass
class Outcome:
    name: str
    action: str
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None


@dataclass
class ToPgMigrationRun:
    status: str
    summary: str
    outcomes: list[Outcome] = field(default_factory=list)
    migration_id: str = ""


COMMIT_BYTES = 16_000_000


def run_to_pg_migration(
    specs: list[TableSpec],
    conflicts: list[str],
    *,
    scope: str,
    strategy: str,
    batch_size: int,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event,
    migration_id: str,
    on_progress=None,
) -> ToPgMigrationRun:
    outcomes: list[Outcome] = []
    committed: list[str] = []
    total = len(specs)
    done = 0
    for spec in specs:
        if cancel.is_set():
            return _finish("Cancelled", "Cancellation was requested before this table. Committed PostgreSQL tables were not rolled back.", outcomes, migration_id)
        if spec.name in conflicts and strategy == STRATEGY_SKIP and scope != SCOPE_DATA:
            outcomes.append(Outcome(spec.name, "skipped", "The existing PostgreSQL table was left unchanged."))
            done += 1
            _progress(on_progress, done, total, spec.name)
            continue
        try:
            if spec.name in conflicts and strategy == STRATEGY_REPLACE:
                target.execute(drop_table_sql(spec.name))
                target.commit()
                outcomes.append(Outcome(spec.name, "dropped", "The existing PostgreSQL table was dropped because replacement was confirmed."))
            if scope != SCOPE_DATA:
                target.execute(create_table_sql(spec))
                target.commit()
                committed.append(spec.name)
            inserted = 0
            if scope != SCOPE_SCHEMA:
                statement = insert_sql(spec)
                pending = 0
                for batch in fetch_batches(spec.name, [column.name for column in spec.columns], batch_size, cancel):
                    if cancel.is_set():
                        target.rollback()
                        outcomes.append(Outcome(spec.name, "rolled_back", "The open insert was rolled back."))
                        return _finish("Cancelled", "Cancellation stopped the open PostgreSQL insert. Earlier committed tables were not rolled back.", outcomes, migration_id)
                    values = [
                        tuple(adapt_value(value, column.pg_type) for value, column in zip(row, spec.columns))
                        for row in batch
                    ]
                    if values:
                        target.executemany(statement, values)
                        inserted += len(values)
                        pending += sum(len(str(item)) if item is not None else 0 for row in values for item in row)
                    if pending >= COMMIT_BYTES:
                        target.commit()
                        pending = 0
                    _progress(on_progress, done, total, f"{spec.name}: {inserted:,} rows copied")
                target.commit()
                if spec.name not in committed:
                    committed.append(spec.name)
            outcomes.append(Outcome(spec.name, "data_copied" if scope != SCOPE_SCHEMA else "schema_created", f"Rows written: {inserted}.", source_rows=inserted))
        except Exception as exc:
            try:
                target.rollback()
            except Exception:
                pass
            outcomes.append(Outcome(spec.name, "failed", redact_text(str(exc)) or "The table failed."))
            if committed or any(item.action == "dropped" for item in outcomes):
                return _finish("Partially completed", "Earlier PostgreSQL changes stayed committed. This attempt was not rolled back. " + (redact_text(str(exc)) or ""), outcomes, migration_id)
            return _finish("Failed", redact_text(str(exc)) or "Migration failed.", outcomes, migration_id)
        done += 1
        _progress(on_progress, done, total, spec.name)

    mismatched = []
    for spec in specs:
        if scope == SCOPE_SCHEMA or spec.name not in [item.name for item in outcomes if item.action == "data_copied"]:
            continue
        source_rows = int(count_source(spec.name))
        target_rows = _count(target, spec.name)
        for item in outcomes:
            if item.name == spec.name and item.action == "data_copied":
                item.source_rows = source_rows
                item.target_rows = target_rows
        if target_rows != source_rows:
            mismatched.append(spec.name)
    if mismatched:
        return _finish("Verification failed", "Row counts do not match for: " + ", ".join(mismatched) + ".", outcomes, migration_id)
    status = "Completed with warnings" if any(item.action in {"skipped", "dropped"} for item in outcomes) else "Completed and verified"
    summary = "PostgreSQL row counts match the source." if status == "Completed and verified" else "The PostgreSQL migration finished with tables skipped or replaced."
    if scope == SCOPE_SCHEMA and status == "Completed and verified":
        summary = "PostgreSQL tables were created. No rows were copied."
    return _finish(status, summary, outcomes, migration_id)


def _count(target, table: str) -> int:
    rows = target.query(f"SELECT count(*) AS row_count FROM {quote_pg(table)}")
    return int(rows[0].get("row_count") or 0)


def _progress(on_progress, done, total, name) -> None:
    if on_progress is not None:
        on_progress(done, total, name)


def _finish(status: str, summary: str, outcomes: list[Outcome], migration_id: str) -> ToPgMigrationRun:
    return ToPgMigrationRun(status, summary, outcomes, migration_id)


class PostgresTargetSession:
    """PostgreSQL statements for one inbound migration."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params=None) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())

    def executemany(self, sql: str, rows) -> None:
        with self._connection.cursor() as cursor:
            cursor.executemany(sql, rows)

    def query(self, sql: str, params=None) -> list[dict]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            if not cursor.description:
                return []
            columns = [col.name if hasattr(col, "name") else col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()
