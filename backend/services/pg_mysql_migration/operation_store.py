"""Durable migration operation records.

SQLite lives under var/data. The file stores identities and outcomes.
It does not store passwords or connection strings.

Writers take a reserved lock with BEGIN IMMEDIATE. A commit is the crash
boundary. A running row stores the process id and that process's creation
time. Startup marks the row reconciliation_required when that pair is no
longer the live process, including when the operating system has reused the
process id. This does not rerun the migration.

Copying the database file without its -wal file can omit the newest commits.
Use snapshot() for a consistent copy of the operation history.

The database file needs to be readable and writable by the account that runs
the API. On Windows the file DACL is limited to that user. Administrators can
still take ownership. The file is not encrypted.
"""

from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import stat
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.core.paths import DATA_DIR
from backend.services.mysql_patch.redaction import redact_text

STORE_PATH = DATA_DIR / "migration_operations.sqlite"
SCHEMA_VERSION = 2

class MigrationStoreError(RuntimeError):
    """The operation database cannot be opened or upgraded safely."""

_BLOCKING = (
    "running",
    "reconciliation_required",
    "partially_completed",
    "cancelled",
    "verification_failed",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_operations (
    operation_id TEXT PRIMARY KEY,
    client_request_id TEXT UNIQUE,
    source_database TEXT NOT NULL,
    target_database TEXT NOT NULL,
    target_server_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    tables_json TEXT NOT NULL DEFAULT '{}',
    recovery TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    confirm_retry INTEGER NOT NULL DEFAULT 0,
    reconciled INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    owner_pid INTEGER NOT NULL DEFAULT 0,
    owner_instance TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_META = """
CREATE TABLE IF NOT EXISTS migration_store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

RECONCILE_TEXT = (
    "The API process stopped while this migration was running. "
    "Do not assume MySQL changes were rolled back. "
    "Reconcile the target before starting another copy."
)


def _restrict_store_family(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.is_file():
            _restrict_store_file(candidate)


def _restrict_store_file(path: Path) -> None:
    """Limit the file to the current user. The API account still needs read and write."""
    if os.name == "nt":
        _restrict_windows_file(path)
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        return


def _restrict_windows_file(path: Path) -> None:
    sid = _current_user_sid()
    if not sid:
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
        check=False,
        capture_output=True,
        text=True,
    )


def _current_user_sid() -> str:
    if os.name != "nt":
        return ""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.GetTokenInformation.restype = ctypes.c_int
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        return ""
    try:
        needed = ctypes.c_uint32()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, needed.value, ctypes.byref(needed)):
            return ""
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        converted = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(converted)):
            return ""
        try:
            return converted.value or ""
        finally:
            kernel32.LocalFree(converted)
    finally:
        kernel32.CloseHandle(token)


def _instance_for_pid(pid: int) -> str | None:
    """Return pid plus process creation time. A reused pid has a new creation time."""
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_instance(pid)
    return _posix_instance(pid)


class _FileTime(ctypes.Structure):
    _fields_ = (("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32))


def _windows_instance(pid: int) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, 0, pid)
    if not handle:
        return None
    try:
        created = _FileTime()
        unused = _FileTime()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(unused),
            ctypes.byref(unused),
            ctypes.byref(unused),
        )
        if not ok:
            return None
        stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return f"{pid}:{stamp}"
    finally:
        kernel32.CloseHandle(handle)


def _posix_instance(pid: int) -> str | None:
    stat_path = Path(f"/proc/{pid}/stat")
    boot_path = Path("/proc/sys/kernel/random/boot_id")
    try:
        fields = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        start_ticks = fields[19]
        boot = boot_path.read_text(encoding="utf-8").strip() if boot_path.exists() else "noboot"
    except (OSError, IndexError):
        return None
    return f"{pid}:{boot}:{start_ticks}"


def current_process_instance() -> str:
    identity = _instance_for_pid(os.getpid())
    if identity is None:
        raise MigrationStoreError("The current process identity could not be read. No schema or data was copied.")
    return identity


def instance_is_alive(stored: str) -> bool:
    if not stored or ":" not in stored:
        return False
    pid_text, _, _stamp = stored.partition(":")
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    return _instance_for_pid(pid) == stored


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MigrationOperationStore:
    """Cross-process operation claims. SQLite serializes writers with BEGIN IMMEDIATE."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else STORE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Lock()
        with self._immediate() as conn:
            self._upgrade(conn)
        _restrict_store_family(self.path)

    def reconcile_interrupted(self) -> int:
        """Mark migrations left running by a dead process. A live process is left alone. This does not rerun work."""
        with self._immediate() as conn:
            rows = conn.execute(
                "SELECT operation_id, owner_instance FROM migration_operations WHERE status = 'running'"
            ).fetchall()
            stale = [row["operation_id"] for row in rows if not instance_is_alive(row["owner_instance"])]
            for operation_id in stale:
                conn.execute(
                    """
                    UPDATE migration_operations
                    SET status = 'reconciliation_required', recovery = ?, updated_at = ?
                    WHERE operation_id = ? AND status = 'running'
                    """,
                    (RECONCILE_TEXT, utc_now(), operation_id),
                )
            return len(stale)

    def get_by_client_request(self, client_request_id: str) -> dict | None:
        key = client_request_id.strip()
        if not key:
            return None
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM migration_operations WHERE client_request_id = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def get(self, operation_id: str) -> dict | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return dict(row) if row else None

    def snapshot(self, destination: Path) -> None:
        """Write a consistent copy. This does not include database passwords."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        with self._session() as source:
            copied = sqlite3.connect(destination)
            try:
                source.backup(copied)
            finally:
                copied.close()
        _restrict_store_family(destination)

    def claim(
        self,
        *,
        operation_id: str,
        client_request_id: str,
        source_database: str,
        target_database: str,
        scope: str,
        strategy: str,
        confirm_retry: bool,
    ) -> tuple[bool, dict]:
        """Reserve an operation. Returns the existing row when the client id was already claimed."""
        key = client_request_id.strip() or None
        now = utc_now()
        with self._immediate() as conn:
            if key:
                existing = conn.execute(
                    "SELECT * FROM migration_operations WHERE client_request_id = ?",
                    (key,),
                ).fetchone()
                if existing:
                    return False, dict(existing)
            try:
                conn.execute(
                    """
                    INSERT INTO migration_operations (
                        operation_id, client_request_id, source_database, target_database,
                        scope, strategy, status, confirm_retry, owner_pid, owner_instance, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        key,
                        source_database,
                        target_database,
                        scope,
                        strategy,
                        int(confirm_retry),
                        os.getpid(),
                        current_process_instance(),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT * FROM migration_operations WHERE client_request_id = ?",
                    (key,),
                ).fetchone()
                if existing:
                    return False, dict(existing)
                raise
            row = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return True, dict(row)

    def bind_target(self, operation_id: str, server_id: str, *, confirm_retry: bool) -> str | None:
        """Claim the target database. Returns a rejection when another operation still owns it."""
        with self._immediate() as conn:
            current = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if current is None:
                return "The migration operation was not found. Nothing new was copied."
            blockers = conn.execute(
                """
                SELECT * FROM migration_operations
                WHERE operation_id != ?
                  AND target_database = ?
                  AND status IN ({})
                  AND (target_server_id = ? OR target_server_id = '')
                """.format(",".join("?" for _ in _BLOCKING)),
                (operation_id, current["target_database"], *_BLOCKING, server_id),
            ).fetchall()
            for blocker in blockers:
                if blocker["status"] == "running":
                    return "A migration is already running for this MySQL database. Nothing new was copied."
                if not blocker["reconciled"]:
                    return (
                        f"Reconcile operation {blocker['operation_id']} before another copy. "
                        f"It is {blocker['status'].replace('_', ' ')}. Nothing new was copied."
                    )
                if not confirm_retry:
                    return "Confirm retry before starting another copy. Nothing new was copied."
            conn.execute(
                "UPDATE migration_operations SET target_server_id = ?, updated_at = ? WHERE operation_id = ?",
                (server_id, utc_now(), operation_id),
            )
        return None

    def finish(self, operation_id: str, *, status: str, summary: str, tables: dict, recovery: str) -> None:
        with self._immediate() as conn:
            conn.execute(
                """
                UPDATE migration_operations
                SET status = ?, summary = ?, tables_json = ?, recovery = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    status,
                    redact_text(summary) or "",
                    json.dumps(tables),
                    redact_text(recovery) or "",
                    utc_now(),
                    operation_id,
                ),
            )

    def fail(self, operation_id: str, summary: str) -> None:
        self.finish(operation_id, status="failed", summary=summary, tables={}, recovery="")

    def request_cancel(self, operation_id: str) -> bool:
        with self._immediate() as conn:
            cursor = conn.execute(
                """
                UPDATE migration_operations
                SET cancel_requested = 1, updated_at = ?
                WHERE operation_id = ? AND status = 'running'
                """,
                (utc_now(), operation_id),
            )
            return cursor.rowcount == 1

    def cancel_requested(self, operation_id: str) -> bool:
        row = self.get(operation_id)
        return bool(row and row["cancel_requested"])

    def reconcile(self, operation_id: str) -> dict | None:
        """Record that an operator reviewed the target. This does not rerun the migration."""
        with self._immediate() as conn:
            current = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] not in _BLOCKING or current["status"] == "running":
                return dict(current)
            conn.execute(
                "UPDATE migration_operations SET reconciled = 1, updated_at = ? WHERE operation_id = ?",
                (utc_now(), operation_id),
            )
            row = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return dict(row)

    def schema_version(self) -> int:
        with self._session() as conn:
            row = conn.execute("SELECT value FROM migration_store_meta WHERE key = 'schema_version'").fetchone()
        return int(row["value"]) if row else 0

    def _upgrade(self, conn: sqlite3.Connection) -> None:
        conn.execute(_META)
        row = conn.execute("SELECT value FROM migration_store_meta WHERE key = 'schema_version'").fetchone()
        version = int(row["value"]) if row else 0
        if version > SCHEMA_VERSION:
            raise MigrationStoreError(
                f"The migration operation store is version {version}. "
                f"This application understands version {SCHEMA_VERSION}. The file was not modified."
            )
        columns = {item[1] for item in conn.execute("PRAGMA table_info(migration_operations)")}
        if not columns:
            conn.execute(_SCHEMA)
        else:
            if "owner_pid" not in columns:
                conn.execute("ALTER TABLE migration_operations ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0")
            if "owner_instance" not in columns:
                conn.execute("ALTER TABLE migration_operations ADD COLUMN owner_instance TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "INSERT INTO migration_store_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _immediate(self):
        return _Immediate(self)


class _Immediate:
    def __init__(self, store: MigrationOperationStore) -> None:
        self._store = store
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._store._thread.acquire()
        self._conn = self._store._connect()
        self._conn.execute("BEGIN IMMEDIATE")
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._conn is not None:
                if exc_type is None:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute("ROLLBACK")
                self._conn.close()
                _restrict_store_family(self._store.path)
        finally:
            self._store._thread.release()
