"""Apply a reviewed PostgreSQL-to-MySQL plan with parameterized inserts."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.precheck import SCOPE_DATA, SCOPE_SCHEMA, STRATEGY_REPLACE, STRATEGY_SKIP
from backend.services.pg_mysql_migration.mapping import adapt_value, quote_mysql
from backend.services.pg_mysql_migration.schema import TableSpec, create_index_sql, create_table_sql, foreign_key_sql, insert_sql


@dataclass
class Outcome:
    name: str
    action: str
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None


@dataclass
class MysqlMigrationRun:
    status: str
    summary: str
    outcomes: list[Outcome] = field(default_factory=list)
    migration_id: str = ""


def run_pg_mysql_migration(
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
) -> MysqlMigrationRun:
    """Copy public tables.

    Cancellation is checked before each table and between insert batches.
    A MySQL statement that has already started runs until the server returns it.
    DDL that MySQL has committed is not rolled back.
    """
    outcomes: list[Outcome] = []
    committed: list[str] = []
    selected = [spec for spec in specs if not (spec.name in conflicts and strategy == STRATEGY_SKIP and scope != SCOPE_DATA)]
    total = len(specs)
    done = 0
    for spec in specs:
        if cancel.is_set():
            return _finish(
                "Cancelled",
                "Cancellation was requested before this table. Committed MySQL tables were not rolled back.",
                outcomes,
                migration_id,
            )
        if spec.name in conflicts and strategy == STRATEGY_SKIP and scope != SCOPE_DATA:
            outcomes.append(Outcome(spec.name, "skipped", "The existing MySQL table was left unchanged."))
            done += 1
            _progress(on_progress, done, total, spec.name)
            continue
        try:
            if spec.name in conflicts and strategy == STRATEGY_REPLACE:
                target.execute("SET FOREIGN_KEY_CHECKS = 0")
                target.execute(f"DROP TABLE {quote_mysql(spec.name)}")
                target.execute("SET FOREIGN_KEY_CHECKS = 1")
                outcomes.append(Outcome(spec.name, "dropped", "The existing MySQL table was dropped because replacement was confirmed. MySQL committed that DROP."))
            if scope != SCOPE_DATA:
                target.execute(create_table_sql(spec))
                for name, columns in spec.indexes:
                    target.execute(create_index_sql(spec.name, name, columns))
                committed.append(spec.name)
            inserted = 0
            if scope != SCOPE_SCHEMA:
                statement = insert_sql(spec)
                for batch in fetch_batches(spec.name, [column.name for column in spec.columns], batch_size, cancel):
                    if cancel.is_set():
                        target.rollback()
                        outcomes.append(Outcome(spec.name, "rolled_back", "The open insert transaction was rolled back. Earlier committed tables were not rolled back."))
                        return _finish(
                            "Cancelled",
                            "Cancellation stopped the open MySQL insert. Earlier committed tables were not rolled back.",
                            outcomes,
                            migration_id,
                        )
                    values = [
                        tuple(adapt_value(value, column.mysql_type) for value, column in zip(row, spec.columns))
                        for row in batch
                    ]
                    if values:
                        target.executemany(statement, values)
                        inserted += len(values)
                if cancel.is_set():
                    target.rollback()
                    outcomes.append(Outcome(spec.name, "rolled_back", "The open insert transaction was rolled back. Earlier committed tables were not rolled back."))
                    return _finish(
                        "Cancelled",
                        "Cancellation stopped the open MySQL insert. Earlier committed tables were not rolled back.",
                        outcomes,
                        migration_id,
                    )
                target.commit()
                if spec.name not in committed:
                    committed.append(spec.name)
                _set_autoincrement(target, spec)
            outcomes.append(Outcome(spec.name, "data_copied" if scope != SCOPE_SCHEMA else "schema_created", f"Rows written: {inserted}.", source_rows=inserted))
        except Exception as exc:
            try:
                target.rollback()
            except Exception:
                pass
            outcomes.append(Outcome(spec.name, "failed", redact_text(str(exc)) or "The table failed."))
            if committed or any(item.action == "dropped" for item in outcomes):
                return _finish(
                    "Partially completed",
                    "Earlier MySQL changes stayed committed. This attempt was not rolled back. " + (redact_text(str(exc)) or ""),
                    outcomes,
                    migration_id,
                )
            return _finish("Failed", redact_text(str(exc)) or "Migration failed.", outcomes, migration_id)
        done += 1
        _progress(on_progress, done, total, spec.name)

    for spec in selected:
        for foreign_key in spec.foreign_keys:
            if scope == SCOPE_DATA:
                break
            try:
                target.execute(foreign_key_sql(spec.name, foreign_key))
                target.commit()
            except Exception as exc:
                outcomes.append(Outcome(foreign_key.name, "failed", redact_text(str(exc)) or "Foreign key failed."))
                return _finish(
                    "Partially completed",
                    "Tables already created or loaded stayed in MySQL. The foreign key was not added, and earlier work was not rolled back.",
                    outcomes,
                    migration_id,
                )

    mismatched = []
    for spec in selected:
        if scope == SCOPE_SCHEMA:
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
        return _finish(
            "Verification failed",
            "Row counts do not match for: " + ", ".join(mismatched) + ". Earlier committed tables were not rolled back.",
            outcomes,
            migration_id,
        )
    status = "Completed with warnings" if any(item.action in {"skipped", "dropped"} for item in outcomes) else "Completed and verified"
    summary = "MySQL row counts match the PostgreSQL source." if status == "Completed and verified" else "The MySQL migration finished with tables skipped or replaced."
    if scope == SCOPE_SCHEMA and status == "Completed and verified":
        summary = "MySQL tables were created. No rows were copied."
    return _finish(status, summary, outcomes, migration_id)


def _set_autoincrement(target, spec: TableSpec) -> None:
    column = next((item for item in spec.columns if item.auto_increment), None)
    if column is None:
        return
    rows = target.query(f"SELECT MAX({quote_mysql(column.name)}) AS max_id FROM {quote_mysql(spec.name)}")
    max_id = rows[0].get("max_id") if rows else None
    if max_id is None:
        return
    target.execute(f"ALTER TABLE {quote_mysql(spec.name)} AUTO_INCREMENT = {int(max_id) + 1}")
    target.commit()


def _count(target, table: str) -> int:
    rows = target.query(f"SELECT COUNT(*) AS row_count FROM {quote_mysql(table)}")
    return int(rows[0].get("row_count") or 0)


def _progress(on_progress, done: int, total: int, name: str) -> None:
    if on_progress is not None:
        on_progress(done, total, name)


def _finish(status: str, summary: str, outcomes: list[Outcome], migration_id: str) -> MysqlMigrationRun:
    return MysqlMigrationRun(status, summary, outcomes, migration_id)


class MysqlTargetSession:
    """MySQL statements for one migration. PostgreSQL COPY is not available here."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params=None) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params or ())
        finally:
            cursor.close()

    def executemany(self, sql: str, rows) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.executemany(sql, rows)
        finally:
            cursor.close()

    def query(self, sql: str, params=None) -> list[dict]:
        cursor = self._connection.cursor(dictionary=True)
        try:
            cursor.execute(sql, params or ())
            return list(cursor.fetchall())
        finally:
            cursor.close()

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()
