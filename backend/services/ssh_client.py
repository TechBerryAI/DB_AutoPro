"""SSH command execution for remote database operations."""

from __future__ import annotations

import shlex
import shutil
import socket
import stat
from pathlib import Path
from typing import Any


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
) -> tuple[int, str, str]:
    """Run a command on a remote Linux host over SSH."""
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
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode("utf-8", errors="replace"), stderr.read().decode(
            "utf-8", errors="replace"
        )
    except paramiko.AuthenticationException as exc:
        raise RuntimeError(f"SSH authentication failed for Linux user '{username}'.") from exc
    except Exception as exc:
        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    finally:
        client.close()


def ssh_options_from_connection(conn: Any) -> dict[str, Any]:
    """Extract SSH settings stored on a resolved connection profile."""
    opts = conn.extra_options or {}
    if not opts.get("ssh_enabled"):
        return {}
    return {
        "ssh_host": opts.get("ssh_host") or conn.host,
        "fallback_host": conn.host,
        "ssh_port": int(opts.get("ssh_port", 22)),
        "username": opts.get("linux_username", ""),
        "password": opts.get("linux_password", ""),
        "mongodump_path": opts.get(
            "mongodump_path",
            "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump",
        ),
        "remote_backup_dir": opts.get("remote_backup_dir", "/mnt/backup"),
    }


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


def remove_remote_path(ssh: dict[str, Any], remote_path: str) -> None:
    """Remove a remote file or directory."""
    cmd = f"rm -rf {shlex.quote(remote_path)}"
    exit_code, _stdout, stderr = run_ssh_command(command=cmd, timeout=120, **_ssh_kwargs(ssh))
    if exit_code != 0:
        raise RuntimeError(f"Could not remove remote path {remote_path}: {stderr.strip()}")
