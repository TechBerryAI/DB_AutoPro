"""Mode-specific backup execution helpers (incremental / differential)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
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

# Level 6 keeps custom-format backups much smaller than level 1 with acceptable CPU cost.
PG_DUMP_COMPRESS_LEVEL = 6


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
                    if output_path.is_file():
                        written_mb = output_path.stat().st_size / (1024 * 1024)
                    elif output_path.is_dir():
                        written_mb = sum(
                            p.stat().st_size for p in output_path.rglob("*") if p.is_file()
                        ) / (1024 * 1024)
                # Report only the growing backup file size (not database size estimates).
                progress = min(0.92, 0.08 + min(written_mb / 50.0, 0.80))
                message = f"Running {tool}... backup file {written_mb:.1f} MB ({elapsed}s)"
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
        if request.use_remote_exec:
            raise BackupError(
                "Physical pg_basebackup cannot be saved with Backup destination = Linux Server yet. "
                "Use Logical backup for Linux Server, or switch destination to Local."
            )
        return _postgresql_basebackup(request, output_path, cli_not_found_error)
    if request.backup_mode == BackupMode.FULL:
        from backend.connectors.base import resolve_connection
        from backend.services.ssh_client import ssh_options_for_backup

        conn = resolve_connection(request.profile)
        ssh = ssh_options_for_backup(request, conn)
        if request.use_remote_exec and not ssh:
            raise BackupError(
                "Saving on the Linux server requires SSH. "
                "Use Linux Server (enable SSH on the connection) or "
                "Remote Linux Server (enter SSH host, username, and password)."
            )
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
    env = {
        **os.environ,
        "PGPASSWORD": conn.password,
        "PGCONNECT_TIMEOUT": "20",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if request.method == BackupMethod.PG_DUMPALL:
        tool = "pg_dumpall"
        args = [
            "-h", conn.host,
            "-p", str(conn.port),
            "-U", conn.username,
            "--no-password",
            "-f", str(output_path),
        ]
    else:
        tool = "pg_dump"
        db = request.database or conn.database
        from backend.services.backup_catalog import (
            build_pg_dump_extra_args,
            resolve_pg_dump_format,
        )
        from backend.models.schemas import PgDumpFormat

        # Directory format requires the target path itself to not exist yet.
        if (
            resolve_pg_dump_format(request) == PgDumpFormat.DIRECTORY
            and output_path.exists()
        ):
            raise BackupError(
                f"Directory format output already exists: {output_path}. "
                "Choose a new backup or remove the existing folder."
            )

        args = [
            "-h", conn.host,
            "-p", str(conn.port),
            "-U", conn.username,
            "--no-password",
            "-d", db,
            "-f", str(output_path),
        ]
        args.extend(build_pg_dump_extra_args(request))

    if progress_callback:
        progress_callback(0.08, f"Running {tool}...")
    _run_pg_dump_command(
        tool,
        args,
        env,
        output_path,
        cli_not_found_error=cli_not_found_error,
        progress_callback=progress_callback,
        expected_bytes=None,
    )
    if progress_callback:
        if output_path.is_file():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            progress_callback(0.9, f"{tool} completed — backup file {size_mb:.1f} MB")
        else:
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
    """Run pg_dump on the Linux server; optionally keep the file on the server."""
    import shlex

    from backend.models.schemas import PgDumpFormat
    from backend.services.backup_catalog import (
        build_pg_dump_common_args,
        build_pg_dump_format_args,
        build_pg_dump_scope_args,
        resolve_pg_dump_format,
        resolve_pg_file_extension,
    )
    from backend.services.ssh_client import (
        _ssh_kwargs,
        build_pg_dump_remote_command,
        can_use_postgres_peer_auth,
        download_remote_directory,
        download_remote_file,
        pg_endpoint_from_connection,
        remote_file_size,
        remove_remote_path,
        resolve_remote_pg_dump_path,
        run_ssh_command,
        stream_ssh_stdout_to_file,
    )

    if not request.use_remote_exec:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    pg_host, pg_port = pg_endpoint_from_connection(conn)
    db = request.database or conn.database
    dumpall = request.method == BackupMethod.PG_DUMPALL
    fmt = None if dumpall else resolve_pg_dump_format(request)
    is_directory = fmt == PgDumpFormat.DIRECTORY
    keep_on_server = bool(request.use_remote_exec)
    # Stream archive formats over SSH (one pass). Directory format still uses /tmp + SFTP.
    # When keeping on server, always write to a file path (no stream to this PC).
    use_stream = (not is_directory) and (not keep_on_server)
    # Plain SQL is bulky on the wire; gzip only for transfer, still save as .sql locally.
    wire_gzip = use_stream and (dumpall or fmt == PgDumpFormat.PLAIN)

    if dumpall:
        remote_name = output_path.name if output_path.suffix else f"{output_path.name}.sql"
    elif is_directory:
        remote_name = output_path.name
    else:
        ext = resolve_pg_file_extension(request)
        remote_name = (
            output_path.name
            if output_path.suffix
            else f"{output_path.name}.{ext or 'dump'}"
        )
    if keep_on_server:
        from backend.services.ssh_client import ensure_remote_backup_dir

        try:
            backup_root = ensure_remote_backup_dir(
                ssh,
                request.remote_output_dir or ssh.get("remote_backup_dir"),
            )
        except RuntimeError as exc:
            raise BackupError(str(exc)) from exc
        remote_path = f"{backup_root}/{remote_name}"
    else:
        remote_path = f"/tmp/{remote_name}"

    if is_directory and output_path.exists():
        raise BackupError(
            f"Directory format output already exists: {output_path}. "
            "Choose a new backup or remove the existing folder."
        )

    if progress_callback:
        progress_callback(0.12, "Locating pg_dump on Linux server...")
    try:
        pg_dump_path = resolve_remote_pg_dump_path(ssh, None)
    except RuntimeError as exc:
        raise BackupError(str(exc)) from exc

    # Prefer postgres OS peer auth so limited DB roles do not block table locks.
    peer_auth = False
    try:
        peer_auth = can_use_postgres_peer_auth(ssh)
    except Exception:
        peer_auth = False

    if not use_stream:
        try:
            remove_remote_path(ssh, remote_path)
        except Exception:
            pass

    ssh_user = str((_ssh_kwargs(ssh).get("username") or "")).strip()
    format_args = None if dumpall else build_pg_dump_format_args(request)
    scope_args = None
    if not dumpall:
        scope_args = build_pg_dump_common_args(request) + build_pg_dump_scope_args(request)

    def _build_command(*, use_peer: bool, stream: bool) -> str:
        return build_pg_dump_remote_command(
            pg_dump_path=pg_dump_path,
            pg_host=pg_host,
            pg_port=pg_port,
            username=conn.username,
            password=conn.password,
            output_path="-" if stream else remote_path,
            database=db,
            dumpall=dumpall,
            compress_level=PG_DUMP_COMPRESS_LEVEL,
            format_args=format_args,
            extra_args=scope_args,
            peer_auth=use_peer,
            ssh_username=ssh_user,
            pipe_gzip=bool(stream and wire_gzip),
        )

    def _raise_dump_error(stderr: str) -> None:
        detail = (stderr or "").strip() or "pg_dump failed on Linux server"
        lowered = detail.lower()
        if "permission denied" in lowered:
            detail = (
                f"{detail}\n\n"
                f"The database user '{conn.username}' cannot read one or more tables. "
                "Grant SELECT (or use a superuser), for example:\n"
                f"  GRANT SELECT ON ALL TABLES IN SCHEMA public TO {conn.username};\n"
                "Or connect with Linux root/sudo so backup can run as the postgres OS user."
            )
        elif "password" in lowered or "authentication" in lowered:
            detail = (
                f"{detail}\n\nCheck the PostgreSQL username/password on the server. "
                "Remote pg_dump uses the same DB credentials as your connection."
            )
        elif "could not connect" in lowered or "connection refused" in lowered:
            detail = (
                f"{detail}\n\nPostgreSQL was not reachable from the Linux server at "
                f"{pg_host}:{pg_port}. Confirm PostgreSQL is running and listening locally."
            )
        elif "timeout" in lowered:
            detail = (
                f"{detail}\n\nTimed out connecting to PostgreSQL at {pg_host}:{pg_port} "
                "from the Linux server."
            )
        raise BackupError(detail)

    started = time.monotonic()

    if use_stream:
        if progress_callback:
            label = "pg_dumpall" if dumpall else "pg_dump"
            mode = "as postgres OS user" if peer_auth else f"as DB user '{conn.username}'"
            wire_note = " (gzip on the wire → .sql locally)" if wire_gzip else ""
            progress_callback(
                0.15,
                f"Streaming {label} from Linux server {mode}{wire_note}...",
            )

        def _on_stream_progress(bytes_written: int, elapsed: int, _stderr_tail: str) -> None:
            if not progress_callback:
                return
            mb = bytes_written / (1024 * 1024)
            # Small schema dumps may sit at 0 MB while pg_dump plans/locks; advance
            # with elapsed time so the UI does not look frozen. Byte growth fills
            # the rest of the bar as data streams.
            time_part = min(0.45, (elapsed / 45.0) * 0.45)
            byte_part = min(0.55, (mb / 20.0) * 0.55)
            progress = min(0.90, 0.15 + max(time_part, byte_part))
            rate = (mb / elapsed) if elapsed > 0 else 0.0
            if mb < 0.05 and elapsed >= 2:
                msg = (
                    f"pg_dump running on Linux server... {elapsed}s "
                    "(planning / locking tables — data will start streaming shortly)"
                )
            elif rate >= 0.05:
                msg = f"Streaming backup to this PC... {mb:.1f} MB ({rate:.1f} MB/s, {elapsed}s)"
            else:
                msg = f"Streaming backup to this PC... {mb:.1f} MB received ({elapsed}s)"
            progress_callback(progress, msg)

        command = _build_command(use_peer=peer_auth, stream=True)
        try:
            exit_code, stderr = stream_ssh_stdout_to_file(
                ssh,
                command,
                output_path,
                timeout=7200,
                on_progress=_on_stream_progress,
                gzip_decompress=wire_gzip,
            )
        except RuntimeError as exc:
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            raise BackupError(str(exc)) from exc

        if (
            exit_code != 0
            and not peer_auth
            and "permission denied" in (stderr or "").lower()
        ):
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            if can_use_postgres_peer_auth(ssh):
                if progress_callback:
                    progress_callback(
                        0.18,
                        "DB user lacks table access — retrying stream as postgres OS user...",
                    )
                peer_auth = True
                command = _build_command(use_peer=True, stream=True)
                try:
                    exit_code, stderr = stream_ssh_stdout_to_file(
                        ssh,
                        command,
                        output_path,
                        timeout=7200,
                        on_progress=_on_stream_progress,
                        gzip_decompress=wire_gzip,
                    )
                except RuntimeError as exc:
                    if output_path.exists():
                        try:
                            output_path.unlink()
                        except OSError:
                            pass
                    raise BackupError(str(exc)) from exc

        if exit_code != 0:
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            _raise_dump_error(stderr)

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise BackupError("Streamed backup finished but the local file is empty.")

        if progress_callback:
            local_mb = output_path.stat().st_size / (1024 * 1024)
            elapsed = int(time.monotonic() - started)
            progress_callback(
                0.92,
                f"Backup saved ({local_mb:.1f} MB in {elapsed}s)",
            )
        if not dumpall:
            _record_postgresql_chain(request, output_path.parent, str(output_path))
        return output_path

    # ---- Directory format (or legacy file-to-/tmp path) ----
    command = _build_command(use_peer=peer_auth, stream=False)

    if progress_callback:
        label = "pg_dumpall" if dumpall else "pg_dump"
        mode = "as postgres OS user" if peer_auth else f"as DB user '{conn.username}'"
        progress_callback(0.18, f"Running {label} on Linux server {mode}...")

    latest_dump_bytes: list[int] = [0]
    stop_size_poll = threading.Event()

    def _poll_dump_size() -> None:
        while not stop_size_poll.wait(2.0):
            try:
                latest_dump_bytes[0] = remote_file_size(ssh, remote_path)
            except Exception:
                pass

    size_thread = threading.Thread(target=_poll_dump_size, daemon=True)
    size_thread.start()

    def _on_dump_progress(elapsed: int) -> None:
        if not progress_callback:
            return
        label = "pg_dumpall" if dumpall else "pg_dump"
        written = latest_dump_bytes[0]
        written_mb = written / (1024 * 1024)
        if written > 0:
            progress = min(0.70, 0.18 + min(written_mb / 50.0, 0.50))
            progress_callback(
                progress,
                f"Running {label} on Linux server... backup file {written_mb:.1f} MB ({elapsed}s)",
            )
        else:
            progress = min(0.70, 0.18 + min(elapsed, 900) / 1500.0)
            progress_callback(
                progress,
                f"Running {label} on Linux server... creating backup file ({elapsed}s)",
            )

    try:
        exit_code, _stdout, stderr = run_ssh_command(
            command=command,
            timeout=7200,
            on_progress=_on_dump_progress,
            **_ssh_kwargs(ssh),
        )
    except RuntimeError as exc:
        stop_size_poll.set()
        remove_remote_path(ssh, remote_path)
        raise BackupError(str(exc)) from exc

    if (
        exit_code != 0
        and not peer_auth
        and "permission denied" in (stderr or "").lower()
    ):
        try:
            remove_remote_path(ssh, remote_path)
        except Exception:
            pass
        latest_dump_bytes[0] = 0
        if can_use_postgres_peer_auth(ssh):
            if progress_callback:
                progress_callback(
                    0.20,
                    "DB user lacks table access — retrying as postgres OS user...",
                )
            peer_auth = True
            command = _build_command(use_peer=True, stream=False)
            try:
                exit_code, _stdout, stderr = run_ssh_command(
                    command=command,
                    timeout=7200,
                    on_progress=_on_dump_progress,
                    **_ssh_kwargs(ssh),
                )
            except RuntimeError as exc:
                stop_size_poll.set()
                remove_remote_path(ssh, remote_path)
                raise BackupError(str(exc)) from exc

    stop_size_poll.set()

    if exit_code != 0:
        remove_remote_path(ssh, remote_path)
        _raise_dump_error(stderr)

    dump_bytes = remote_file_size(ssh, remote_path)
    if dump_bytes <= 0:
        remove_remote_path(ssh, remote_path)
        raise BackupError("pg_dump finished but the backup file is missing or empty on the server.")

    if keep_on_server:
        if progress_callback:
            remote_mb = dump_bytes / (1024 * 1024)
            progress_callback(0.95, f"Backup saved on Linux server ({remote_mb:.1f} MB)")
        return Path(remote_path)

    if progress_callback:
        elapsed = int(time.monotonic() - started)
        remote_mb = dump_bytes / (1024 * 1024)
        progress_callback(
            0.72,
            f"Downloading backup file ({remote_mb:.1f} MB, created in {elapsed}s)...",
        )

    def _on_download_progress(transferred: int, total: int) -> None:
        if not progress_callback:
            return
        done_mb = transferred / (1024 * 1024)
        total_mb = (total or transferred) / (1024 * 1024)
        ratio = (transferred / total) if total > 0 else 0.0
        progress = min(0.90, 0.72 + (0.18 * ratio))
        progress_callback(
            progress,
            f"Downloading backup... {done_mb:.1f} / {total_mb:.1f} MB",
        )

    try:
        if is_directory:
            download_remote_directory(ssh, remote_path, output_path)
        else:
            download_remote_file(
                ssh, remote_path, output_path, on_progress=_on_download_progress
            )
    finally:
        remove_remote_path(ssh, remote_path)

    if is_directory:
        if not output_path.is_dir() or not any(output_path.iterdir()):
            raise BackupError("Backup download completed but the local directory is empty.")
    elif not output_path.exists() or output_path.stat().st_size <= 0:
        raise BackupError("Backup download completed but the local file is empty.")

    if progress_callback:
        if is_directory:
            progress_callback(0.92, f"Backup saved to {output_path.name}/")
        else:
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
    if request.method == BackupMethod.MYSQL_PHYSICAL:
        return _mysql_physical(request, output_path, progress_callback)
    if request.backup_mode == BackupMode.FULL:
        return _mysql_full(request, output_path, progress_callback)
    return _mysql_incremental(request, output_path, progress_callback)


def _mysql_full(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    conn = resolve_connection(request.profile)
    from backend.services.ssh_client import ssh_options_for_backup

    ssh = ssh_options_for_backup(request, conn)
    if request.use_remote_exec and not ssh:
        raise BackupError(
            "Saving on the Linux server requires SSH on the connection. "
            "Enable SSH Tunnel and reconnect."
        )
    if ssh:
        return _mysql_full_via_ssh(request, output_path, conn, ssh, progress_callback)
    return _mysql_full_local(request, output_path, conn, progress_callback)


def _mysql_full_local(
    request: BackupRequest,
    output_path: Path,
    conn,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    from backend.services.backup_catalog import (
        build_mysqldump_scope_args,
        mysqldump_table_targets,
        resolve_mysql_backup_scope,
    )
    from backend.models.schemas import MySQLBackupScope

    db = request.database or conn.database
    if not db:
        raise BackupError("Select a MySQL database to back up.")

    scope = resolve_mysql_backup_scope(request)
    tables = mysqldump_table_targets(request)
    if scope == MySQLBackupScope.SELECTED_TABLES and not tables:
        raise BackupError("Selected Tables scope requires at least one table.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Always write plain SQL first; gzip is applied by BackupService when requested.
    sql_path = output_path if output_path.suffix.lower() == ".sql" else output_path.with_suffix(".sql")
    if sql_path.suffix.lower() != ".sql":
        sql_path = Path(str(sql_path) + ".sql")

    cmd = [
        "mysqldump",
        "-h", conn.host,
        "-P", str(conn.port),
        "-u", conn.username,
        f"-p{conn.password}",
        *build_mysqldump_scope_args(request),
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--result-file", str(sql_path),
        db,
        *tables,
    ]
    # Avoid putting password in process list when possible — keep for compatibility.
    if progress_callback:
        progress_callback(0.15, "Running mysqldump...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError as exc:
        raise BackupError(
            "'mysqldump' was not found on this PC. Install MySQL client tools and add them to PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError("mysqldump timed out.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise BackupError(f"mysqldump failed: {detail}")

    _record_mysql_full_chain(request, sql_path, db)
    if progress_callback:
        size_mb = sql_path.stat().st_size / (1024 * 1024) if sql_path.exists() else 0
        progress_callback(0.85, f"mysqldump completed — {size_mb:.1f} MB")
    return sql_path


def _mysql_full_via_ssh(
    request: BackupRequest,
    output_path: Path,
    conn,
    ssh: dict,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    """Run mysqldump on the Linux host; keep on server or download to this PC."""
    import shlex

    from backend.models.schemas import MySQLBackupScope
    from backend.services.backup_catalog import (
        build_mysqldump_scope_args,
        mysqldump_table_targets,
        resolve_mysql_backup_scope,
    )
    from backend.services.ssh_client import (
        _ssh_kwargs,
        download_remote_file,
        pg_endpoint_from_connection,
        remove_remote_path,
        run_ssh_command,
    )

    db = request.database or conn.database
    if not db:
        raise BackupError("Select a MySQL database to back up.")
    scope = resolve_mysql_backup_scope(request)
    tables = mysqldump_table_targets(request)
    if scope == MySQLBackupScope.SELECTED_TABLES and not tables:
        raise BackupError("Selected Tables scope requires at least one table.")

    keep_on_server = bool(request.use_remote_exec)
    if not keep_on_server:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    sql_path = output_path if str(output_path).lower().endswith(".sql") else Path(str(output_path) + ".sql")
    remote_name = sql_path.name
    if keep_on_server:
        from backend.services.ssh_client import ensure_remote_backup_dir

        try:
            backup_root = ensure_remote_backup_dir(
                ssh,
                request.remote_output_dir or ssh.get("remote_backup_dir"),
            )
        except RuntimeError as exc:
            raise BackupError(str(exc)) from exc
        remote_path = f"{backup_root}/{remote_name}"
    else:
        remote_path = f"/tmp/{remote_name}"

    # From the Linux host, MySQL is usually on localhost.
    mysql_host, mysql_port = pg_endpoint_from_connection(conn)
    user = conn.username
    password = conn.password
    scope_flags = " ".join(shlex.quote(a) for a in build_mysqldump_scope_args(request))
    table_args = " ".join(shlex.quote(t) for t in tables)
    common_flags = (
        f"--single-transaction --routines --triggers --events "
        f"--result-file={shlex.quote(remote_path)}"
    )
    db_q = shlex.quote(db)

    # Prefer socket/privileged mysqldump (sees all schemas); fall back to DB user.
    linux_password = str(ssh.get("password") or "")
    candidates = [
        (
            f"mysqldump -h {shlex.quote(mysql_host)} -P {mysql_port} "
            f"{scope_flags} {common_flags} {db_q} {table_args}"
        ).strip(),
        (
            f"sudo -n mysqldump -h {shlex.quote(mysql_host)} -P {mysql_port} "
            f"{scope_flags} {common_flags} {db_q} {table_args}"
        ).strip(),
        (
            f"MYSQL_PWD={shlex.quote(password)} mysqldump "
            f"-h {shlex.quote(mysql_host)} -P {mysql_port} -u {shlex.quote(user)} "
            f"{scope_flags} {common_flags} {db_q} {table_args}"
        ).strip(),
    ]
    if linux_password:
        pw = shlex.quote(linux_password)
        candidates.insert(
            1,
            (
                f"printf '%s\\n' {pw} | sudo -S -p '' mysqldump "
                f"-h {shlex.quote(mysql_host)} -P {mysql_port} "
                f"{scope_flags} {common_flags} {db_q} {table_args}"
            ).strip(),
        )

    if progress_callback:
        progress_callback(0.12, "Running mysqldump on Linux server...")

    remove_remote_path(ssh, remote_path)
    last_err = ""
    ok = False
    for command in candidates:
        try:
            code, _out, err = run_ssh_command(
                command=command,
                timeout=3600,
                **_ssh_kwargs(ssh),
            )
        except Exception as exc:
            last_err = str(exc)
            continue
        if code == 0:
            ok = True
            break
        last_err = (err or "").strip() or f"exit {code}"
        remove_remote_path(ssh, remote_path)

    if not ok:
        raise BackupError(f"Remote mysqldump failed: {last_err}")

    if keep_on_server:
        if progress_callback:
            progress_callback(0.95, f"Backup saved on Linux server: {remote_path}")
        return Path(remote_path)

    if progress_callback:
        progress_callback(0.7, "Downloading MySQL dump to this PC...")
    try:
        download_remote_file(ssh, remote_path, sql_path)
    finally:
        remove_remote_path(ssh, remote_path)

    if not sql_path.exists() or sql_path.stat().st_size <= 0:
        raise BackupError("Remote mysqldump produced an empty file.")

    _record_mysql_full_chain(request, sql_path, db)
    if progress_callback:
        size_mb = sql_path.stat().st_size / (1024 * 1024)
        progress_callback(0.88, f"mysqldump completed — {size_mb:.1f} MB")
    return sql_path


def _record_mysql_full_chain(request: BackupRequest, sql_path: Path, db: str) -> None:
    try:
        status = _mysql_binlog_status(request.profile)
    except Exception:
        status = {"log_file": "", "log_pos": 0}
    record_backup(
        sql_path.parent,
        request,
        {
            "path": str(sql_path),
            "log_file": status.get("log_file", ""),
            "log_pos": status.get("log_pos", 0),
            "database": db,
        },
        BackupMode.FULL,
    )


def _mysql_physical(
    request: BackupRequest,
    output_path: Path,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    """Physical backup via mysqlbackup (MEB) when present, else SSH datadir archive."""
    conn = resolve_connection(request.profile)
    from backend.services.ssh_client import ssh_options_for_backup

    ssh = ssh_options_for_backup(request, conn)
    if ssh:
        return _mysql_physical_via_ssh(request, output_path, conn, ssh, progress_callback)

    # Local MEB attempt
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = output_path if not output_path.suffix else output_path.with_suffix("")
    backup_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "mysqlbackup",
        f"--host={conn.host}",
        f"--port={conn.port}",
        f"--user={conn.username}",
        f"--password={conn.password}",
        f"--backup-dir={backup_dir}",
        "backup",
    ]
    if progress_callback:
        progress_callback(0.2, "Running MySQL Enterprise Backup (mysqlbackup)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    except FileNotFoundError as exc:
        raise BackupError(
            "Physical MySQL backup requires MySQL Enterprise Backup (`mysqlbackup`) "
            "on this PC, or an SSH-enabled connection for a filesystem datadir snapshot."
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BackupError(f"mysqlbackup failed: {detail}")
    record_backup(
        backup_dir.parent,
        request,
        {"path": str(backup_dir), "database": request.database or conn.database, "kind": "mysqlbackup"},
        BackupMode.FULL,
    )
    return backup_dir


def _mysql_physical_via_ssh(
    request: BackupRequest,
    output_path: Path,
    conn,
    ssh: dict,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    """Prefer remote mysqlbackup; fall back to consistent datadir tar snapshot."""
    import shlex

    from backend.services.ssh_client import (
        _ssh_kwargs,
        download_remote_file,
        pg_endpoint_from_connection,
        remove_remote_path,
        run_ssh_command,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    archive = (
        output_path
        if str(output_path).lower().endswith((".tar.gz", ".tgz"))
        else Path(str(output_path) + ".tar.gz")
    )
    remote_archive = f"/tmp/{archive.name}"
    mysql_host, mysql_port = pg_endpoint_from_connection(conn)
    linux_password = str(ssh.get("password") or "")
    user = conn.username
    password = conn.password

    if progress_callback:
        progress_callback(0.1, "Attempting MySQL Enterprise Backup on Linux...")

    meb_dir = f"/tmp/meb_{archive.stem}"
    meb_cmds = [
        (
            f"mysqlbackup --host={shlex.quote(mysql_host)} --port={mysql_port} "
            f"--user={shlex.quote(user)} --password={shlex.quote(password)} "
            f"--backup-dir={shlex.quote(meb_dir)} backup "
            f"&& tar -czf {shlex.quote(remote_archive)} -C {shlex.quote(meb_dir)} . "
            f"&& rm -rf {shlex.quote(meb_dir)}"
        ),
    ]
    remove_remote_path(ssh, remote_archive)
    meb_ok = False
    for command in meb_cmds:
        try:
            code, _out, _err = run_ssh_command(
                command=command,
                timeout=7200,
                **_ssh_kwargs(ssh),
            )
            if code == 0:
                meb_ok = True
                break
        except Exception:
            continue

    if not meb_ok:
        if progress_callback:
            progress_callback(0.25, "Creating filesystem snapshot of MySQL datadir...")
        # Hold FTWRL in the mysql client session while tar runs via \! shell escape.
        remote_archive_q = shlex.quote(remote_archive)
        snapshot = f"""
set -e
DATADIR=$(mysql -Nse 'SELECT @@datadir' 2>/dev/null || sudo -n mysql -Nse 'SELECT @@datadir' 2>/dev/null || true)
if [ -z "$DATADIR" ]; then
  DATADIR=$(printf '%s\\n' {shlex.quote(linux_password)} | sudo -S -p '' mysql -Nse 'SELECT @@datadir' 2>/dev/null || true)
fi
test -n "$DATADIR"
MYSQL_BIN=mysql
if ! command -v mysql >/dev/null 2>&1; then
  echo 'mysql client not found' >&2
  exit 1
fi
$MYSQL_BIN --batch <<EOF
FLUSH TABLES WITH READ LOCK;
\\\\! tar -czf {remote_archive_q} -C "$DATADIR" .
UNLOCK TABLES;
EOF
test -s {remote_archive_q}
""".strip()
        # Also try sudo mysql client for privilege
        snapshot_sudo = f"""
set -e
DATADIR=$(sudo -n mysql -Nse 'SELECT @@datadir')
test -n "$DATADIR"
sudo -n mysql --batch <<EOF
FLUSH TABLES WITH READ LOCK;
\\\\! tar -czf {remote_archive_q} -C "$DATADIR" .
UNLOCK TABLES;
EOF
test -s {remote_archive_q}
""".strip()
        snapshot_sudo_pw = ""
        if linux_password:
            pw = shlex.quote(linux_password)
            snapshot_sudo_pw = f"""
set -e
DATADIR=$(printf '%s\\n' {pw} | sudo -S -p '' mysql -Nse 'SELECT @@datadir')
test -n "$DATADIR"
printf '%s\\n' {pw} | sudo -S -p '' mysql --batch <<EOF
FLUSH TABLES WITH READ LOCK;
\\\\! tar -czf {remote_archive_q} -C "$DATADIR" .
UNLOCK TABLES;
EOF
test -s {remote_archive_q}
""".strip()

        commands = [c for c in (snapshot_sudo, snapshot_sudo_pw, snapshot) if c]
        snap_ok = False
        last_err = ""
        for command in commands:
            try:
                code, _out, err = run_ssh_command(
                    command=command,
                    timeout=7200,
                    **_ssh_kwargs(ssh),
                )
            except Exception as exc:
                last_err = str(exc)
                continue
            if code == 0:
                snap_ok = True
                break
            last_err = (err or "").strip() or f"exit {code}"
            remove_remote_path(ssh, remote_archive)
        if not snap_ok:
            raise BackupError(
                "Physical MySQL backup failed. Install MySQL Enterprise Backup "
                f"(mysqlbackup) on the server, or ensure SSH can archive @@datadir. {last_err}"
            )

    if progress_callback:
        progress_callback(0.75, "Downloading physical MySQL archive...")
    try:
        download_remote_file(ssh, remote_archive, archive)
    finally:
        remove_remote_path(ssh, remote_archive)

    if not archive.exists() or archive.stat().st_size <= 0:
        raise BackupError("Physical MySQL backup archive is empty.")

    record_backup(
        archive.parent,
        request,
        {
            "path": str(archive),
            "database": request.database or conn.database,
            "kind": "mysqlbackup" if meb_ok else "filesystem_snapshot",
        },
        BackupMode.FULL,
    )
    if progress_callback:
        size_mb = archive.stat().st_size / (1024 * 1024)
        progress_callback(0.95, f"Physical backup ready — {size_mb:.1f} MB")
    return archive


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
    from backend.services.ssh_client import ssh_options_for_backup

    conn = resolve_connection(request.profile)
    ssh = ssh_options_for_backup(request, conn)
    if request.use_remote_exec:
        if not ssh:
            raise BackupError(
                "Saving on the Linux server requires SSH on the connection. "
                "Enable SSH Tunnel and reconnect."
            )
        return _oracle_full_via_ssh(request, output_path, conn, ssh, progress_callback)

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


def _oracle_full_via_ssh(
    request: BackupRequest,
    output_path: Path,
    conn,
    ssh: dict,
    progress_callback: Callable[[float, str], None] | None,
) -> Path:
    """Run classic exp on the Linux host into the selected server folder."""
    import shlex

    from backend.services.ssh_client import _ssh_kwargs, run_ssh_command

    backup_root = (request.remote_output_dir or ssh.get("remote_backup_dir") or "/mnt/backup").rstrip("/")
    remote_name = output_path.name if output_path.suffix else f"{output_path.name}.dmp"
    remote_path = f"{backup_root}/{remote_name}"
    host = (conn.extra_options or {}).get("db_host") or conn.host
    port = (conn.extra_options or {}).get("db_port") or conn.port
    service = conn.database
    user = conn.username
    password = conn.password

    if progress_callback:
        progress_callback(0.2, "Creating Oracle dump on Linux server...")

    from backend.services.ssh_client import ensure_remote_backup_dir

    try:
        backup_root = ensure_remote_backup_dir(ssh, backup_root)
    except RuntimeError as exc:
        raise BackupError(str(exc)) from exc
    remote_path = f"{backup_root}/{remote_name}"

    # Classic exp writes directly to a filesystem path (no Oracle DIRECTORY object required).
    connect = f"{user}/{password}@//{host}:{port}/{service}"
    exp_cmd = (
        "bash -lc "
        + shlex.quote(
            "export ORACLE_HOME=${ORACLE_HOME:-/opt/oracle/product/19c/dbhome_1}; "
            "export PATH=$ORACLE_HOME/bin:$PATH; "
            f"exp {shlex.quote(connect)} file={shlex.quote(remote_path)} full=y "
            "consistent=y statistics=none"
        )
    )
    code, out, err = run_ssh_command(command=exp_cmd, timeout=3600, **_ssh_kwargs(ssh))
    if code != 0:
        detail = (err or out or "").strip() or f"exit {code}"
        raise BackupError(f"Oracle remote exp failed: {detail}")

    if progress_callback:
        progress_callback(0.95, f"Backup saved on Linux server: {remote_path}")
    return Path(remote_path)


def _oracle_current_scn(profile: str) -> str:
    connector = get_connector(DatabaseType.ORACLE, profile)
    rows = connector.execute_query("SELECT current_scn FROM v$database")
    if not rows:
        return ""
    row = rows[0]
    return str(row.get("CURRENT_SCN") or row.get("current_scn") or "")
