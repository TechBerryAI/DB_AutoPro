from types import SimpleNamespace

import pytest

from backend.services import ssh_client


def _ssh(host="10.0.0.5"):
    return {"ssh_host": host, "fallback_host": host, "ssh_port": 22, "username": "u", "password": "p"}


def test_backup_uses_loopback_when_ssh_server_is_the_mongo_server(monkeypatch):
    seen = []

    def fake(command, **_kwargs):
        seen.append(command)
        return 0, "", ""

    monkeypatch.setattr(ssh_client, "run_ssh_command", fake)
    conn = SimpleNamespace(extra_options={}, host="10.0.0.5", port=27018)
    assert ssh_client.resolve_remote_mongo_endpoint(conn, _ssh(), prefer_loopback=True) == ("127.0.0.1", 27018)
    assert "127.0.0.1/27018" in seen[0]
    assert ssh_client.resolve_remote_mongo_endpoint(conn, _ssh()) == ("10.0.0.5", 27018)


def test_loopback_failure_falls_back_to_the_lan_address_and_unreachable_is_explained(monkeypatch):
    def only_lan(command, **_kwargs):
        return (0 if "10.0.0.5/27018" in command else 1), "", ""

    monkeypatch.setattr(ssh_client, "run_ssh_command", only_lan)
    conn = SimpleNamespace(extra_options={}, host="10.0.0.5", port=27018)
    assert ssh_client.resolve_remote_mongo_endpoint(conn, _ssh(), prefer_loopback=True) == ("10.0.0.5", 27018)

    monkeypatch.setattr(ssh_client, "run_ssh_command", lambda command, **_kwargs: (1, "", ""))
    with pytest.raises(RuntimeError) as error:
        ssh_client.resolve_remote_mongo_endpoint(conn, _ssh("10.0.1.9"), prefer_loopback=True)
    assert "cannot reach MongoDB at 10.0.0.5:27018" in str(error.value)
    assert "10.0.1.9" in str(error.value)
