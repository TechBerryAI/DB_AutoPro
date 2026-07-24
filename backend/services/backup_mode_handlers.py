"""Mode-specific backup execution helpers (incremental / differential)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from backend.connectors.base import get_connector, resolve_connection
from backend.core.exceptions import BackupError
from backend.models.schemas import (
    BackupMethod,
    BackupMode,
    BackupRequest,
    DatabaseType,
    backup_method_category,
)
from backend.services.backup_chain import anchor_for_mode, load_chain, record_backup
from backend.services.pg_tools import resolve_local_pg_tool

# Level 1 keeps custom-format backups small without the heavy CPU cost of level 9.
PG_DUMP_COMPRESS_LEVEL = 1


def _pg_executable(tool: str, cli_not_found_error: Callable[[str], BackupError]) -> str:
    try:
        return resolve_local_pg_tool(tool)
    except FileNotFoundError as exc:
        raise cli_not_found_error(tool) from exc


def _run_pg_dump_command(
    tool: str,
    args: list[str],
    env: dict[str, str],
    output_path: Path,
    *,
    cli_not_found_error: Callable[[str], BackupError],
    progress_callback: Callable[[float, str], None] | None = None,
    expected_bytes: int | None = None,
) -> None:
    """Run pg_dump/pg_dumpall using the resolved binary and optional file-size progress."""
    pg_bin = _pg_executable(tool, cli_not_found_error)
    cmd = [pg_bin, *args]
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if progress_callback:
                elapsed = int(time.monotonic() - started)
                written_mb = 0.0
                if output_path.exists():
                    written_mb = output_path.stat().st_size / (1024 * 1024)
                if expected_bytes and expected_bytes > 0:
                    expected_mb = expected_bytes / (1024 * 1024)
                    ratio = min(1.0, (written_mb * 1024 * 1024) / expected_bytes)
                    progress = min(0.92, 0.08 + ratio * 0.84)
                    message = (
                        f"Running {tool}... {written_mb:.1f} / ~{expected_mb:.0f} MB ({elapsed}s)"
                    )
                else:
                    progress = min(0.92, 0.08 + written_mb / 120.0)
                    message = f"Running {tool}... {written_mb:.1f} MB written ({elapsed}s)"
                progress_callback(progress, message)
            time.sleep(0.8)
    finally:
        if proc.poll() is None:
            proc.wait()

    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        detail = stderr.strip() or f"{tool} exited with code {proc.returncode}"
        raise BackupError(f"{tool} failed: {detail}")


def _pg_env(profile: str) -> dict[str, str]:
    conn = resolve_connection(profile)
    return {**os.environ, "PGPASSWORD": conn.password}


def _pg_query(profile: str, query: str) -> list[dict]:
    connector = get_connector(DatabaseType.POSTGRESQL, profile)
    return connector.execute_query(query)


def _pg_setting(profile: str, name: str) -> str:
    rows = _pg_query(
        profile,
        f"SELECT setting FROM pg_settings WHERE name = '{name}'",
    )
    return str(rows[0]["setting"]) if rows else ""


def _pg_current_lsn(profile: str) -> str:
    rows = _pg_query(profile, "SELECT pg_current_wal_lsn()::text AS lsn")
    return str(rows[0]["lsn"]) if rows else ""


def _pg_database_size_bytes(profile: str, database: str) -> int | None:
    """Return on-disk database size in bytes for progress estimation."""
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database or ""):
        return None
    try:
        rows = _pg_query(
            profile,
            f"SELECT pg_database_size('{database}') AS size",
        )
        if rows and rows[0].get("size") is not None:
            return int(rows[0]["size"])
    except Exception:
        return None
    return None


def backup_postgresql(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None,
    *,
    cli_not_found_error: Callable[[str], BackupError],
) -> Path:
    if request.method == BackupMethod.PG_BASEBACKUP:
        return _postgresql_basebackup(request, output_path, cli_not_found_error)
    if request.backup_mode == BackupMode.FULL:
        from backend.connectors.base import resolve_connection
        from backend.services.ssh_client import ssh_options_from_connection

        conn = resolve_connection(request.profile)
        ssh = ssh_options_from_connection(conn)
        if ssh and request.method in (BackupMethod.PG_DUMP, BackupMethod.PG_DUMPALL):
            return _postgresql_logical_full_via_ssh(
                request,
                output_path,
                conn,
                ssh,
                cli_not_found_error,
                progress_callback,
            )
        return _postgresql_logical_full(
            request, output_path, cli_not_found_error, progress_callback
        )
    return _postgresql_wal_mode(
        request, output_path, progress_callback, cli_not_found_error
    )


def _postgresql_basebackup(
    request: BackupRequest,
    output_path: Path,
    cli_not_found_error: Callable[[str], BackupError],
) -> Path:
    conn = resolve_connection(request.profile)
    env = {"PGPASSWORD": conn.password}
    backup_dir = output_path if output_path.suffix == "" else output_path.with_suffix("")
    backup_dir.mkdir(parents=True, exist_ok=True)
    pg_bin = _pg_executable("pg_basebackup", cli_not_found_error)
    cmd = [
        pg_bin,
        "-h", conn.host,
        "-p", str(conn.port),
        "-U", conn.username,
        "-D", str(backup_dir),
        "-Ft", "-z", "-P",
    ]
    if request.backup_mode == BackupMode.INCREMENTAL:
        cmd.extend(["-X", "stream"])
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise cli_not_found_error("pg_basebackup") from exc
    except subprocess.CalledProcessError as exc:
        raise BackupError(f"pg_basebackup failed: {exc.stderr}") from exc
    record_backup(
        output_path.parent,
        request,
        {"path": str(backup_dir), "lsn": _pg_current_lsn(request.profile)},
        request.backup_mode,
    )
    return backup_dir


def _postgresql_logical_full(
    request: BackupRequest,
    output_path: Path,
    cli_not_found_error: Callable[[str], BackupError],
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    conn = resolve_connection(request.profile)
    env = {**os.environ, "PGPASSWORD": conn.password}
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if request.method == BackupMethod.PG_DUMPALL:
        tool = "pg_dumpall"
        args = [
            "-h", conn.host,
            "-p", str(conn.port),
            "-U", conn.username,
            "-f", str(output_path),
        ]
    else:
        tool = "pg_dump"
        db = request.database or conn.database
        args = [
            "-h", conn.host,
            "-p", str(conn.port),
            "-U", conn.username,
            "-d", db,
            "-f", str(output_path),
            "--format=custom",
            f"--compress={PG_DUMP_COMPRESS_LEVEL}",
        ]

    expected_bytes = None
    if tool == "pg_dump":
        db_name = request.database or conn.database
        expected_bytes = _pg_database_size_bytes(request.profile, db_name)

    if progress_callback:
        if expected_bytes:
            expected_mb = expected_bytes / (1024 * 1024)
            progress_callback(0.08, f"Running {tool}... (~{expected_mb:.0f} MB database)")
        else:
            progress_callback(0.08, f"Running {tool}...")
    _run_pg_dump_command(
        tool,
        args,
        env,
        output_path,
        cli_not_found_error=cli_not_found_error,
        progress_callback=progress_callback,
        expected_bytes=expected_bytes,
    )
    if progress_callback:
        progress_callback(0.9, f"{tool} completed")
    _record_postgresql_chain(request, output_path.parent, str(output_path))
    return output_path


def _postgresql_logical_full_via_ssh(
    request: BackupRequest,
    output_path: Path,
    conn,
    ssh: dict,
    cli_not_found_error: Callable[[str], BackupError],
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    """Run pg_dump on the Linux server, then download the archive to this PC."""
    from backend.services.ssh_client import (
        _ssh_kwargs,
        build_pg_dump_remote_command,
        download_remote_file,
        pg_endpoint_from_connection,
        remote_file_size,
        remove_remote_path,
        resolve_remote_pg_dump_path,
        run_ssh_command,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pg_host, pg_port = pg_endpoint_from_connection(conn)
    db = request.database or conn.database
    dumpall = request.method == BackupMethod.PG_DUMPALL
    remote_name = output_path.name if output_path.suffix else f"{output_path.name}.dump"
    remote_path = f"/tmp/{remote_name}"

    if progress_callback:
        progress_callback(0.12, "Locating pg_dump on Linux server...")
    try:
        pg_dump_path = resolve_remote_pg_dump_path(ssh, None)
    except RuntimeError as exc:
        raise BackupError(str(exc)) from exc

    expected_bytes = None if dumpall else _pg_database_size_bytes(request.profile, db)

    command = build_pg_dump_remote_command(
        pg_dump_path=pg_dump_path,
        pg_host=pg_host,
        pg_port=pg_port,
        username=conn.username,
        password=conn.password,
        output_path=remote_path,
        database=db,
        dumpall=dumpall,
        compress_level=PG_DUMP_COMPRESS_LEVEL,
    )

    if progress_callback:
        label = "pg_dumpall" if dumpall else "pg_dump"
        if expected_bytes:
            expected_mb = expected_bytes / (1024 * 1024)
            progress_callback(0.18, f"Running {label} on Linux server (~{expected_mb:.0f} MB DB)...")
        else:
            progress_callback(0.18, f"Running {label} on Linux server...")

    started = time.monotonic()
    exit_code, _stdout, stderr = run_ssh_command(command=command, timeout=7200, **_ssh_kwargs(ssh))
    if exit_code != 0:
        remove_remote_path(ssh, remote_path)
        detail = stderr.strip() or "pg_dump failed on Linux server"
        raise BackupError(detail)

    if progress_callback:
        elapsed = int(time.monotonic() - started)
        remote_mb = remote_file_size(ssh, remote_path) / (1024 * 1024)
        progress_callback(0.72, f"Downloading backup ({remote_mb:.1f} MB, server finished in {elapsed}s)...")

    try:
        download_remote_file(ssh, remote_path, output_path)
    finally:
        remove_remote_path(ssh, remote_path)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise BackupError("Backup download completed but the local file is empty.")

    if progress_callback:
        local_mb = output_path.stat().st_size / (1024 * 1024)
        progress_callback(0.92, f"Backup saved ({local_mb:.1f} MB)")

    if not dumpall:
        _record_postgresql_chain(request, output_path.parent, str(output_path))
    return output_path


def _postgresql_full(
    request: BackupRequest,
    output_path: Path,
    cli_not_found_error: Callable[[str], BackupError],
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    return _postgresql_logical_full(
        request, output_path, cli_not_found_error, progress_callback
    )


def _postgresql_wal_mode(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None,
    cli_not_found_error: Callable[[str], BackupError],
) -> Path:
    wal_level = _pg_setting(request.profile, "wal_level")
    if wal_level == "minimal":
        raise BackupError(
            "PostgreSQL wal_level must be 'replica' or 'logical' for incremental/differential "
            "backups. Set wal_level in postgresql.conf and restart the server."
        )

    base_dir = output_path.parent
    chain = load_chain(base_dir, request)
    anchor = anchor_for_mode(chain, request.backup_mode)
    if not anchor:
        raise BackupError(
            f"Run a Full backup first before starting a {request.backup_mode.value} backup."
        )

    start_lsn = anchor.get("lsn")
    if not start_lsn:
        raise BackupError(
            "Previous backup is missing WAL position metadata. Run a new Full backup first."
        )

    if progress_callback:
        progress_callback(0.35, "Archiving WAL segments...")

    conn = resolve_connection(request.profile)
    _pg_query(request.profile, "SELECT pg_switch_wal()")

    wal_dir = output_path if not output_path.suffix else output_path.with_suffix("")
    wal_dir.mkdir(parents=True, exist_ok=True)

    env = _pg_env(request.profile)
    pg_receivewal = _pg_executable("pg_receivewal", cli_not_found_error)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            [
                pg_receivewal,
                "-h", conn.host,
                "-p", str(conn.port),
                "-U", conn.username,
                "-D", str(wal_dir),
                "--no-sync",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(4)
        proc.terminate()
        proc.wait(timeout=8)
    except FileNotFoundError as exc:
        raise cli_not_found_error("pg_receivewal") from exc
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
    finally:
        if proc and proc.poll() is None:
            proc.kill()

    end_lsn = _pg_current_lsn(request.profile)
    manifest = {
        "backup_mode": request.backup_mode.value,
        "category": backup_method_category(request.method).value,
        "method": request.method.value,
        "start_lsn": start_lsn,
        "end_lsn": end_lsn,
        "database": request.database or conn.database,
    }
    (wal_dir / "backup_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    record_backup(
        base_dir,
        request,
        {"path": str(wal_dir), "lsn": end_lsn, **manifest},
        request.backup_mode,
    )
    return wal_dir


def _record_postgresql_chain(
    request: BackupRequest,
    base_dir: Path,
    path: str,
) -> None:
    lsn = _pg_current_lsn(request.profile)
    record_backup(
        base_dir,
        request,
        {"path": path, "lsn": lsn, "database": request.database},
        BackupMode.FULL,
    )


def backup_mysql(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None,
) -> Path:
    if request.backup_mode == BackupMode.FULL:
        return _mysql_full(request, output_path)
    return _mysql_incremental(request, output_path, progress_callback)


def _mysql_full(request: BackupRequest, output_path: Path) -> Path:
    conn = resolve_connection(request.profile)
    db = request.database or conn.database
    cmd = [
        "mysqldump",
        "-h", conn.host,
        "-P", str(conn.port),
        "-u", conn.username,
        f"-p{conn.password}",
        db,
        "--single-transaction",
        "--flush-logs",
        "--master-data=2",
        "--result-file", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"mysqldump failed: {result.stderr}")

    status = _mysql_binlog_status(request.profile)
    record_backup(
        output_path.parent,
        request,
        {
            "path": str(output_path),
            "log_file": status["log_file"],
            "log_pos": status["log_pos"],
            "database": db,
        },
        BackupMode.FULL,
    )
    return output_path


def _mysql_binlog_status(profile: str) -> dict[str, str | int]:
    connector = get_connector(DatabaseType.MYSQL, profile)
    rows = connector.execute_query("SHOW BINARY LOG STATUS")
    if not rows:
        rows = connector.execute_query("SHOW MASTER STATUS")
    if not rows:
        raise BackupError("Could not read MySQL binary log status.")
    row = rows[0]
    file_key = "File" if "File" in row else "Log_name"
    pos_key = "Position" if "Position" in row else "Pos"
    return {"log_file": str(row[file_key]), "log_pos": int(row[pos_key])}


def _mysql_incremental(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None,
) -> Path:
    base_dir = output_path.parent
    chain = load_chain(base_dir, request)
    anchor = anchor_for_mode(chain, request.backup_mode)
    if not anchor:
        raise BackupError("Run a Full backup first before an incremental backup.")

    start_file = str(anchor.get("log_file", ""))
    start_pos = int(anchor.get("log_pos", 0))
    if not start_file:
        raise BackupError(
            "Previous full backup is missing binlog coordinates. Run a new Full backup first."
        )

    connector = get_connector(DatabaseType.MYSQL, request.profile)
    connector.execute_script("FLUSH BINARY LOGS")
    end_status = _mysql_binlog_status(request.profile)

    conn = resolve_connection(request.profile)
    inc_path = output_path.with_name(f"{output_path.stem}_incremental.sql")

    if progress_callback:
        progress_callback(0.4, "Exporting binary log changes...")

    if not _mysql_export_binlog(
        request.profile,
        conn,
        start_file,
        start_pos,
        end_status["log_file"],
        int(end_status["log_pos"]),
        inc_path,
    ):
        raise BackupError(
            "Could not export incremental binlog SQL. Ensure binary logging is enabled "
            "(log_bin=ON) and the account has REPLICATION CLIENT privilege."
        )

    record_backup(
        base_dir,
        request,
        {
            "path": str(inc_path),
            "log_file": end_status["log_file"],
            "log_pos": end_status["log_pos"],
            "start_log_file": start_file,
            "start_log_pos": start_pos,
        },
        BackupMode.INCREMENTAL,
    )
    return inc_path


def _mysql_binlog_file_list(profile: str) -> list[str]:
    connector = get_connector(DatabaseType.MYSQL, profile)
    rows = connector.execute_query("SHOW BINARY LOGS")
    if not rows:
        rows = connector.execute_query("SHOW MASTER LOGS")
    key = "Log_name" if rows and "Log_name" in rows[0] else "File"
    return [str(r[key]) for r in rows]


def _mysql_export_binlog(
    profile: str,
    conn,
    start_file: str,
    start_pos: int,
    end_file: str,
    end_pos: int,
    output_path: Path,
) -> bool:
    cmd = [
        "mysqlbinlog",
        "-h", conn.host,
        "-P", str(conn.port),
        "-u", conn.username,
        f"-p{conn.password}",
        f"--start-position={start_pos}",
        f"--stop-position={end_pos}",
        start_file,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return _mysql_export_binlog_via_connector(
            profile, start_file, start_pos, end_file, end_pos, output_path
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError("mysqlbinlog timed out while exporting incremental changes.") from exc

    if result.returncode != 0 or not result.stdout.strip():
        return _mysql_export_binlog_via_connector(
            profile, start_file, start_pos, end_file, end_pos, output_path
        )

    output_path.write_text(result.stdout.strip() + "\n", encoding="utf-8")
    return True


def _mysql_export_binlog_via_connector(
    profile: str,
    start_file: str,
    start_pos: int,
    end_file: str,
    end_pos: int,
    output_path: Path,
) -> bool:
    connector = get_connector(DatabaseType.MYSQL, profile)
    binlog_format = connector.execute_query("SHOW VARIABLES LIKE 'binlog_format'")
    fmt = str(binlog_format[0]["Value"]).upper() if binlog_format else "ROW"
    if fmt != "STATEMENT":
        return False

    lines: list[str] = ["-- MySQL incremental backup (statement binlog)\n"]
    files = _mysql_binlog_file_list(profile)
    if start_file not in files or end_file not in files:
        return False
    start_idx = files.index(start_file)
    end_idx = files.index(end_file)

    for file_name in files[start_idx : end_idx + 1]:
        pos = start_pos if file_name == start_file else 4
        stop = end_pos if file_name == end_file else None
        while True:
            rows = connector.execute_query(
                f"SHOW BINLOG EVENTS IN '{file_name}' FROM {pos} LIMIT 200"
            )
            if not rows:
                break
            for row in rows:
                end_lp = int(row.get("End_log_pos", pos))
                event_type = str(row.get("Event_type", ""))
                info = str(row.get("Info", "") or "").strip()
                if event_type == "Query" and info and info.upper() not in {"BEGIN", "COMMIT"}:
                    lines.append(info if info.endswith(";") else f"{info};")
                pos = end_lp
                if stop is not None and end_lp >= stop:
                    break
            else:
                if len(rows) < 200:
                    break
                continue
            break

    body = "\n".join(lines).strip()
    if len(body) < 20:
        return False
    output_path.write_text(body + "\n", encoding="utf-8")
    return True


def backup_oracle(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None,
) -> Path:
    conn = resolve_connection(request.profile)
    base_dir = output_path.parent

    if request.backup_mode == BackupMode.FULL:
        cmd = [
            "expdp",
            f"{conn.username}/{conn.password}@{conn.host}:{conn.port}/{conn.database}",
            "DIRECTORY=DATA_PUMP_DIR",
            f"DUMPFILE={output_path.name}",
            "FULL=Y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise BackupError(f"Oracle expdp failed: {result.stderr}")
        scn = _oracle_current_scn(request.profile)
        record_backup(
            base_dir,
            request,
            {"path": str(output_path), "scn": scn},
            BackupMode.FULL,
        )
        return output_path

    chain = load_chain(base_dir, request)
    anchor = anchor_for_mode(chain, request.backup_mode)
    if not anchor or not anchor.get("scn"):
        raise BackupError("Run a Full backup first before an incremental backup.")

    inc_name = output_path.with_name(f"{output_path.stem}_incremental.dmp").name
    cmd = [
        "expdp",
        f"{conn.username}/{conn.password}@{conn.host}:{conn.port}/{conn.database}",
        "DIRECTORY=DATA_PUMP_DIR",
        f"DUMPFILE={inc_name}",
        "FULL=N",
        "INCREMENTAL=YES",
        f"FLASHBACK_SCN={anchor['scn']}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"Oracle incremental expdp failed: {result.stderr}")

    inc_path = output_path.with_name(inc_name)
    scn = _oracle_current_scn(request.profile)
    record_backup(
        base_dir,
        request,
        {"path": str(inc_path), "scn": scn, "base_scn": anchor["scn"]},
        BackupMode.INCREMENTAL,
    )
    return inc_path


def _oracle_current_scn(profile: str) -> str:
    connector = get_connector(DatabaseType.ORACLE, profile)
    rows = connector.execute_query("SELECT current_scn FROM v$database")
    if not rows:
        return ""
    row = rows[0]
    return str(row.get("CURRENT_SCN") or row.get("current_scn") or "")
