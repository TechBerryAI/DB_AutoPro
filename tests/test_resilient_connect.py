"""Resilient Connect orchestrator — direct, SSH, and Oracle listener recovery."""

from __future__ import annotations

from backend.models.schemas import DatabaseType
from desktop.connection import (
    ConnectionResult,
    _direct_access_likely_blocked,
    _is_credential_error,
)
from desktop.connection import test_connection as run_connection_test


def _conn(**kwargs) -> ConnectionResult:
    base = dict(
        name="demo",
        db_type=DatabaseType.POSTGRESQL,
        host="192.168.1.10",
        port=5432,
        username="postgres",
        password="secret",
        database="postgres",
        ssh_enabled=False,
        linux_username="root",
        linux_password="linux",
        ssh_host="192.168.1.10",
        ssh_port=22,
    )
    base.update(kwargs)
    return ConnectionResult(**base)


def test_credential_errors_do_not_look_like_network_blocks():
    assert _is_credential_error("password authentication failed for user postgres")
    assert _direct_access_likely_blocked("password authentication failed for user") is False


def test_oracle_port_closed_is_network_block():
    assert _direct_access_likely_blocked(
        "Oracle port closed on 192.168.1.161:1521. Start the listener"
    )


def test_connect_tries_ssh_after_direct_network_failure(monkeypatch):
    from desktop import connection as mod

    class _Tunnel:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("desktop.ssh_tunnel.ssh_tunnel_session", lambda c: _Tunnel(c))
    monkeypatch.setattr(mod, "apply_connection", lambda *_a, **_k: None)

    class _Healthy:
        def health_check(self):
            return {"status": "healthy", "version": "PostgreSQL 17", "latency_ms": 10}

    class _Unhealthy:
        def health_check(self):
            return {
                "status": "unhealthy",
                "error": 'FATAL: no pg_hba.conf entry for host "192.168.1.59"',
                "latency_ms": 5,
            }

    state = {"n": 0}

    def factory(_t, _i):
        state["n"] += 1
        return _Unhealthy() if state["n"] == 1 else _Healthy()

    monkeypatch.setattr(mod, "get_connector_from_info", factory)

    conn = _conn(ssh_enabled=False)
    ok, msg = run_connection_test(conn)
    assert ok is True
    assert conn.ssh_enabled is True
    assert "Connected successfully" in msg
    assert state["n"] >= 2


def test_connect_does_not_ssh_fallback_on_bad_password(monkeypatch):
    from desktop import connection as mod

    class _Tunnel:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("desktop.ssh_tunnel.ssh_tunnel_session", lambda c: _Tunnel(c))
    monkeypatch.setattr(mod, "apply_connection", lambda *_a, **_k: None)

    class _Unhealthy:
        def health_check(self):
            return {
                "status": "unhealthy",
                "error": "password authentication failed for user \"postgres\"",
                "latency_ms": 5,
            }

    calls = {"n": 0}

    def factory(_t, _i):
        calls["n"] += 1
        return _Unhealthy()

    monkeypatch.setattr(mod, "get_connector_from_info", factory)

    conn = _conn(ssh_enabled=False)
    ok, msg = run_connection_test(conn)
    assert ok is False
    assert conn.ssh_enabled is False
    assert calls["n"] == 1
    assert "password authentication failed" in msg.lower()


def test_oracle_listener_recovery_then_connect(monkeypatch):
    from desktop import connection as mod

    class _Tunnel:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("desktop.ssh_tunnel.ssh_tunnel_session", lambda c: _Tunnel(c))
    monkeypatch.setattr(mod, "apply_connection", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "desktop.oracle_listener.try_start_oracle_listener",
        lambda _c: "started",
    )

    class _Healthy:
        def health_check(self):
            return {"status": "healthy", "version": "Oracle 19c", "latency_ms": 20}

    class _Down:
        def health_check(self):
            return {
                "status": "unhealthy",
                "error": "Oracle port closed on 192.168.1.161:1521",
                "latency_ms": 5,
            }

    state = {"n": 0}

    def factory(_t, _i):
        state["n"] += 1
        # First attempt(s) fail until listener start; then healthy.
        return _Down() if state["n"] <= 1 else _Healthy()

    monkeypatch.setattr(mod, "get_connector_from_info", factory)

    conn = _conn(
        name="Banking_DB",
        db_type=DatabaseType.ORACLE,
        host="192.168.1.161",
        port=1521,
        username="BANKING",
        database="BANKDB",
        ssh_enabled=False,
        linux_username="oracle",
        linux_password="123",
        ssh_host="192.168.1.161",
    )
    ok, msg = run_connection_test(conn)
    assert ok is True
    assert "Connected successfully" in msg
    assert state["n"] >= 2
