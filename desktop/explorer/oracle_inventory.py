"""Oracle privileged inventory over SSH using sqlplus (containers + users)."""

from __future__ import annotations

import base64
import shlex
from typing import Any

from desktop.connection import ConnectionResult


def _ssh_params(connection: ConnectionResult) -> dict[str, Any] | None:
    if not connection.ssh_enabled or not connection.linux_username:
        return None
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


def _oracle_env_shell() -> str:
    """Resolve ORACLE_HOME / ORACLE_SID on common Docker and RPM layouts."""
    return (
        "if [ -z \"$ORACLE_HOME\" ]; then "
        "for d in "
        "/opt/oracle/product/19c/dbhome_1 "
        "/opt/oracle/product/21c/dbhome_1 "
        "/u01/app/oracle/product/19.0.0/dbhome_1 "
        "/u01/app/oracle/product/19c/dbhome_1 "
        "/home/oracle/app/oracle/product/19.0.0/dbhome_1; "
        "do [ -x \"$d/bin/sqlplus\" ] && export ORACLE_HOME=$d && break; done; "
        "fi; "
        "export ORACLE_HOME=${ORACLE_HOME:-/opt/oracle/product/19c/dbhome_1}; "
        "export PATH=$ORACLE_HOME/bin:$PATH; "
        "if [ -z \"$ORACLE_SID\" ]; then "
        "if [ -f /etc/oratab ]; then "
        "ORACLE_SID=$(awk -F: '/^[A-Za-z]/ && $1 !~ /^\\*/ {print $1; exit}' /etc/oratab); "
        "fi; "
        "export ORACLE_SID=${ORACLE_SID:-ORCLCDB}; "
        "fi; "
    )


def _inventory_sql_sysdba() -> str:
    """List containers and CDB-wide users; each container is also queried separately."""
    return """
SET TAB OFF
SET COLSEP '|'
SET LINESIZE 400
SET PAGESIZE 50000
SET FEEDBACK OFF
SET HEADING OFF
SET VERIFY OFF
SET ECHO OFF
WHENEVER SQLERROR CONTINUE
SELECT 'C|' || name || '|' || NVL(open_mode,'UNKNOWN') || '|' || con_id FROM v$containers ORDER BY con_id;
SELECT 'U|' || NVL(c.name,'CDB$ROOT') || '|' || u.username || '|' || REPLACE(NVL(u.account_status,'OPEN'),'|','/')
FROM cdb_users u
LEFT JOIN v$containers c ON c.con_id = u.con_id
ORDER BY u.con_id, u.username;
EXIT
""".strip()


def _container_users_sql(container: str) -> str:
    name = (container or "CDB$ROOT").strip().upper()
    # PDB$SEED is a read-only template; skip heavy work but still try.
    return f"""
SET TAB OFF
SET LINESIZE 400
SET PAGESIZE 50000
SET FEEDBACK OFF
SET HEADING OFF
SET VERIFY OFF
SET ECHO OFF
WHENEVER SQLERROR CONTINUE
ALTER SESSION SET CONTAINER = {name};
SELECT 'U|' || '{name}' || '|' || username || '|' || REPLACE(NVL(account_status,'OPEN'),'|','/')
FROM dba_users ORDER BY username;
EXIT
""".strip()


def _clean_line(line: str) -> str:
    text = line.strip()
    if not text:
        return ""
    low = text.lower()
    skip_prefixes = (
        "sql>",
        "sql*plus",
        "copyright",
        "connected to",
        "oracle database",
        "with the partition",
        "production",
        "session altered",
        "no rows",
        "pl/sql procedure",
    )
    if any(low.startswith(p) for p in skip_prefixes):
        return ""
    if low.startswith("ora-") or low.startswith("sp2-"):
        return ""
    return text


def _parse_inventory(output: str) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    containers: list[dict[str, Any]] = []
    users_by: dict[str, list[dict[str, str]]] = {}
    seen_containers: set[str] = set()
    seen_users: dict[str, set[str]] = {}

    for raw in output.splitlines():
        line = _clean_line(raw)
        if not line:
            continue
        # Some sqlplus layouts pad columns; normalize pipes.
        if "C|" in line and not line.startswith("C|"):
            line = line[line.index("C|") :]
        if "U|" in line and not line.startswith("U|"):
            line = line[line.index("U|") :]

        if line.startswith("C|"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            name = parts[1].upper()
            if not name or name in seen_containers:
                continue
            seen_containers.add(name)
            open_mode = parts[2] if len(parts) > 2 else "UNKNOWN"
            con_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            containers.append({"name": name, "open_mode": open_mode, "con_id": con_id})
        elif line.startswith("U|"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            container = (parts[1] or "CDB$ROOT").upper()
            username = parts[2]
            status = parts[3] or "OPEN"
            if not username:
                continue
            seen = seen_users.setdefault(container, set())
            if username.upper() in seen:
                continue
            seen.add(username.upper())
            users_by.setdefault(container, []).append(
                {"name": username, "status": status}
            )

    return containers, users_by


def _run_sqlplus(connection: ConnectionResult, script: str, *, sysdba: bool = True) -> str | None:
    params = _ssh_params(connection)
    if not params:
        return None

    from backend.services.ssh_client import run_ssh_command

    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    env = _oracle_env_shell()
    connect = "/ as sysdba" if sysdba else "/"

    candidates = [
        (
            f"{env}"
            f"TMPF=$(mktemp /tmp/dbap_ora_XXXXXX.sql); "
            f"echo {shlex.quote(encoded)} | base64 -d > \"$TMPF\"; "
            f"sqlplus -S {connect} @\"$TMPF\"; "
            f"RC=$?; rm -f \"$TMPF\"; exit $RC"
        ),
        (
            f"{env}"
            f"echo {shlex.quote(encoded)} | base64 -d | sqlplus -S {connect}"
        ),
        # Some images only have sqlplus under a non-default PATH.
        (
            f"{env}"
            f"SQLPLUS=$(command -v sqlplus || ls $ORACLE_HOME/bin/sqlplus 2>/dev/null | head -n1); "
            f"TMPF=$(mktemp /tmp/dbap_ora_XXXXXX.sql); "
            f"echo {shlex.quote(encoded)} | base64 -d > \"$TMPF\"; "
            f"\"$SQLPLUS\" -S {connect} @\"$TMPF\"; "
            f"RC=$?; rm -f \"$TMPF\"; exit $RC"
        ),
    ]

    best: str | None = None
    for command in candidates:
        try:
            code, out, err = run_ssh_command(
                ssh_host=params["ssh_host"],
                fallback_host=params["fallback_host"],
                ssh_port=params["ssh_port"],
                username=params["username"],
                password=params["password"],
                command=command,
                timeout=120,
            )
        except Exception:
            continue
        text = f"{out or ''}\n{err or ''}".strip()
        if "C|" in text or "U|" in text:
            return text
        if code == 0 and text and best is None:
            best = text
    return best


def fetch_oracle_inventory_via_ssh(
    connection: ConnectionResult,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    """Return (containers, users_by_container) using sqlplus / as sysdba over SSH."""
    output = _run_sqlplus(connection, _inventory_sql_sysdba(), sysdba=True)
    if not output:
        return [], {}
    containers, users_by = _parse_inventory(output)

    # Ensure each known container has its own dba_users pull (accurate counts).
    names = [c["name"] for c in containers] or ["CDB$ROOT", "PDB$SEED", "ORCLPDB1"]
    for name in names:
        key = str(name).upper()
        current = users_by.get(key) or []
        # Always refresh CDB$ROOT so root count is not left empty after a PDB-only login.
        if key == "CDB$ROOT" or not current:
            rows = fetch_container_users_via_ssh(connection, key)
            if rows and len(rows) >= len(current):
                users_by[key] = rows
        elif key == "PDB$SEED" and key not in users_by:
            users_by[key] = []

    return containers, users_by


def fetch_container_users_via_ssh(
    connection: ConnectionResult,
    container: str,
) -> list[dict[str, str]]:
    """List users inside one container via ALTER SESSION + dba_users."""
    output = _run_sqlplus(connection, _container_users_sql(container), sysdba=True)
    if not output:
        return []
    _containers, users_by = _parse_inventory(output)
    key = (container or "").strip().upper()
    return list(users_by.get(key, []))


def fetch_pdb_users_via_ssh(
    connection: ConnectionResult,
    pdb_name: str,
) -> list[dict[str, str]]:
    """Fallback: list users inside one PDB via sqlplus ALTER SESSION."""
    return fetch_container_users_via_ssh(connection, pdb_name or "ORCLPDB1")
