"""Credential protection tests. They use temporary files and fake passwords only."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from desktop.connection_store import (
    PROTECTION_ID,
    STORE_PATH,
    SavedServer,
    load_saved_servers,
    load_servers_from_path,
    migrate_servers_file,
    save_servers,
)
from desktop.windows_dpapi import CredentialProtectionError, protect_bytes, unprotect_bytes

_LIVE_STORE = STORE_PATH.resolve()


def _reject_live_store(path) -> None:
    try:
        candidate = Path(path)
        if candidate.resolve() == _LIVE_STORE:
            raise AssertionError("A test accessed the live saved_servers.json file")
    except OSError:
        return


@pytest.fixture(autouse=True)
def forbid_live_saved_servers(monkeypatch):
    """Fail the test if it reads or writes the real credential file."""
    real_open = open

    def guarded_open(file, *args, **kwargs):
        _reject_live_store(file)
        return real_open(file, *args, **kwargs)

    def guarded(name):
        original = getattr(Path, name)

        def wrapped(self, *args, **kwargs):
            _reject_live_store(self)
            return original(self, *args, **kwargs)

        return wrapped

    monkeypatch.setattr("builtins.open", guarded_open)
    for method in ("read_text", "read_bytes", "write_text", "write_bytes"):
        monkeypatch.setattr(Path, method, guarded(method))


def _server(
    password: str = "fake-db-password",
    linux_password: str = "fake-ssh-password",
    *,
    ssh_enabled: bool = False,
) -> SavedServer:
    return SavedServer(
        name="temp-server",
        db_type="mysql",
        host="10.0.0.5",
        port=3306,
        username="dbuser",
        password=password,
        database="employees",
        ssh_enabled=ssh_enabled,
        linux_username="linuxuser",
        linux_password=linux_password,
        ssh_host="10.0.0.5",
        ssh_port=22,
    )


def _write_legacy(path, server: SavedServer) -> None:
    payload = {
        "servers": [
            {
                "name": server.name,
                "db_type": server.db_type,
                "host": server.host,
                "port": server.port,
                "username": server.username,
                "password": server.password,
                "database": server.database,
                "linux_username": server.linux_username,
                "linux_password": server.linux_password,
                "ssh_enabled": server.ssh_enabled,
                "ssh_host": server.ssh_host,
                "ssh_port": server.ssh_port,
            }
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_dpapi_round_trip_hides_the_plaintext():
    blob = protect_bytes(b"fake-db-password")

    assert blob != b"fake-db-password"
    assert unprotect_bytes(blob) == b"fake-db-password"
    assert protect_bytes(b"fake-db-password") != blob


def test_corrupted_protected_value_is_rejected():
    with pytest.raises(CredentialProtectionError) as invalid:
        SavedServer.from_dict({"name": "temp-server", "password_protected": "not-base64"})
    assert "fake-db-password" not in str(invalid.value)

    garbage = base64.b64encode(b"this is not a dpapi blob").decode("ascii")
    with pytest.raises(CredentialProtectionError) as corrupt:
        SavedServer.from_dict({"name": "temp-server", "password_protected": garbage})
    assert "fake-db-password" not in str(corrupt.value)


def test_missing_credentials_load_as_empty_strings():
    server = SavedServer.from_dict({"name": "temp-server"})

    assert server.password == ""
    assert server.linux_password == ""


def test_legacy_plaintext_profile_still_loads(tmp_path):
    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server())

    loaded = load_servers_from_path(path)

    assert loaded[0].password == "fake-db-password"
    assert loaded[0].linux_password == "fake-ssh-password"
    assert "credential_protection" not in path.read_text(encoding="utf-8")


def test_unprotected_save_does_not_migrate(tmp_path):
    path = tmp_path / "saved_servers.json"
    save_servers([_server()], path=path)
    text = path.read_text(encoding="utf-8")

    assert "fake-db-password" in text
    assert "credential_protection" not in text


def test_migration_encrypts_a_temporary_file_and_keeps_a_backup(tmp_path):
    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server())

    backup = migrate_servers_file(path)
    text = path.read_text(encoding="utf-8")
    loaded = load_servers_from_path(path)

    assert backup.read_bytes() != path.read_bytes()
    assert "fake-db-password" in backup.read_text(encoding="utf-8")
    assert "fake-db-password" not in text
    assert "fake-ssh-password" not in text
    assert PROTECTION_ID in text
    assert loaded[0].password == "fake-db-password"
    assert loaded[0].linux_password == "fake-ssh-password"


def test_later_save_of_a_protected_file_stays_encrypted(tmp_path):
    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server())
    migrate_servers_file(path)
    loaded = load_servers_from_path(path)
    loaded[0].host = "10.0.0.6"

    save_servers(loaded, path=path)
    text = path.read_text(encoding="utf-8")

    assert "fake-db-password" not in text
    assert load_servers_from_path(path)[0].password == "fake-db-password"
    assert load_servers_from_path(path)[0].host == "10.0.0.6"


def test_migration_failure_restores_the_original_file(tmp_path, monkeypatch):
    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server())
    original = path.read_bytes()

    def fail(_plain: bytes) -> bytes:
        raise CredentialProtectionError("protection failed in test")

    monkeypatch.setattr("desktop.connection_store.protect_bytes", fail)

    with pytest.raises(CredentialProtectionError):
        migrate_servers_file(path)

    assert path.read_bytes() == original
    assert (tmp_path / "saved_servers.json.bak").read_bytes() == original


def test_migration_refuses_the_live_store_path(tmp_path, monkeypatch):
    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server())
    monkeypatch.setattr("desktop.connection_store.STORE_PATH", path)

    with pytest.raises(CredentialProtectionError, match="live saved_servers.json"):
        migrate_servers_file(path)

    assert path.read_text(encoding="utf-8").count("fake-db-password") == 1


def test_second_migration_does_not_replace_the_plaintext_backup(tmp_path):
    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server())
    backup = migrate_servers_file(path)
    plaintext_backup = backup.read_bytes()

    with pytest.raises(CredentialProtectionError, match="already protected"):
        migrate_servers_file(path)

    assert backup.read_bytes() == plaintext_backup
    assert "fake-db-password" not in path.read_text(encoding="utf-8")


def test_invalid_json_is_left_unchanged_and_has_no_backup(tmp_path):
    path = tmp_path / "saved_servers.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CredentialProtectionError, match="not valid JSON"):
        migrate_servers_file(path)

    assert path.read_text(encoding="utf-8") == "{not json"
    assert not (tmp_path / "saved_servers.json.bak").exists()


def test_reloaded_profile_fills_the_connection_form_without_showing_passwords(tmp_path, monkeypatch):
    import customtkinter as ctk

    from desktop.connection_panel import ConnectionPanel
    from desktop.theme import ThemeManager

    path = tmp_path / "saved_servers.json"
    _write_legacy(path, _server(ssh_enabled=True))
    migrate_servers_file(path)
    monkeypatch.setattr("desktop.connection_store.STORE_PATH", path)
    reloaded = load_saved_servers()[0]
    connection = reloaded.to_connection_result()
    logged: list[str] = []

    root = ctk.CTk()
    try:
        panel = ConnectionPanel(root, ThemeManager(), log_callback=lambda message, _level: logged.append(message))
        panel.load_from_connection(connection)
        panel.update_idletasks()

        assert panel.password_entry.get() == "fake-db-password"
        assert panel.linux_password_entry.get() == "fake-ssh-password"
        assert panel.password_entry.cget("show") == "*"
        assert panel.linux_password_entry.cget("show") == "*"
        assert "fake-db-password" not in panel.status_label.cget("text")
        assert "fake-ssh-password" not in panel.status_label.cget("text")
        assert all("fake-db-password" not in message and "fake-ssh-password" not in message for message in logged)
        assert path.read_text(encoding="utf-8").count("fake-db-password") == 0
        form = panel._get_form_values()
        assert form.password == "fake-db-password"
        assert form.linux_password == "fake-ssh-password"
    finally:
        root.destroy()


def test_missing_file_is_not_created(tmp_path):
    missing = tmp_path / "absent.json"

    with pytest.raises(CredentialProtectionError, match="not found"):
        migrate_servers_file(missing)

    assert not missing.exists()
