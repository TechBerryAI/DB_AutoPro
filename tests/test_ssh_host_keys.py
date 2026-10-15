"""Host-key checks with fake SSH clients. No sockets and no real servers."""

from __future__ import annotations

import paramiko
import pytest

from backend.services.mysql_patch.command_runner import CMD_OS_RELEASE, SshCommandRunner
from backend.services.ssh_host_keys import (
    SshHostKeyError,
    configure_ssh_client,
    connect_verified_client,
    decide_host_key,
    fingerprint_sha256,
    host_key_error_from_bad_key,
    load_known_hosts,
    record_verified_host_key,
)


class FakeSSHClient:
    """Stands in for Paramiko's SSHClient. It never opens a connection."""

    def __init__(self) -> None:
        self.policy = None
        self.host_keys = paramiko.HostKeys()
        self.loaded_path: str | None = None

    def load_host_keys(self, filename: str) -> None:
        self.loaded_path = filename
        self.host_keys.load(filename)

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy


def _key():
    return paramiko.RSAKey.generate(1024)


def test_unknown_host_is_rejected_and_not_stored(tmp_path):
    path = tmp_path / "ssh_known_hosts"
    client = FakeSSHClient()
    configure_ssh_client(client, "10.0.0.9", 22, path)
    presented = _key()

    with pytest.raises(SshHostKeyError) as caught:
        client.policy.missing_host_key(client, "10.0.0.9", presented)

    assert caught.value.reason == "unknown"
    assert caught.value.fingerprint == fingerprint_sha256(presented)
    assert client.host_keys.lookup("10.0.0.9") is None
    assert not path.exists()
    assert type(client.policy).__name__ != "AutoAddPolicy"


def test_first_connection_stores_the_host_key_and_continues(tmp_path):
    path = tmp_path / "ssh_known_hosts"
    presented = _key()

    class ConnectingClient(FakeSSHClient):
        def __init__(self) -> None:
            super().__init__()
            self.connects = 0

        def close(self) -> None:
            return None

        def connect(self, **kwargs) -> None:
            self.connects += 1
            hostname = kwargs["hostname"]
            if self.host_keys.lookup(hostname) is None:
                self.policy.missing_host_key(self, hostname, presented)

    client = ConnectingClient()
    connect_verified_client(
        client,
        hostname="10.0.0.9",
        port=22,
        username="user",
        password="secret-value",
        known_hosts_path=path,
    )

    assert client.connects == 2
    decide_host_key(load_known_hosts(path), "10.0.0.9", 22, presented)
    assert "secret-value" not in path.read_text(encoding="utf-8")


def test_matching_key_is_accepted_for_a_known_host(tmp_path):
    path = tmp_path / "ssh_known_hosts"
    trusted = _key()
    record_verified_host_key(
        host="10.0.0.8",
        port=22,
        key=trusted,
        verified_fingerprint=fingerprint_sha256(trusted),
        path=path,
    )
    client = FakeSSHClient()
    configure_ssh_client(client, "10.0.0.8", 22, path)

    decide_host_key(client.host_keys, "10.0.0.8", 22, trusted)


def test_mismatched_key_is_rejected_and_does_not_replace_the_stored_key(tmp_path):
    path = tmp_path / "ssh_known_hosts"
    trusted = _key()
    presented = _key()
    record_verified_host_key(
        host="10.0.0.8",
        port=22,
        key=trusted,
        verified_fingerprint=fingerprint_sha256(trusted),
        path=path,
    )
    before = path.read_text(encoding="utf-8")
    client = FakeSSHClient()
    configure_ssh_client(client, "10.0.0.8", 22, path)

    with pytest.raises(SshHostKeyError) as caught:
        decide_host_key(client.host_keys, "10.0.0.8", 22, presented)

    assert caught.value.reason == "mismatch"
    assert path.read_text(encoding="utf-8") == before
    decide_host_key(load_known_hosts(path), "10.0.0.8", 22, trusted)


def test_malformed_known_host_entry_refuses_the_connection(tmp_path):
    path = tmp_path / "ssh_known_hosts"
    path.write_text("this line is not a host key\n", encoding="utf-8")
    client = FakeSSHClient()

    with pytest.raises(SshHostKeyError) as caught:
        configure_ssh_client(client, "10.0.0.8", 22, path)

    assert caught.value.reason == "malformed"
    assert client.policy is None


def test_presented_fingerprint_is_not_stored_unless_the_operator_verifies_it(tmp_path):
    path = tmp_path / "ssh_known_hosts"
    presented = _key()

    with pytest.raises(SshHostKeyError) as caught:
        record_verified_host_key(
            host="10.0.0.8",
            port=22,
            key=presented,
            verified_fingerprint="SHA256:not-the-server-fingerprint",
            path=path,
        )

    assert caught.value.reason == "unverified"
    assert not path.exists()


def test_paramiko_mismatch_is_reported_without_replacing_the_key():
    trusted = _key()
    presented = _key()
    exc = paramiko.BadHostKeyException("10.0.0.8", presented, trusted)

    error = host_key_error_from_bad_key(exc, host="10.0.0.8", port=22)

    assert error.reason == "mismatch"
    assert error.fingerprint == fingerprint_sha256(presented)
    assert "BEGIN" not in str(error)


def test_ssh_runner_marks_a_pinned_connection_and_keeps_the_password_out(monkeypatch):
    monkeypatch.setattr(
        "backend.services.ssh_client.run_ssh_command",
        lambda **_kwargs: (0, "ok", ""),
    )
    runner = SshCommandRunner(host="10.0.0.8", port=22, username="user", password="secret-value")

    assert runner.host_key_verified is False
    runner.run(CMD_OS_RELEASE)

    assert runner.host_key_verified is True
    assert "secret-value" not in repr(runner)


def test_ssh_runner_stays_unverified_when_the_host_key_is_rejected(monkeypatch):
    def reject(**_kwargs):
        raise SshHostKeyError(
            host="10.0.0.8",
            port=22,
            reason="unknown",
            key_type="ssh-ed25519",
            fingerprint="SHA256:abc",
        )

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", reject)
    runner = SshCommandRunner(host="10.0.0.8", port=22, username="user", password="secret-value")

    with pytest.raises(SshHostKeyError):
        runner.run(CMD_OS_RELEASE)

    assert runner.host_key_verified is False
