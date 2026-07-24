"""Tests for SSH tunnel connection resolution used by dashboard metrics."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult
from desktop import ssh_tunnel


def _ssh_connection() -> ConnectionResult:
    return ConnectionResult(
        name="postgres",
        db_type=DatabaseType.POSTGRESQL,
        host="192.168.1.57",
        port=5432,
        username="postgres",
        password="secret",
        database="postgres",
        ssh_enabled=True,
        linux_username="root",
        linux_password="secret",
    )


def test_resolve_active_connection_uses_local_tunnel_port(monkeypatch):
    conn = _ssh_connection()
    forwarder = MagicMock()
    forwarder.local_bind_port = 54321
    monkeypatch.setattr(ssh_tunnel, "_active_forwarder", forwarder)

    effective = ssh_tunnel.resolve_active_connection(conn)

    assert effective.host == "127.0.0.1"
    assert effective.port == 54321
    assert effective.display_host == "192.168.1.57"
    assert effective.display_port == 5432


def test_resolve_active_connection_without_tunnel_returns_original(monkeypatch):
    conn = _ssh_connection()
    monkeypatch.setattr(ssh_tunnel, "_active_forwarder", None)

    effective = ssh_tunnel.resolve_active_connection(conn)

    assert effective is conn


def test_tunneled_connection_preserves_display_endpoint():
    conn = _ssh_connection()
    effective = ssh_tunnel._tunneled_connection(conn, 55000)

    assert effective.endpoint_label == "192.168.1.57:5432"
