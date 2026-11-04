"""Direct DB connect auto-falls back to SSH when host rules block the client."""

from __future__ import annotations

from backend.models.schemas import DatabaseType
from desktop.connection import (
    ConnectionResult,
    _direct_access_likely_blocked,
    _enrich_ssh_from_saved,
    _has_linux_ssh_credentials,
)
from desktop.connection import test_connection as run_connection_test


def _pg(**kwargs) -> ConnectionResult:
    base = dict(
        name="Ganesh_Postgres",
        db_type=DatabaseType.POSTGRESQL,
        host="192.168.1.159",
        port=5432,
        username="postgres",
        password="secret",
        database="postgres",
        ssh_enabled=False,
        linux_username="root",
        linux_password="linux-secret",
        ssh_host="192.168.1.159",
        ssh_port=22,
    )
    base.update(kwargs)
    return ConnectionResult(**base)


def test_pg_hba_is_treated_as_direct_block():
    msg = (
        'FATAL: no pg_hba.conf entry for host "192.168.1.59", user "postgres", '
        'database "postgres", no encryption'
    )
    assert _direct_access_likely_blocked(msg) is True


def test_oracle_listener_errors_are_treated_as_direct_block():
    assert _direct_access_likely_blocked("ORA-12541: TNS:no listener") is True
    assert _direct_access_likely_blocked("DPY-4011: the database or network closed") is True
    assert _direct_access_likely_blocked(
        "Oracle port closed on 192.168.1.161:1521. Start the listener (lsnrctl start)"
    ) is True


def test_wrong_password_is_not_an_ssh_bypass_signal():
    assert _direct_access_likely_blocked("password authentication failed for user") is False


def test_enrich_ssh_from_saved_fills_missing_linux_login(monkeypatch):
    class _Saved:
        name = "Ganesh_Postgres"
        linux_username = "root"
        linux_password = "from-disk"
        ssh_host = "192.168.1.159"
        ssh_port = 22

    monkeypatch.setattr(
        "desktop.connection_store.load_saved_servers",
        lambda: [_Saved()],
    )
    bare = _pg(linux_username="", linux_password="")
    enriched = _enrich_ssh_from_saved(bare)
    assert enriched.linux_username == "root"
    assert enriched.linux_password == "from-disk"
    assert _has_linux_ssh_credentials(enriched) is True


def test_direct_pg_hba_failure_retries_through_ssh(monkeypatch):
    from desktop import connection as connection_mod

    class _Tunnel:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("desktop.ssh_tunnel.ssh_tunnel_session", lambda conn: _Tunnel(conn))
    monkeypatch.setattr(connection_mod, "apply_connection", lambda *_a, **_k: None)

    class _Healthy:
        def health_check(self):
            return {"status": "healthy", "version": "PostgreSQL 17.10", "latency_ms": 50}

    class _Unhealthy:
        def health_check(self):
            return {
                "status": "unhealthy",
                "error": (
                    'FATAL: no pg_hba.conf entry for host "192.168.1.59", '
                    'user "postgres", database "postgres", no encryption'
                ),
                "latency_ms": 10,
            }

    state = {"n": 0}

    def factory(_db_type, _info):
        state["n"] += 1
        return _Unhealthy() if state["n"] == 1 else _Healthy()

    monkeypatch.setattr(connection_mod, "get_connector_from_info", factory)

    conn = _pg(ssh_enabled=False)
    ok, message = run_connection_test(conn)
    assert ok is True
    assert conn.ssh_enabled is True
    assert state["n"] >= 2
    assert "automatically" in message.lower() or "via SSH" in message


def test_ssh_fallback_skipped_when_no_linux_credentials(monkeypatch):
    from desktop import connection as connection_mod

    class _Tunnel:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("desktop.ssh_tunnel.ssh_tunnel_session", lambda conn: _Tunnel(conn))
    monkeypatch.setattr(connection_mod, "apply_connection", lambda *_a, **_k: None)

    class _Unhealthy:
        def health_check(self):
            return {
                "status": "unhealthy",
                "error": 'FATAL: no pg_hba.conf entry for host "192.168.1.59"',
                "latency_ms": 5,
            }

    monkeypatch.setattr(
        connection_mod,
        "get_connector_from_info",
        lambda *_a, **_k: _Unhealthy(),
    )
    monkeypatch.setattr(connection_mod, "_enrich_ssh_from_saved", lambda c: c)

    conn = _pg(ssh_enabled=False, linux_username="", linux_password="")
    ok, message = run_connection_test(conn)
    assert ok is False
    assert conn.ssh_enabled is False
    assert "pg_hba" in message.lower()
