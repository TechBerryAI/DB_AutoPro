"""MongoDB Linux Server restore: binary discovery and credential use."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.models.schemas import (
    DatabaseType,
    MongoRestoreScope,
    RestoreRequest,
    RestoreSourceType,
    RestoreTargetType,
)
from backend.services.mongo_tools import (
    is_placeholder_remote_mongo_path,
    sibling_mongo_tool_path,
)
from backend.services.restore_catalog import describe_restore_command
from backend.services.restore_service import RestoreService
from backend.services.ssh_client import resolve_remote_mongorestore_path

STALE_DUMP = "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump"
STALE_RESTORE = "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongorestore"
DISCOVERED = (
    "/riya/standalone/mongodb-database-tools-ubuntu2204-x86_64-100.19.1/bin/mongorestore"
)
EXPLICIT = "/opt/custom/bin/mongorestore"

SSH = {
    "ssh_host": "192.168.0.73",
    "fallback_host": "192.168.0.73",
    "ssh_port": 22,
    "username": "mongodb",
    "password": "ssh-secret",
    "mongodump_path": STALE_DUMP,
}


def _mongo_conn() -> SimpleNamespace:
    return SimpleNamespace(
        host="127.0.0.1",
        port=55000,
        username="riya",
        password="mongo-secret",
        database="amazon_db",
        extra_options={
            "auth_source": "admin",
            "mongo_host": "192.168.0.73",
            "mongo_port": 27018,
            "ssh_enabled": True,
            "ssh_host": "192.168.0.73",
            "linux_username": "mongodb",
            "linux_password": "ssh-secret",
            "mongodump_path": STALE_DUMP,
        },
    )


def test_plan_mongorestore_does_not_stat_linux_folder(monkeypatch) -> None:
    """Clicking /riya on Restore must not call Path.exists() (Windows hang)."""
    real_exists = Path.exists
    real_is_dir = Path.is_dir
    real_is_file = Path.is_file

    def _is_linux_click(path: Path) -> bool:
        text = str(path).replace("\\", "/").rstrip("/")
        return text == "/riya" or text.endswith("/riya")

    def forbidden_exists(self):
        if _is_linux_click(self):
            raise AssertionError(f"must not stat Linux path on Windows: {self}")
        return real_exists(self)

    def forbidden_is_dir(self):
        if _is_linux_click(self):
            raise AssertionError(f"must not stat Linux path on Windows: {self}")
        return real_is_dir(self)

    def forbidden_is_file(self):
        if _is_linux_click(self):
            raise AssertionError(f"must not stat Linux path on Windows: {self}")
        return real_is_file(self)

    monkeypatch.setattr(Path, "exists", forbidden_exists)
    monkeypatch.setattr(Path, "is_dir", forbidden_is_dir)
    monkeypatch.setattr(Path, "is_file", forbidden_is_file)

    from backend.services.mongo_restore import plan_mongorestore

    plan = plan_mongorestore(
        RestoreRequest(
            db_type=DatabaseType.MONGODB,
            backup_path="/riya",
            mongo_restore_scope=MongoRestoreScope.ALL_DATABASES,
            restore_source=RestoreSourceType.CONNECTED_SERVER,
            restore_target=RestoreTargetType.LINUX_SERVER,
            validate_backup=False,
        ),
        Path("/riya"),
    )
    assert plan.source_path == "/riya"


def test_placeholder_detects_stale_database_tools_default() -> None:
    assert is_placeholder_remote_mongo_path(STALE_RESTORE)
    assert is_placeholder_remote_mongo_path(STALE_DUMP)
    assert is_placeholder_remote_mongo_path("")
    assert is_placeholder_remote_mongo_path(None)
    assert not is_placeholder_remote_mongo_path(DISCOVERED)
    assert not is_placeholder_remote_mongo_path("/usr/bin/mongorestore")


def test_sibling_path_ignores_stale_default() -> None:
    assert sibling_mongo_tool_path(STALE_DUMP, "mongorestore") is None
    assert sibling_mongo_tool_path(DISCOVERED.replace("mongorestore", "mongodump"), "mongorestore") == DISCOVERED


def test_discover_mongorestore_skips_stale_path(monkeypatch) -> None:
    commands: list[str] = []

    def fake_run(*, command, **_kwargs):
        commands.append(command)
        if "test -x" in command:
            return 1, "", ""
        if "command -v mongorestore" in command:
            return 1, "", ""
        if "find " in command:
            return 0, DISCOVERED + "\n", ""
        return 1, "", ""

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fake_run)
    found = resolve_remote_mongorestore_path(SSH, STALE_RESTORE)
    assert found == DISCOVERED
    assert not any(STALE_RESTORE in cmd and "test -x" in cmd for cmd in commands)


def test_discover_mongorestore_uses_explicit_working_path(monkeypatch) -> None:
    def fake_run(*, command, **_kwargs):
        if EXPLICIT in command and "test -x" in command:
            return 0, EXPLICIT + "\n", ""
        raise AssertionError(f"discovery should not run when preferred exists: {command}")

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fake_run)
    assert resolve_remote_mongorestore_path(SSH, EXPLICIT) == EXPLICIT


def test_discover_mongorestore_finds_home_standalone_layout(monkeypatch) -> None:
    def fake_run(*, command, **_kwargs):
        if "ls -1" in command and "$HOME/*/" in command:
            return 0, DISCOVERED + "\n", ""
        return 1, "", ""

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fake_run)
    assert resolve_remote_mongorestore_path(SSH, None) == DISCOVERED


def test_discover_mongorestore_missing_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.services.ssh_client.run_ssh_command",
        lambda **_kwargs: (1, "", ""),
    )
    with pytest.raises(RuntimeError, match="mongorestore was not found"):
        resolve_remote_mongorestore_path(SSH, STALE_RESTORE)


def test_linux_restore_uses_mongo_credentials_and_discovered_binary(
    tmp_path: Path, monkeypatch
) -> None:
    dump = tmp_path / "Bakcup_whole"
    dump.mkdir()
    (dump / "users.bson").write_bytes(b"bson")

    commands: list[str] = []

    monkeypatch.setattr(
        "backend.services.ssh_client.resolve_remote_mongorestore_path",
        lambda ssh, preferred=None: DISCOVERED,
    )
    monkeypatch.setattr(
        "backend.services.ssh_client.upload_remote_directory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.services.ssh_client.remove_remote_path",
        lambda *_args, **_kwargs: None,
    )

    def fake_run(*, command, username, **kwargs):
        commands.append(command)
        assert username == "mongodb"
        return 0, "", ""

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fake_run)

    request = RestoreRequest(
        db_type=DatabaseType.MONGODB,
        backup_path=str(dump),
        database="amazon_db",
        mongo_restore_scope=MongoRestoreScope.SPECIFIC_DATABASE,
        restore_source=RestoreSourceType.CONNECTED_SERVER,
        restore_target=RestoreTargetType.LINUX_SERVER,
        remote_binary_path=STALE_RESTORE,
        validate_backup=False,
    )
    RestoreService()._restore_mongodb_via_ssh(
        request,
        _mongo_conn(),
        dump,
        SSH,
        None,
        None,
    )

    assert commands
    remote_cmd = commands[-1]
    assert DISCOVERED in remote_cmd
    assert "--username riya" in remote_cmd
    assert "--authenticationDatabase admin" in remote_cmd
    assert "--host 192.168.0.73" in remote_cmd
    assert "--port 27018" in remote_cmd
    assert "mongodb" not in remote_cmd.split("--username", 1)[-1].split()[0]
    assert STALE_RESTORE not in remote_cmd


def test_linux_restore_ignores_stale_preferred_when_resolving(monkeypatch, tmp_path: Path) -> None:
    dump = tmp_path / "dump"
    dump.mkdir()
    seen: list[str | None] = []

    monkeypatch.setattr(
        "backend.services.ssh_client.resolve_remote_mongorestore_path",
        lambda ssh, preferred=None: seen.append(preferred) or DISCOVERED,
    )
    monkeypatch.setattr(
        "backend.services.ssh_client.upload_remote_directory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.services.ssh_client.remove_remote_path",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.services.ssh_client.run_ssh_command",
        lambda **_kwargs: (0, "", ""),
    )

    request = RestoreRequest(
        db_type=DatabaseType.MONGODB,
        backup_path=str(dump),
        database="amazon_db",
        mongo_restore_scope=MongoRestoreScope.SPECIFIC_DATABASE,
        restore_source=RestoreSourceType.CONNECTED_SERVER,
        remote_binary_path=STALE_RESTORE,
        validate_backup=False,
    )
    RestoreService()._restore_mongodb_via_ssh(
        request, _mongo_conn(), dump, SSH, None, None
    )
    assert seen == [None]


def test_restore_catalog_preview_does_not_use_stale_path(tmp_path: Path, monkeypatch) -> None:
    dump = tmp_path / "dump"
    dump.mkdir()
    monkeypatch.setattr(
        "backend.services.restore_catalog.resolve_connection",
        lambda _profile: _mongo_conn(),
    )
    preview = describe_restore_command(
        RestoreRequest(
            db_type=DatabaseType.MONGODB,
            backup_path=str(dump),
            database="amazon_db",
            mongo_restore_scope=MongoRestoreScope.SPECIFIC_DATABASE,
            restore_source=RestoreSourceType.CONNECTED_SERVER,
            restore_target=RestoreTargetType.LINUX_SERVER,
            remote_binary_path=STALE_RESTORE,
            validate_backup=False,
        )
    )
    assert STALE_RESTORE not in preview
    assert "mongorestore" in preview
    assert "--username riya" in preview
    assert "192.168.0.73" in preview
