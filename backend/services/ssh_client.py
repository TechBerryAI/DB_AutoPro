"""SSH command execution for remote database operations."""

from __future__ import annotations

import shlex
import shutil
import socket
import stat
from pathlib import Path
from typing import Any, Callable


def resolve_ssh_host(ssh_host: str | None, fallback_host: str) -> str:
    """Resolve SSH hostname, falling back to the database host IP if needed."""
    candidates: list[str] = []
    for host in (ssh_host, fallback_host):
        if host and host.strip() and host.strip() not in candidates:
            candidates.append(host.strip())

    last_error: Exception | None = None
    for host in candidates:
        try:
            socket.getaddrinfo(host, 22, type=socket.SOCK_STREAM)
            return host
        except socket.gaierror as exc:
            last_error = exc

    tried = ", ".join(candidates) if candidates else "empty"
    raise RuntimeError(
        f"Cannot resolve Linux host ({tried}). "
        f"Use the server IP address (e.g. {fallback_host}) in the Linux host field."
    ) from last_error


def run_ssh_command(
    *,
    ssh_host: str | None,
    fallback_host: str,
    ssh_port: int,
    username: str,
    password: str,
    command: str,
    timeout: int = 600,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[int, str, str]:
    """Run a command on a remote Linux host over SSH.

    on_progress receives elapsed seconds while the remote command is still running.
    """
    import select
    import time

    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is not installed. Run: pip install paramiko") from exc

    resolved_host = resolve_ssh_host(ssh_host, fallback_host)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=resolved_host,
            port=ssh_port,
            username=username,
            password=password or None,
            allow_agent=False,
            look_for_keys=False,
            timeout=15,
            banner_timeout=30,
            auth_timeout=30,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        channel = stdout.channel
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        started = time.monotonic()
        deadline = started + max(1, timeout)
        last_progress_at = 0.0

        # Drain both streams while waiting so full buffers cannot deadlock
        # recv_exit_status() (common Paramiko hang with long commands).
        while True:
            now = time.monotonic()
            if now > deadline:
                try:
                    channel.close()
                except Exception:
                    pass
                raise RuntimeError(
                    f"SSH command timed out after {timeout}s. "
                    "The remote command may still be running on the server."
                )

            if channel.recv_ready():
                chunk = channel.recv(65536)
                if chunk:
                    stdout_chunks.append(chunk)
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(65536)
                if chunk:
                    stderr_chunks.append(chunk)

            if on_progress and (now - last_progress_at) >= 1.5:
                on_progress(int(now - started))
                last_progress_at = now

            if channel.exit_status_ready():
                while channel.recv_ready():
                    chunk = channel.recv(65536)
                    if not chunk:
                        break
                    stdout_chunks.append(chunk)
                while channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(65536)
                    if not chunk:
                        break
                    stderr_chunks.append(chunk)
                break

            transport = channel.get_transport()
            if transport is not None:
                try:
                    select.select([channel], [], [], 0.5)
                except (OSError, ValueError, TypeError):
                    time.sleep(0.25)
            else:
                time.sleep(0.25)

        exit_code = channel.recv_exit_status()
        out_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        return exit_code, out_text, err_text
    except paramiko.AuthenticationException as exc:
        raise RuntimeError(f"SSH authentication failed for Linux user '{username}'.") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    finally:
        client.close()


def ssh_options_from_connection(conn: Any) -> dict[str, Any]:
    """Extract SSH settings stored on a resolved connection profile."""
    opts = conn.extra_options or {}
    if not opts.get("ssh_enabled"):
        return {}
    db_host = opts.get("db_host") or opts.get("mongo_host") or conn.host
    return {
        "ssh_host": opts.get("ssh_host") or db_host,
        "fallback_host": db_host,
        "ssh_port": int(opts.get("ssh_port", 22)),
        "username": opts.get("linux_username", ""),
        "password": opts.get("linux_password", ""),
        "mongodump_path": opts.get(
            "mongodump_path",
            "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump",
        ),
        "remote_backup_dir": opts.get("remote_backup_dir", "/mnt/backup"),
    }


def pg_endpoint_from_connection(conn: Any) -> tuple[str, int]:
    """PostgreSQL host/port as seen from the Linux SSH server."""
    opts = conn.extra_options or {}
    db_host = str(opts.get("db_host") or opts.get("mongo_host") or conn.host)
    db_port = int(opts.get("db_port") or opts.get("mongo_port") or conn.port)
    ssh_host = str(opts.get("ssh_host") or db_host)
    if db_host in ("127.0.0.1", "localhost") or ssh_host == db_host:
        return "127.0.0.1", db_port
    return db_host, db_port


def mongo_endpoint_from_connection(conn: Any) -> tuple[str, int]:
    """Return the real MongoDB host/port (not the local SSH tunnel endpoint)."""
    opts = conn.extra_options or {}
    host = opts.get("mongo_host") or conn.host
    port = int(opts.get("mongo_port", conn.port))
    return str(host), port


def remote_backup_folder_name(custom_name: str | None = None, db_name: str = "all") -> str:
    """Build a timestamped backup folder name, optionally using a custom prefix."""
    from datetime import datetime
    import re

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if custom_name and custom_name.strip():
        raw = custom_name.strip()
        if re.search(r"<timestamp>", raw, re.IGNORECASE):
            named = re.sub(r"<timestamp>", timestamp, raw, flags=re.IGNORECASE)
            return re.sub(r'[<>:"/\\|?*]+', "_", named)
        base = re.sub(r'[<>:"/\\|?*\s]+', "_", raw).strip("._")
        base = base or "db_backup"
        return f"{base}_{timestamp}"
    return f"db_backup_{timestamp}"


def _ssh_kwargs(ssh: dict[str, Any]) -> dict[str, Any]:
    return {
        "ssh_host": ssh["ssh_host"],
        "fallback_host": ssh["fallback_host"],
        "ssh_port": ssh["ssh_port"],
        "username": ssh["username"],
        "password": ssh["password"],
    }


def resolve_remote_mongodump_path(ssh: dict[str, Any], preferred_path: str | None = None) -> str:
    """Locate a working mongodump binary on the Linux server over SSH."""
    kwargs = _ssh_kwargs(ssh)
    candidates: list[str] = []

    if preferred_path and preferred_path.strip():
        candidates.append(preferred_path.strip())

    default_glob = "/mnt/mongodb-database-tools-*/bin/mongodump"
    if default_glob not in candidates:
        candidates.append(default_glob)

    for candidate in candidates:
        if "*" in candidate:
            cmd = f"ls -1 {candidate} 2>/dev/null | head -n 1"
        else:
            cmd = f"test -x {shlex.quote(candidate)} && echo {shlex.quote(candidate)}"
        exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=30, **kwargs)
        if exit_code != 0:
            continue
        for line in stdout.splitlines():
            path = line.strip()
            if path:
                return path

    discovery_cmds = (
        "command -v mongodump 2>/dev/null",
        "ls -1 /mnt/mongodb-database-tools-*/bin/mongodump 2>/dev/null | head -n 1",
        "find /mnt /usr /opt -maxdepth 6 -name mongodump -type f -perm -111 2>/dev/null | head -n 1",
    )
    for cmd in discovery_cmds:
        exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=45, **kwargs)
        if exit_code != 0:
            continue
        for line in stdout.splitlines():
            path = line.strip()
            if path:
                return path

    tried = preferred_path or "(not set)"
    raise RuntimeError(
        "mongodump was not found on the Linux server.\n"
        f"Tried path: {tried}\n"
        "Install MongoDB Database Tools on the server, or set the correct "
        "'Mongodump binary' path in Backup (e.g. "
        "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump)."
    )


def build_mongodump_remote_command(
    *,
    mongodump_path: str,
    mongo_host: str,
    mongo_port: int,
    username: str,
    password: str,
    auth_database: str,
    output_dir: str,
    database: str | None = None,
    collection: str | None = None,
    gzip: bool = False,
) -> str:
    """Build a shell-safe mongodump command for execution on the Linux server."""
    cmd = (
        f"{shlex.quote(mongodump_path)} "
        f"--host {shlex.quote(mongo_host)} "
        f"--port {mongo_port} "
    )
    if username:
        cmd += (
            f"--username {shlex.quote(username)} "
            f"--password {shlex.quote(password)} "
            f"--authenticationDatabase {shlex.quote(auth_database)} "
        )
    if database:
        cmd += f"--db {shlex.quote(database)} "
    if collection:
        cmd += f"--collection {shlex.quote(collection)} "
    if gzip:
        cmd += "--gzip "
    cmd += f"--out {shlex.quote(output_dir)}"
    return cmd


def build_mongorestore_remote_command(
    *,
    mongorestore_path: str,
    mongo_host: str,
    mongo_port: int,
    username: str,
    password: str,
    auth_database: str,
    input_dir: str,
    database: str | None = None,
    collection: str | None = None,
    drop_existing: bool = False,
    extra_args: list[str] | None = None,
) -> str:
    """Build a shell-safe mongorestore command for execution on the Linux server."""
    cmd = (
        f"{shlex.quote(mongorestore_path)} "
        f"--host {shlex.quote(mongo_host)} "
        f"--port {mongo_port} "
    )
    if username:
        cmd += (
            f"--username {shlex.quote(username)} "
            f"--password {shlex.quote(password)} "
            f"--authenticationDatabase {shlex.quote(auth_database)} "
        )
    if drop_existing and database:
        cmd += "--drop "
    if database:
        cmd += f"--db {shlex.quote(database)} "
    if collection:
        cmd += f"--collection {shlex.quote(collection)} "
    extra_args = extra_args or []
    has_archive = any(arg.startswith("--archive=") for arg in extra_args)
    for arg in extra_args:
        cmd += f"{shlex.quote(arg)} "
    if not has_archive:
        cmd += shlex.quote(input_dir)
    return cmd


def resolve_remote_mongorestore_path(ssh: dict[str, Any], preferred_path: str | None = None) -> str:
    """Locate a working mongorestore binary on the Linux server over SSH."""
    if preferred_path and preferred_path.strip():
        preferred = preferred_path.strip()
        if "mongodump" in preferred:
            preferred = preferred.replace("mongodump", "mongorestore")
        candidates = [preferred]
    else:
        candidates = []

    default_glob = "/mnt/mongodb-database-tools-*/bin/mongorestore"
    if default_glob not in candidates:
        candidates.append(default_glob)

    kwargs = _ssh_kwargs(ssh)
    for candidate in candidates:
        if "*" in candidate:
            cmd = f"ls -1 {candidate} 2>/dev/null | head -n 1"
        else:
            cmd = f"test -x {shlex.quote(candidate)} && echo {shlex.quote(candidate)}"
        exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=30, **kwargs)
        if exit_code != 0:
            continue
        for line in stdout.splitlines():
            path = line.strip()
            if path:
                return path

    discovery_cmds = (
        "command -v mongorestore 2>/dev/null",
        "ls -1 /mnt/mongodb-database-tools-*/bin/mongorestore 2>/dev/null | head -n 1",
        "find /mnt /usr /opt -maxdepth 6 -name mongorestore -type f -perm -111 2>/dev/null | head -n 1",
    )
    for cmd in discovery_cmds:
        exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=45, **kwargs)
        if exit_code != 0:
            continue
        for line in stdout.splitlines():
            path = line.strip()
            if path:
                return path

    tried = preferred_path or "(not set)"
    raise RuntimeError(
        "mongorestore was not found on the Linux server.\n"
        f"Tried path: {tried}\n"
        "Install MongoDB Database Tools on the server, or set the correct "
        "'Mongorestore binary' path in Restore."
    )


def flatten_mongodump_output(output_dir: Path, database: str) -> None:
    """Move collection files from output_dir/database/ into output_dir/."""
    nested = output_dir / database
    if not nested.is_dir():
        return
    for item in nested.iterdir():
        target = output_dir / item.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(item), str(output_dir))
    try:
        nested.rmdir()
    except OSError:
        shutil.rmtree(nested, ignore_errors=True)


def flatten_remote_mongodump_output(ssh: dict[str, Any], remote_dir: str, database: str) -> None:
    """Hoist mongodump files from remote_dir/database/ into remote_dir/ on the server."""
    remote_dir = remote_dir.rstrip("/")
    nested = f"{remote_dir}/{database}"
    cmd = (
        f"nested={shlex.quote(nested)}; "
        f"out={shlex.quote(remote_dir)}; "
        f'if [ -d "$nested" ]; then cp -a "$nested"/. "$out"/ && rm -rf "$nested"; fi'
    )
    exit_code, _stdout, stderr = run_ssh_command(command=cmd, timeout=120, **_ssh_kwargs(ssh))
    if exit_code != 0:
        raise RuntimeError(f"Could not flatten remote mongodump output: {stderr.strip()}")


def _open_ssh_client(ssh: dict[str, Any]):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is not installed. Run: pip install paramiko") from exc

    resolved_host = resolve_ssh_host(ssh["ssh_host"], ssh["fallback_host"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=resolved_host,
            port=ssh["ssh_port"],
            username=ssh["username"],
            password=ssh["password"] or None,
            allow_agent=False,
            look_for_keys=False,
            timeout=15,
        )
    except paramiko.AuthenticationException as exc:
        raise RuntimeError(f"SSH authentication failed for Linux user '{ssh['username']}'.") from exc
    except Exception as exc:
        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    return client


def list_remote_directory(ssh: dict[str, Any], remote_path: str) -> list[dict[str, Any]]:
    """List files and folders in a remote Linux directory via SFTP."""
    path = remote_path.rstrip("/") or "/"
    client = _open_ssh_client(ssh)
    try:
        sftp = client.open_sftp()
        entries: list[dict[str, Any]] = []
        for attr in sftp.listdir_attr(path):
            name = attr.filename
            if name in (".", ".."):
                continue
            full_path = f"{path}/{name}"
            is_dir = stat.S_ISDIR(attr.st_mode)
            entries.append({
                "name": name,
                "path": full_path,
                "is_dir": is_dir,
                "size": attr.st_size,
            })
        entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
        return entries
    except FileNotFoundError as exc:
        raise RuntimeError(f"Remote path not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not list remote directory {path}: {exc}") from exc
    finally:
        client.close()


def _sftp_download_tree(sftp, remote_path: str, local_path: Path) -> None:
    local_path.mkdir(parents=True, exist_ok=True)
    for entry in sftp.listdir_attr(remote_path):
        remote_item = f"{remote_path.rstrip('/')}/{entry.filename}"
        local_item = local_path / entry.filename
        if stat.S_ISDIR(entry.st_mode):
            _sftp_download_tree(sftp, remote_item, local_item)
        else:
            sftp.get(remote_item, str(local_item))


def _sftp_upload_tree(sftp, local_path: Path, remote_path: str) -> None:
    try:
        sftp.mkdir(remote_path)
    except OSError:
        pass
    for item in local_path.iterdir():
        remote_item = f"{remote_path.rstrip('/')}/{item.name}"
        if item.is_dir():
            _sftp_upload_tree(sftp, item, remote_item)
        else:
            sftp.put(str(item), remote_item)


def upload_remote_directory(ssh: dict[str, Any], local_path: Path, remote_path: str) -> None:
    """Upload a local directory tree to a remote folder via SFTP."""
    client = _open_ssh_client(ssh)
    try:
        sftp = client.open_sftp()
        _sftp_upload_tree(sftp, local_path, remote_path)
    finally:
        client.close()


def upload_remote_file(ssh: dict[str, Any], local_path: Path, remote_path: str) -> None:
    """Upload a single local file to a remote path via SFTP."""
    client = _open_ssh_client(ssh)
    try:
        sftp = client.open_sftp()
        remote_parent = remote_path.rsplit("/", 1)[0]
        if remote_parent:
            try:
                sftp.mkdir(remote_parent)
            except OSError:
                pass
        sftp.put(str(local_path), remote_path)
    finally:
        client.close()


def download_remote_directory(ssh: dict[str, Any], remote_path: str, local_path: Path) -> None:
    """Download a remote directory tree to a local folder via SFTP."""
    client = _open_ssh_client(ssh)
    try:
        sftp = client.open_sftp()
        _sftp_download_tree(sftp, remote_path, local_path)
    finally:
        client.close()


def download_remote_file(
    ssh: dict[str, Any],
    remote_path: str,
    local_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Download a single remote file to the local PC via SFTP.

    on_progress(transferred_bytes, total_bytes) is invoked periodically.
    Uses SFTP prefetch + large reads for multi-GB dumps.
    """
    import time

    client = _open_ssh_client(ssh)
    try:
        transport = client.get_transport()
        if transport is not None:
            # Enlarge the SSH window so large SFTP transfers stay pipelined.
            try:
                transport.default_window_size = 64 * 1024 * 1024
                transport.packetizer.REKEY_BYTES = pow(2, 40)
                transport.packetizer.REKEY_PACKETS = pow(2, 40)
            except Exception:
                pass

        sftp = client.open_sftp()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            total = int(sftp.stat(remote_path).st_size)
        except OSError:
            total = 0

        transferred = 0
        last_report = 0.0
        chunk_size = 1024 * 1024  # 1 MiB

        with sftp.open(remote_path, "rb") as remote_file:
            try:
                # Prefetch dramatically speeds multi-GB downloads over Paramiko.
                if total > 0 and hasattr(remote_file, "prefetch"):
                    remote_file.prefetch(total)
            except Exception:
                pass
            with open(local_path, "wb") as local_file:
                while True:
                    data = remote_file.read(chunk_size)
                    if not data:
                        break
                    local_file.write(data)
                    transferred += len(data)
                    now = time.monotonic()
                    if on_progress and (now - last_report) >= 0.75:
                        on_progress(transferred, total or transferred)
                        last_report = now

        if on_progress:
            on_progress(transferred, total or transferred)
    finally:
        client.close()


def stream_ssh_stdout_to_file(
    ssh: dict[str, Any],
    command: str,
    local_path: Path,
    *,
    timeout: int = 7200,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, str]:
    """Run a remote command and stream its stdout to a local file.

    Faster than dump-to-/tmp then SFTP for large pg_dump archives: one transfer.
    on_progress(bytes_written, elapsed_seconds, latest_stderr_tail).
    Returns (exit_code, stderr_text).
    """
    import select
    import time

    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is not installed. Run: pip install paramiko") from exc

    kwargs = _ssh_kwargs(ssh)
    resolved_host = resolve_ssh_host(kwargs["ssh_host"], kwargs["fallback_host"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    local_path.parent.mkdir(parents=True, exist_ok=True)

    stderr_chunks: list[bytes] = []
    written = 0
    started = time.monotonic()
    deadline = started + max(1, timeout)
    last_progress_at = 0.0

    try:
        client.connect(
            hostname=resolved_host,
            port=kwargs["ssh_port"],
            username=kwargs["username"],
            password=kwargs["password"] or None,
            allow_agent=False,
            look_for_keys=False,
            timeout=15,
            banner_timeout=30,
            auth_timeout=30,
        )
        transport = client.get_transport()
        if transport is not None:
            try:
                transport.default_window_size = 64 * 1024 * 1024
                transport.packetizer.REKEY_BYTES = pow(2, 40)
                transport.packetizer.REKEY_PACKETS = pow(2, 40)
            except Exception:
                pass

        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout, bufsize=0)
        channel = stdout.channel
        # Request a large receive window for high-throughput streaming.
        try:
            channel.set_combine_stderr(False)
        except Exception:
            pass

        with open(local_path, "wb") as local_file:
            while True:
                now = time.monotonic()
                if now > deadline:
                    try:
                        channel.close()
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"SSH stream timed out after {timeout}s while receiving the backup."
                    )

                if channel.recv_ready():
                    chunk = channel.recv(1024 * 1024)
                    if chunk:
                        local_file.write(chunk)
                        written += len(chunk)

                if channel.recv_stderr_ready():
                    err_chunk = channel.recv_stderr(65536)
                    if err_chunk:
                        stderr_chunks.append(err_chunk)
                        # Cap retained stderr so -v dumps do not exhaust memory.
                        if sum(len(c) for c in stderr_chunks) > 2 * 1024 * 1024:
                            stderr_chunks[:] = stderr_chunks[-8:]

                if on_progress and (now - last_progress_at) >= 1.0:
                    tail = b"".join(stderr_chunks[-2:]).decode("utf-8", errors="replace")
                    on_progress(written, int(now - started), tail[-240:])
                    last_progress_at = now

                if channel.exit_status_ready():
                    while channel.recv_ready():
                        chunk = channel.recv(1024 * 1024)
                        if not chunk:
                            break
                        local_file.write(chunk)
                        written += len(chunk)
                    while channel.recv_stderr_ready():
                        err_chunk = channel.recv_stderr(65536)
                        if err_chunk:
                            stderr_chunks.append(err_chunk)
                    break

                if transport is not None:
                    try:
                        select.select([channel], [], [], 0.25)
                    except (OSError, ValueError, TypeError):
                        time.sleep(0.1)
                else:
                    time.sleep(0.1)

                # Avoid stalling forever with no data when remote is still working.
                if not channel.recv_ready() and not channel.recv_stderr_ready():
                    time.sleep(0.05)

        exit_code = channel.recv_exit_status()
        err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        if on_progress:
            on_progress(written, int(time.monotonic() - started), err_text[-240:])
        return exit_code, err_text
    except paramiko.AuthenticationException as exc:
        raise RuntimeError(
            f"SSH authentication failed for Linux user '{kwargs['username']}'."
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"SSH stream failed: {exc}") from exc
    finally:
        client.close()


def remote_file_size(ssh: dict[str, Any], remote_path: str) -> int:
    """Return remote file or directory size in bytes, or 0 when missing.

    Only measures the exact dump path — never a parent like /tmp.
    """
    path = (remote_path or "").strip()
    if not path or path in ("/", "/tmp", "/tmp/", "/var", "/var/tmp", "/var/tmp/"):
        return 0

    # Prefer a single-file size; for directories sum contents of that path only.
    cmd = (
        f"path={shlex.quote(path)}; "
        f"if [ -f \"$path\" ]; then stat -c%s \"$path\"; "
        f"elif [ -d \"$path\" ]; then du -sb \"$path\" | cut -f1; "
        f"else echo 0; fi"
    )
    exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=30, **_ssh_kwargs(ssh))
    if exit_code != 0:
        return 0
    try:
        # Take the last numeric token only (ignore banners / warnings).
        for line in reversed(stdout.splitlines()):
            token = line.strip().split()[0] if line.strip() else ""
            if token.isdigit():
                return int(token)
    except (ValueError, IndexError):
        return 0
    return 0


def resolve_remote_pg_dump_path(ssh: dict[str, Any], preferred_path: str | None = None) -> str:
    """Locate pg_dump on the Linux server over SSH."""
    kwargs = _ssh_kwargs(ssh)
    candidates: list[str] = []
    if preferred_path and preferred_path.strip():
        candidates.append(preferred_path.strip())

    for candidate in candidates:
        cmd = f"test -x {shlex.quote(candidate)} && echo {shlex.quote(candidate)}"
        exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=30, **kwargs)
        if exit_code == 0:
            for line in stdout.splitlines():
                if line.strip():
                    return line.strip()

    discovery_cmds = (
        "command -v pg_dump 2>/dev/null",
        "ls -1 /usr/lib/postgresql/*/bin/pg_dump 2>/dev/null | tail -n 1",
        "find /usr/bin /usr/local/bin /usr/lib/postgresql -maxdepth 4 -name pg_dump -type f 2>/dev/null | head -n 1",
    )
    for cmd in discovery_cmds:
        exit_code, stdout, _stderr = run_ssh_command(command=cmd, timeout=45, **kwargs)
        if exit_code != 0:
            continue
        for line in stdout.splitlines():
            path = line.strip()
            if path:
                return path

    raise RuntimeError(
        "pg_dump was not found on the Linux server. "
        "Install PostgreSQL client tools on the server (e.g. sudo apt install postgresql-client)."
    )


def can_use_postgres_peer_auth(ssh: dict[str, Any]) -> bool:
    """True when the SSH session can run pg_dump as the postgres OS user."""
    kwargs = _ssh_kwargs(ssh)
    username = str(kwargs.get("username") or "").strip()
    if username == "postgres":
        return True
    # root can usually switch user without a password prompt
    if username == "root":
        exit_code, _out, _err = run_ssh_command(
            command="id -u postgres >/dev/null 2>&1",
            timeout=20,
            **kwargs,
        )
        return exit_code == 0
    # passwordless sudo to postgres
    exit_code, _out, _err = run_ssh_command(
        command="sudo -n -u postgres true",
        timeout=20,
        **kwargs,
    )
    return exit_code == 0


def wrap_command_as_postgres_os_user(ssh_username: str, command: str) -> str:
    """Run a shell command as the postgres OS user on the Linux server."""
    user = (ssh_username or "").strip()
    if user == "postgres":
        return command
    if user == "root":
        # Prefer runuser/su so we do not depend on sudoers configuration.
        return (
            f"if command -v runuser >/dev/null 2>&1; then "
            f"runuser -u postgres -- bash -lc {shlex.quote(command)}; "
            f"else su -s /bin/bash postgres -c {shlex.quote(command)}; fi"
        )
    return f"sudo -n -u postgres -- bash -lc {shlex.quote(command)}"


def build_pg_dump_remote_command(
    *,
    pg_dump_path: str,
    pg_host: str,
    pg_port: int,
    username: str,
    password: str,
    output_path: str,
    database: str | None = None,
    dumpall: bool = False,
    compress_level: int = 1,
    custom_format: bool | None = None,
    format_args: list[str] | None = None,
    extra_args: list[str] | None = None,
    peer_auth: bool = False,
    ssh_username: str | None = None,
) -> str:
    """Build a shell-safe pg_dump / pg_dumpall command for the Linux server.

    peer_auth=True runs as the postgres OS user over the local socket (superuser),
    which avoids permission-denied errors from limited DB roles.

    Use output_path='-' to write the archive to stdout (streamed over SSH).
    """
    if format_args is not None:
        resolved_format = " ".join(shlex.quote(arg) for arg in format_args)
    elif custom_format is False:
        resolved_format = "--format=plain"
    else:
        resolved_format = f"--format=custom --compress={compress_level}"
    extra = " ".join(shlex.quote(arg) for arg in (extra_args or []))
    extra = f"{extra} " if extra else ""
    to_stdout = (output_path or "").strip() == "-"

    if peer_auth:
        # Local socket / peer authentication as OS user postgres (DB superuser).
        if dumpall:
            dump_cmd = f"{shlex.quote(pg_dump_path)} -f {shlex.quote(output_path)}"
        else:
            if not database:
                raise ValueError("database is required for pg_dump")
            dump_cmd = (
                f"{shlex.quote(pg_dump_path)} -d {shlex.quote(database)} "
                f"-f {shlex.quote(output_path)} {extra}{resolved_format}"
            )
        wrapped = wrap_command_as_postgres_os_user(ssh_username or "", dump_cmd)
        if to_stdout:
            return wrapped
        # Make the dump readable for SFTP download by the SSH login user.
        # Keep dump exit status (do not mask failures with `|| true` on the whole chain).
        chmod_cmd = (
            f"status=$?; chmod -R a+rX {shlex.quote(output_path)} 2>/dev/null || true; "
            f"exit $status"
        )
        return f"{wrapped}; {chmod_cmd}"

    env = (
        f"PGPASSWORD={shlex.quote(password)} "
        f"PGCONNECT_TIMEOUT=20"
    )
    base = (
        f"{env} {shlex.quote(pg_dump_path)} "
        f"-h {shlex.quote(pg_host)} -p {pg_port} -U {shlex.quote(username)} "
        f"--no-password "
    )
    if dumpall:
        return f"{base}-f {shlex.quote(output_path)}"
    if not database:
        raise ValueError("database is required for pg_dump")
    return (
        f"{base}-d {shlex.quote(database)} -f {shlex.quote(output_path)} "
        f"{extra}{resolved_format}"
    )


def remove_remote_path(ssh: dict[str, Any], remote_path: str) -> None:
    """Remove a remote file or directory."""
    cmd = f"rm -rf {shlex.quote(remote_path)}"
    exit_code, _stdout, stderr = run_ssh_command(command=cmd, timeout=120, **_ssh_kwargs(ssh))
    if exit_code != 0:
        raise RuntimeError(f"Could not remove remote path {remote_path}: {stderr.strip()}")
