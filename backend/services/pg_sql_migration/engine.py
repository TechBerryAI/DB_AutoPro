"""Copy mapped PostgreSQL tables into SQL Server or Oracle."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from backend.services.migration_framework.registry import MSSQL, _norm
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.precheck import SCOPE_DATA, SCOPE_SCHEMA, STRATEGY_REPLACE, STRATEGY_SKIP
from backend.services.pg_sql_migration.mapping import adapt_value, quote_ident
from backend.services.pg_sql_migration.schema import TableSpec, create_table_sql, drop_table_sql, insert_sql

COMMIT_BYTES = 16_000_000


@dataclass
class Outcome:
    name: str
    action: str
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None


@dataclass
class SqlMigrationRun:
    status: str
    summary: str
    outcomes: list[Outcome] = field(default_factory=list)
    migration_id: str = ""


def run_pg_sql_migration(
    specs: list[TableSpec],
    conflicts: list[str],
    *,
    target_kind: str,
    scope: str,
    strategy: str,
    batch_size: int,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event,
    migration_id: str,
    on_progress=None,
) -> SqlMigrationRun:
    kind = _norm(target_kind)
    label = "SQL Server" if kind == MSSQL else "Oracle"
    outcomes: list[Outcome] = []
    committed: list[str] = []
    total = len(specs)
    done = 0
    for spec in specs:
        if cancel.is_set():
            return _finish("Cancelled", f"Cancellation was requested before this table. Committed {label} tables were not rolled back.", outcomes, migration_id)
        if spec.name in conflicts and strategy == STRATEGY_SKIP and scope != SCOPE_DATA:
            outcomes.append(Outcome(spec.name, "skipped", f"The existing {label} table was left unchanged."))
            done += 1
            _progress(on_progress, done, total, spec.name)
            continue
        try:
            if spec.name in conflicts and strategy == STRATEGY_REPLACE:
                target.execute(drop_table_sql(kind, spec.name))
                target.commit()
                outcomes.append(Outcome(spec.name, "dropped", f"The existing {label} table was dropped because replacement was confirmed."))
            if scope != SCOPE_DATA:
                target.execute(create_table_sql(kind, spec))
                target.commit()
                committed.append(spec.name)
            inserted = 0
            if scope != SCOPE_SCHEMA:
                identity_on = _begin_identity(kind, target, spec)
                statement = insert_sql(kind, spec)
                pending = 0
                for batch in fetch_batches(spec.name, [column.name for column in spec.columns], batch_size, cancel):
                    if cancel.is_set():
                        target.rollback()
                        _end_identity(kind, target, spec, identity_on)
                        outcomes.append(Outcome(spec.name, "rolled_back", "The open insert was rolled back."))
                        return _finish("Cancelled", f"Cancellation stopped the open {label} insert. Earlier committed tables were not rolled back.", outcomes, migration_id)
                    values = [
                        tuple(adapt_value(value, column.target_type) for value, column in zip(row, spec.columns))
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
                _end_identity(kind, target, spec, identity_on)
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
                return _finish("Partially completed", f"Earlier {label} changes stayed committed. This attempt was not rolled back. " + (redact_text(str(exc)) or ""), outcomes, migration_id)
            return _finish("Failed", redact_text(str(exc)) or "Migration failed.", outcomes, migration_id)
        done += 1
        _progress(on_progress, done, total, spec.name)

    mismatched = []
    for spec in specs:
        if scope == SCOPE_SCHEMA or spec.name not in [item.name for item in outcomes if item.action == "data_copied"]:
            continue
        source_rows = int(count_source(spec.name))
        target_rows = _count(kind, target, spec.name)
        for item in outcomes:
            if item.name == spec.name and item.action == "data_copied":
                item.source_rows = source_rows
                item.target_rows = target_rows
        if target_rows != source_rows:
            mismatched.append(spec.name)
    if mismatched:
        return _finish("Verification failed", "Row counts do not match for: " + ", ".join(mismatched) + ".", outcomes, migration_id)
    status = "Completed with warnings" if any(item.action in {"skipped", "dropped"} for item in outcomes) else "Completed and verified"
    summary = f"{label} row counts match the PostgreSQL source." if status == "Completed and verified" else f"The {label} migration finished with tables skipped or replaced."
    if scope == SCOPE_SCHEMA and status == "Completed and verified":
        summary = f"{label} tables were created. No rows were copied."
    return _finish(status, summary, outcomes, migration_id)


def _begin_identity(kind: str, target, spec: TableSpec) -> bool:
    if kind != MSSQL or not any(column.identity for column in spec.columns):
        return False
    try:
        target.execute(f"SET IDENTITY_INSERT {quote_ident(kind, spec.name)} ON")
        return True
    except Exception:
        return False


def _end_identity(kind: str, target, spec: TableSpec, enabled: bool) -> None:
    if not enabled:
        return
    try:
        target.execute(f"SET IDENTITY_INSERT {quote_ident(kind, spec.name)} OFF")
    except Exception:
        pass


def _count(kind: str, target, table: str) -> int:
    rows = target.query(f"SELECT COUNT(*) AS row_count FROM {quote_ident(kind, table)}")
    row = rows[0] if rows else {}
    return int(row.get("row_count") or row.get("ROW_COUNT") or row.get("COUNT(*)") or 0)


def _progress(on_progress, done, total, name) -> None:
    if on_progress is not None:
        on_progress(done, total, name)


def _finish(status: str, summary: str, outcomes: list[Outcome], migration_id: str) -> SqlMigrationRun:
    return SqlMigrationRun(status, summary, outcomes, migration_id)


class SqlTargetSession:
    """SQL Server or Oracle statements for one outbound PostgreSQL migration."""

    def __init__(self, connection, kind: str) -> None:
        self._connection = connection
        self.kind = _norm(kind)

    def execute(self, sql: str, params=None) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params or [])
        finally:
            cursor.close()

    def executemany(self, sql: str, rows) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.executemany(sql, rows)
        finally:
            cursor.close()

    def query(self, sql: str, params=None) -> list[dict]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params or [])
            if not cursor.description:
                return []
            columns = [str(col[0]).lower() if isinstance(col[0], str) else str(getattr(col, "name", col[0])).lower() for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()
