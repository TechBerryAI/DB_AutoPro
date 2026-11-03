"""Apply a reviewed PostgreSQL-to-MySQL plan with parameterized inserts."""

from __future__ import annotations

import threading
import time
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
    packet_budget = 0
    lock_timeout_set = False
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
        compressed = _should_compress(spec)
        retried = False
        if not lock_timeout_set:
            # A DROP or CREATE that waits on another session's lock would otherwise wait for a year (MySQL's default).
            # sql_log_bin off keeps a multi-gigabyte insert from filling the binary-log volume and /var/tmp.
            _prepare_target_session(target)
            lock_timeout_set = True
        while True:
            try:
                if retried:
                    # Retry after a disk-space error: drop the half-loaded table, expire binary logs, rebuild compressed.
                    _emit(on_progress, done, total, f"{spec.name}: reclaiming disk space on the MySQL server")
                    _reclaim_disk(target)
                    _emit(on_progress, done, total, f"{spec.name}: dropping the partly loaded table")
                    target.execute("SET FOREIGN_KEY_CHECKS = 0")
                    target.execute(f"DROP TABLE IF EXISTS {quote_mysql(spec.name)}")
                    target.execute("SET FOREIGN_KEY_CHECKS = 1")
                    compressed = True
                elif spec.name in conflicts and strategy == STRATEGY_REPLACE:
                    _emit(on_progress, done, total, f"{spec.name}: dropping the existing MySQL table")
                    target.execute("SET FOREIGN_KEY_CHECKS = 0")
                    target.execute(f"DROP TABLE {quote_mysql(spec.name)}")
                    target.execute("SET FOREIGN_KEY_CHECKS = 1")
                    outcomes.append(Outcome(spec.name, "dropped", "The existing MySQL table was dropped because replacement was confirmed. MySQL committed that DROP."))
                if scope != SCOPE_DATA:
                    _emit(on_progress, done, total, f"{spec.name}: creating the MySQL table")
                    target.execute(create_table_sql(spec, compressed=compressed))
                    for name, columns in spec.indexes:
                        target.execute(create_index_sql(spec.name, name, columns))
                    if spec.name not in committed:
                        committed.append(spec.name)
                inserted = 0
                if scope != SCOPE_SCHEMA:
                    statement = insert_sql(spec)
                    if not packet_budget:
                        packet_budget = _packet_budget(target)
                    try:
                        source_total = int(count_source(spec.name))
                    except Exception:
                        source_total = 0
                    pending_bytes = 0
                    started = time.monotonic()
                    last_emit = 0.0
                    _emit(on_progress, done, total, f"{spec.name}: reading rows from PostgreSQL")
                    for batch in fetch_batches(spec.name, [column.name for column in spec.columns], batch_size, cancel):
                        if cancel.is_set():
                            return _cancelled(target, spec.name, inserted, outcomes, migration_id)
                        values = [
                            tuple(adapt_value(value, column.mysql_type) for value, column in zip(row, spec.columns))
                            for row in batch
                        ]
                        # Keep each INSERT below the server's max_allowed_packet, so a batch of wide rows
                        # cannot drop the connection.
                        for chunk in _chunks_by_size(values, packet_budget):
                            target.executemany(statement, chunk)
                            inserted += len(chunk)
                            pending_bytes += sum(_row_bytes(row) for row in chunk)
                            now = time.monotonic()
                            if now - last_emit >= 2.0:
                                last_emit = now
                                _emit_rows(on_progress, done, total, spec.name, inserted, source_total, now - started)
                        # Commit in bounded transactions. One transaction for a multi-gigabyte table fills the
                        # server's temporary binary-log cache and takes hours to roll back.
                        if pending_bytes >= COMMIT_BYTES:
                            target.commit()
                            pending_bytes = 0
                    if cancel.is_set():
                        return _cancelled(target, spec.name, inserted, outcomes, migration_id)
                    target.commit()
                    if spec.name not in committed:
                        committed.append(spec.name)
                    _set_autoincrement(target, spec)
                detail = f"Rows written: {inserted}."
                if compressed:
                    detail += " The table uses ROW_FORMAT=COMPRESSED because the first attempt ran out of disk space on the MySQL server."
                outcomes.append(Outcome(spec.name, "data_copied" if scope != SCOPE_SCHEMA else "schema_created", detail, source_rows=inserted))
                break
            except Exception as exc:
                try:
                    target.rollback()
                except Exception:
                    pass
                if not retried and scope != SCOPE_DATA and is_space_error(exc):
                    retried = True
                    compressed = True
                    outcomes.append(Outcome(spec.name, "retry", "The MySQL server ran out of disk space. Binary logs are being expired and the table is rebuilt with ROW_FORMAT=COMPRESSED."))
                    continue
                message = redact_text(str(exc)) or "The table failed."
                if is_space_error(exc):
                    message += " " + SPACE_HINT
                if is_lock_error(exc):
                    message += " " + LOCK_HINT.format(table=spec.name)
                    message += _describe_lock_holders(target, spec.name)
                outcomes.append(Outcome(spec.name, "failed", message))
                if committed or any(item.action == "dropped" for item in outcomes):
                    return _finish(
                        "Partially completed",
                        "Earlier MySQL changes stayed committed. This attempt was not rolled back. " + message,
                        outcomes,
                        migration_id,
                    )
                return _finish("Failed", message, outcomes, migration_id)
        done += 1
        _progress(on_progress, done, total, spec.name)

    try:
        target.execute("SET SESSION foreign_key_checks = 1")
    except Exception:
        pass
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


COMMIT_BYTES = 16_000_000
SPACE_HINT = (
    "The MySQL server has no free disk space for this copy. Free space on the MySQL data directory, its temporary "
    "directory, and its binary-log directory (run PURGE BINARY LOGS if they are large), or extend the disk, then run "
    "the migration again with Replace existing tables."
)


def is_space_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "1114" in text
        or "is full" in text
        or "no space left" in text
        or "errno 28" in text
        or "error 28" in text
        or ("1021" in text and "disk full" in text)
    )


LOCK_WAIT_SECONDS = 120
LOCK_HINT = (
    "Another MySQL session holds a lock on {table}, for example a mysql client with an open transaction, or a "
    "connection left by an earlier migration. On the MySQL server run SHOW FULL PROCESSLIST, then KILL the session "
    "that is using this table (or close that client), and run the migration again."
)


def is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "lock wait timeout" in text or "1205" in text or "metadata lock" in text


_LOCK_HOLDERS_SQL = (
    "SELECT DISTINCT t.PROCESSLIST_ID AS id, t.PROCESSLIST_USER AS user_name, t.PROCESSLIST_HOST AS host_name, "
    "t.PROCESSLIST_COMMAND AS command, t.PROCESSLIST_TIME AS seconds, "
    "LEFT(COALESCE(t.PROCESSLIST_INFO, ''), 80) AS statement_text, ml.LOCK_STATUS AS lock_status "
    "FROM performance_schema.metadata_locks ml "
    "JOIN performance_schema.threads t ON t.THREAD_ID = ml.OWNER_THREAD_ID "
    "WHERE ml.OBJECT_SCHEMA = DATABASE() AND ml.OBJECT_NAME = %s "
    "AND t.PROCESSLIST_ID IS NOT NULL AND t.PROCESSLIST_ID <> CONNECTION_ID() "
    "ORDER BY ml.LOCK_STATUS = 'GRANTED' DESC, t.PROCESSLIST_TIME DESC LIMIT 10"
)


def _describe_lock_holders(target, table: str) -> str:
    """Name the MySQL sessions that hold or wait for a lock on the table, with the KILL command for each."""
    try:
        rows = target.query(_LOCK_HOLDERS_SQL, (table,))
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for row in rows:
        session = row.get("id")
        if session is None:
            continue
        who = f"{row.get('user_name') or '?'}@{row.get('host_name') or '?'}"
        lines.append(
            f"session {session} ({who}, {str(row.get('command') or '').lower() or 'unknown'} for "
            f"{int(row.get('seconds') or 0)}s, lock {str(row.get('lock_status') or '').lower()}"
            f"{', last statement: ' + str(row['statement_text']) if row.get('statement_text') else ''}) - KILL {session};"
        )
    if not lines:
        return ""
    return " Sessions on the MySQL server using this table: " + " | ".join(lines)


def _should_compress(spec: TableSpec) -> bool:
    """Compress InnoDB rows that would otherwise store wide TEXT/BLOB/JSON uncompressed."""
    return any(
        column.mysql_type.upper().split("(")[0] in {"TEXT", "BLOB", "JSON", "MEDIUMTEXT", "LONGTEXT", "MEDIUMBLOB", "LONGBLOB"}
        for column in spec.columns
    )


def _prepare_target_session(target) -> None:
    for sql in (
        f"SET SESSION lock_wait_timeout = {LOCK_WAIT_SECONDS}",
        "SET SESSION sql_log_bin = 0",
        "SET SESSION unique_checks = 0",
        "SET SESSION foreign_key_checks = 0",
    ):
        try:
            target.execute(sql)
        except Exception:
            pass


def _reclaim_disk(target) -> None:
    """Best-effort free space so a compressed retry can start. Failures are ignored."""
    for sql in (
        "SET SESSION sql_log_bin = 0",
        "FLUSH BINARY LOGS",
        "PURGE BINARY LOGS BEFORE NOW()",
    ):
        try:
            target.execute(sql)
        except Exception:
            pass


def _emit(on_progress, done: float, total: int, text: str) -> None:
    if on_progress is not None:
        try:
            on_progress(done, total, text)
        except Exception:
            pass


def _emit_rows(on_progress, done: int, total: int, name: str, inserted: int, source_total: int, elapsed: float) -> None:
    rate = inserted / elapsed if elapsed > 0 else 0.0
    if source_total > 0:
        fraction = min(inserted / source_total, 0.999)
        left = (source_total - inserted) / rate if rate > 0 else 0
        text = (
            f"{name}: {inserted:,} of {source_total:,} rows ({fraction * 100:.1f}%), "
            f"{rate:,.0f} rows/s, about {_duration(left)} left"
        )
        _emit(on_progress, done + fraction, total, text)
    else:
        _emit(on_progress, done, total, f"{name}: {inserted:,} rows copied, {rate:,.0f} rows/s")


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _cancelled(target, table: str, inserted: int, outcomes: list, migration_id: str):
    target.rollback()
    outcomes.append(Outcome(
        table,
        "rolled_back",
        "The open insert transaction was rolled back. Earlier committed tables were not rolled back. "
        "Rows committed before the cancel stay in this table, so it is incomplete.",
    ))
    return _finish(
        "Cancelled",
        "Cancellation stopped the open MySQL insert. Earlier committed tables were not rolled back.",
        outcomes,
        migration_id,
    )


_DEFAULT_PACKET_BUDGET = 1_000_000
_MAX_PACKET_BUDGET = 16_000_000


def _packet_budget(target) -> int:
    """Bytes of row data to send in one INSERT. Escaping can grow values, so use a fraction of max_allowed_packet."""
    try:
        rows = target.query("SELECT @@max_allowed_packet AS packet")
        packet = int((rows[0].get("packet") if rows else 0) or 0)
    except Exception:
        return _DEFAULT_PACKET_BUDGET
    if packet <= 0:
        return _DEFAULT_PACKET_BUDGET
    return max(64_000, min(packet // 4, _MAX_PACKET_BUDGET))


def _row_bytes(row) -> int:
    size = 16
    for value in row:
        if isinstance(value, (str, bytes, bytearray, memoryview)):
            size += len(value) + 8
        else:
            size += 24
    return size


def _chunks_by_size(rows: list, budget: int):
    chunk: list = []
    used = 0
    for row in rows:
        size = _row_bytes(row)
        if chunk and used + size > budget:
            yield chunk
            chunk, used = [], 0
        chunk.append(row)
        used += size
    if chunk:
        yield chunk


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
