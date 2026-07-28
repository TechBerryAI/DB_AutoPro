"""List all MySQL databases on a remote Linux host (SSH), beyond login-user grants."""

from __future__ import annotations

import shlex
from typing import Any

from desktop.connection import ConnectionResult


def _ssh_params(connection: ConnectionResult) -> dict[str, Any] | None:
    if not connection.ssh_enabled or not connection.linux_username:
        return None
    # Prefer real Linux host; tunneled sessions bind to 127.0.0.1 locally.
    display = (connection.display_host or "").strip()
    ssh_host = (connection.ssh_host or "").strip()
    host = (connection.host or "").strip()
    if host in ("127.0.0.1", "localhost"):
        fallback = display or ssh_host or host
    else:
        fallback = host
    resolved_ssh = ssh_host or display or fallback
    if not resolved_ssh:
        return None
    return {
        "ssh_host": resolved_ssh,
        "fallback_host": fallback or resolved_ssh,
        "ssh_port": int(connection.ssh_port or 22),
        "username": connection.linux_username,
        "password": connection.linux_password or "",
    }


def _mysql_cli_candidates(linux_password: str) -> list[str]:
    """Remote mysql client invocations that typically see the full server catalog."""
    show = shlex.quote("SHOW DATABASES")
    candidates = [
        f"mysql -Nse {show}",
        f"sudo -n mysql -Nse {show}",
        f"mysql -uroot -Nse {show}",
    ]
    if linux_password:
        # Non-interactive sudo with the Linux account password.
        pw = shlex.quote(linux_password)
        candidates.append(
            f"printf '%s\\n' {pw} | sudo -S -p '' mysql -Nse {show}"
        )
    return candidates


def _parse_db_names(stdout: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower in {"database", "schema"}:
            continue
        if lower.startswith("mysql:") or "warning" in lower or "password" in lower:
            continue
        # One name per line from `mysql -Nse 'SHOW DATABASES'`.
        if "\t" in line:
            line = line.split("\t", 1)[0].strip()
        if not line or " " in line:
            continue
        if line not in seen:
            seen.add(line)
            names.append(line)
    return names


def list_database_names_via_ssh(connection: ConnectionResult) -> list[str]:
    """Return all database names visible to a privileged local mysql on the SSH host."""
    params = _ssh_params(connection)
    if not params:
        return []

    from backend.services.ssh_client import run_ssh_command

    best: list[str] = []
    for command in _mysql_cli_candidates(params["password"]):
        try:
            code, out, _err = run_ssh_command(
                ssh_host=params["ssh_host"],
                fallback_host=params["fallback_host"],
                ssh_port=params["ssh_port"],
                username=params["username"],
                password=params["password"],
                command=command,
                timeout=45,
            )
        except Exception:
            continue
        if code != 0 or not (out or "").strip():
            continue
        names = _parse_db_names(out)
        if len(names) > len(best):
            best = names
    return best


def fetch_schema_sizes_via_ssh(connection: ConnectionResult) -> dict[str, int]:
    """Map schema -> size bytes using privileged mysql over SSH (best effort)."""
    params = _ssh_params(connection)
    if not params:
        return {}

    from backend.services.ssh_client import run_ssh_command

    sql = (
        "SELECT TABLE_SCHEMA, COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) "
        "FROM information_schema.TABLES GROUP BY TABLE_SCHEMA"
    )
    quoted = shlex.quote(sql)
    linux_password = params["password"]
    commands = [
        f"mysql -Nse {quoted}",
        f"sudo -n mysql -Nse {quoted}",
        f"mysql -uroot -Nse {quoted}",
    ]
    if linux_password:
        pw = shlex.quote(linux_password)
        commands.append(
            f"printf '%s\\n' {pw} | sudo -S -p '' mysql -Nse {quoted}"
        )

    for command in commands:
        try:
            code, out, _err = run_ssh_command(
                ssh_host=params["ssh_host"],
                fallback_host=params["fallback_host"],
                ssh_port=params["ssh_port"],
                username=params["username"],
                password=params["password"],
                command=command,
                timeout=60,
            )
        except Exception:
            continue
        if code != 0 or not (out or "").strip():
            continue
        sizes: dict[str, int] = {}
        for raw in out.splitlines():
            line = raw.strip()
            if not line or "\t" not in line:
                continue
            name, size_text = line.split("\t", 1)
            name = name.strip()
            if not name:
                continue
            try:
                sizes[name] = int(float(size_text.strip()))
            except (TypeError, ValueError):
                sizes[name] = 0
        if sizes:
            return sizes
    return {}
