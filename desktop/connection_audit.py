"""Audit logging for desktop database connection attempts."""

from __future__ import annotations

from desktop.connection import ConnectionResult


def log_connection_attempt(
    user: str | None,
    connection: ConnectionResult,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    from backend.core.logging import get_audit_logger

    audit = get_audit_logger()
    resource = connection.saved_name or connection.name or f"{connection.host}:{connection.port}"
    details: dict = {
        "db_type": connection.db_type.value,
        "host": connection.endpoint_host,
        "port": connection.endpoint_port,
        "ssh_enabled": connection.ssh_enabled,
    }
    if error:
        details["error"] = error[:500]
    audit.log(
        "connect",
        user or "unknown",
        resource,
        "success" if success else "failed",
        details,
    )
