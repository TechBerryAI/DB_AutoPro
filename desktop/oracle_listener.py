"""Best-effort Oracle TNS listener start over SSH when port 1521 is down."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from desktop.connection import ConnectionResult


def listener_appears_down(message: str) -> bool:
    low = (message or "").lower()
    return any(
        token in low
        for token in (
            "oracle port closed",
            "no listener",
            "ora-12541",
            "start the listener",
            "lsnrctl",
            "connection refused",
            "10061",
        )
    )


def try_start_oracle_listener(connection: ConnectionResult) -> str | None:
    """Start the TNS listener on the Linux host using saved SSH credentials.

    Returns a short status string on success, or None when skipped/failed.
    """
    user = (connection.linux_username or "").strip()
    password = connection.linux_password or ""
    if not user or not password:
        return None

    from backend.services.ssh_client import run_ssh_command
    from desktop.explorer.oracle_inventory import _oracle_env_shell

    host = (connection.ssh_host or connection.host or "").strip()
    port = int(connection.ssh_port or 22)
    sid_hint = (connection.database or "").strip() or "BANKDB"
    env = _oracle_env_shell()
    # Prefer the configured service/SID when oratab discovery is wrong.
    cmd = (
        f"{env} "
        f"export ORACLE_SID=${{ORACLE_SID:-{sid_hint}}}; "
        "lsnrctl status >/dev/null 2>&1 || lsnrctl start; "
        "echo \"ALTER SYSTEM REGISTER;\" | sqlplus -s / as sysdba >/dev/null 2>&1 || true; "
        "sleep 1; "
        "ss -ltn 2>/dev/null | grep -E ':1521\\s' || "
        "netstat -ltn 2>/dev/null | grep 1521 || "
        "lsnrctl status | head -40"
    )
    try:
        code, stdout, stderr = run_ssh_command(
            ssh_host=host,
            fallback_host=connection.host,
            ssh_port=port,
            username=user,
            password=password,
            command=cmd,
            timeout=90,
        )
    except Exception:
        return None

    text = f"{stdout or ''}\n{stderr or ''}"
    if code == 0 or ":1521" in text or "STATUS of the LISTENER" in text or "READY" in text:
        return "Oracle listener started on the Linux host"
    if text.strip():
        return "Oracle listener start attempted on the Linux host"
    return None
