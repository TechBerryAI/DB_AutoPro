"""Backup file checks and disposable restore verification.

A backup file, a recognized dump header, or a backup command that exits
successfully does not prove that the backup can be restored.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_tools import is_pg_custom_dump, resolve_local_pg_tool

DISPOSABLE_RESTORE_DATABASE = "db_autopro_migration_restore"
_PLAIN_MARKERS = (b"PostgreSQL database dump", b"-- PostgreSQL database dump")


@dataclass
class BackupReadiness:
    file_exists: bool = False
    format_checked: bool = False
    restore_verified: bool = False
    file_summary: str = "No backup file was selected."
    format_summary: str = "Backup format and completion were not checked."
    restore_summary: str = "Restore has not been proven."
    sha256: str = ""


def inspect_backup_file(path: Path | None) -> BackupReadiness:
    """Report whether a file exists and whether its dump header was recognized.

    This never sets restore_verified.
    """
    readiness = BackupReadiness()
    if path is None or not path.is_file():
        return readiness
    readiness.file_exists = True
    size = path.stat().st_size
    readiness.sha256 = _sha256(path)
    readiness.file_summary = f"Backup file exists ({size} bytes). Existence is not a restore test."
    if size <= 0:
        readiness.format_summary = "The backup file is empty, so format and completion were not checked."
        return readiness
    if is_pg_custom_dump(path) or _plain_dump_header(path):
        readiness.format_checked = True
        readiness.format_summary = (
            "Backup format was recognized and the file is non-empty. "
            "A successful backup command is not a completed restore."
        )
        return readiness
    readiness.format_summary = "The file exists, but its PostgreSQL dump format was not recognized."
    return readiness


def verify_postgresql_restore(
    *,
    source_host: str,
    source_port: int,
    source_user: str,
    source_password: str,
    source_database: str,
    target_host: str,
    target_port: int,
    target_user: str,
    target_password: str,
    target_database: str,
    backup_path: Path,
    target_is_empty,
    target_query,
    expectations: list[tuple[str, str]],
    command_runner=None,
    pg_dump: str | None = None,
    pg_restore: str | None = None,
) -> BackupReadiness:
    """Dump the source and restore only into the named disposable database.

    expectations are (sql, expected text) checked on the restored database.
    The restore is verified only when every expectation matches.
    """
    if target_database != DISPOSABLE_RESTORE_DATABASE:
        readiness = BackupReadiness()
        readiness.restore_summary = (
            "Restore was not run. The target database is not the disposable restore database, "
            "so restore has not been proven."
        )
        return readiness
    if (source_host, int(source_port), source_database) == (target_host, int(target_port), target_database):
        readiness = BackupReadiness()
        readiness.restore_summary = "Restore was not run because the source and target database are the same."
        return readiness
    if not target_is_empty():
        readiness = BackupReadiness()
        readiness.restore_summary = (
            "Restore was not run because the disposable database is not empty. "
            "Existing objects were not overwritten. Restore has not been proven."
        )
        return readiness

    runner = command_runner or _run
    dump_env = {**os.environ, "PGPASSWORD": source_password, "PGCONNECT_TIMEOUT": "20"}
    try:
        pg_dump = pg_dump or resolve_local_pg_tool("pg_dump")
        pg_restore = pg_restore or resolve_local_pg_tool("pg_restore")
    except FileNotFoundError as exc:
        readiness = BackupReadiness()
        readiness.restore_summary = f"Restore was not run because {exc} is not available. Restore has not been proven."
        return readiness

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    dump = runner(
        [
            pg_dump, "-h", source_host, "-p", str(source_port), "-U", source_user, "--no-password",
            "-d", source_database, "-Fc", "-f", str(backup_path),
        ],
        dump_env,
    )
    readiness = inspect_backup_file(backup_path)
    if dump.returncode != 0 or not readiness.format_checked:
        readiness.restore_verified = False
        readiness.restore_summary = (
            "The backup command did not produce a recognized dump. Restore has not been proven. "
            + _redact(dump.stderr, source_password, target_password)
        ).strip()
        return readiness

    restore_env = {**os.environ, "PGPASSWORD": target_password, "PGCONNECT_TIMEOUT": "20"}
    restored = runner(
        [
            pg_restore, "-h", target_host, "-p", str(target_port), "-U", target_user, "--no-password",
            "-d", target_database, "--no-owner", "--no-privileges", str(backup_path),
        ],
        restore_env,
    )
    if restored.returncode != 0:
        readiness.restore_verified = False
        readiness.restore_summary = (
            "The restore command did not exit successfully. Restore has not been proven. "
            + _redact(restored.stderr, source_password, target_password)
        ).strip()
        return readiness

    mismatches = _match_expectations(expectations, target_query)
    if mismatches:
        readiness.restore_verified = False
        readiness.restore_summary = (
            "The restore command exited successfully, but verification did not pass. "
            "Restore has not been proven. " + "; ".join(mismatches)
        )
        return readiness
    readiness.restore_verified = True
    readiness.restore_summary = (
        "Restore was successfully tested and verified on the disposable database. "
        "This does not prove a different target can be restored."
    )
    return readiness


def _match_expectations(expectations: list[tuple[str, str]], query) -> list[str]:
    mismatches = []
    for sql, expected in expectations:
        try:
            rows = query(sql)
        except Exception as exc:
            mismatches.append(_redact(str(exc), "", ""))
            continue
        if not rows:
            mismatches.append(f"No row for check {sql[:40]}")
            continue
        value = next(iter(rows[0].values()))
        if str(value) != str(expected):
            mismatches.append(f"Expected {expected}, observed {value}")
    return mismatches


def _plain_dump_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(256)
    except OSError:
        return False
    return any(marker in sample for marker in _PLAIN_MARKERS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact(text: str, *secrets: str) -> str:
    cleaned = redact_text(text or "") or ""
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=env, capture_output=True, text=True)
