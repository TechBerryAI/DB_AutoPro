"""Remote backup listing for MySQL patch prechecks. No live SSH."""

from __future__ import annotations

import stat
from datetime import datetime, timezone

import pytest

from backend.services.mysql_patch.models import BackupReadinessState
from backend.services.mysql_patch.precheck import LocalBackupReadiness
from backend.services.mysql_patch.remote_backup import (
    DEFAULT_REMOTE_BACKUP_DIR,
    READ_LIMIT_BYTES,
    RemoteBackupBrowser,
    RemoteBackupError,
    configured_backup_directory,
    file_inside_directory,
)
from backend.services.ssh_host_keys import SshHostKeyError


class _Attr:
    def __init__(self, filename: str, mode: int, size: int, mtime: float) -> None:
        self.filename = filename
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = mtime


class _Handle:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.limit = None

    def read(self, limit: int) -> bytes:
        self.limit = limit
        return self.data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


class _Sftp:
    def __init__(self, entries: dict, files: dict | None = None, fail: Exception | None = None) -> None:
        self.entries = entries
        self.files = files or {}
        self.fail = fail
        self.reads: list[_Handle] = []

    def listdir_attr(self, path: str):
        if self.fail is not None:
            raise self.fail
        if path not in self.entries:
            raise FileNotFoundError(path)
        return self.entries[path]

    def lstat(self, path: str):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path][0]

    def open(self, path: str, mode: str = "rb"):
        assert mode == "rb"
        handle = _Handle(self.files[path][1])
        self.reads.append(handle)
        return handle


def _regular(name: str, size: int, mtime: float) -> _Attr:
    return _Attr(name, stat.S_IFREG | 0o644, size, mtime)


def _browser(sftp: _Sftp) -> RemoteBackupBrowser:
    def opener(_ssh):
        from contextlib import contextmanager

        @contextmanager
        def opened():
            yield sftp

        return opened()

    return RemoteBackupBrowser(open_sftp=opener)


def test_lists_regular_files_with_path_size_and_modification_time() -> None:
    stamp = 1_700_000_000
    folder = "/home/user/db_autopro_backups"
    sftp = _Sftp(
        {
            folder: [
                _regular("employees.sql", 1200, stamp),
                _Attr("notes", stat.S_IFDIR | 0o755, 4096, stamp),
                _Attr("link.sql", stat.S_IFLNK | 0o777, 12, stamp),
            ]
        }
    )
    files = _browser(sftp).list_files({}, folder)
    assert len(files) == 1
    assert files[0].name == "employees.sql"
    assert files[0].path == f"{folder}/employees.sql"
    assert files[0].size == 1200
    assert files[0].modified_at == datetime.fromtimestamp(stamp, tz=timezone.utc)


def test_empty_saved_directory_uses_the_page_default() -> None:
    assert configured_backup_directory("") == DEFAULT_REMOTE_BACKUP_DIR
    assert configured_backup_directory(None) == DEFAULT_REMOTE_BACKUP_DIR
    assert configured_backup_directory("/var/backups/mysql") == "/var/backups/mysql"


def test_rejects_symlink_names_and_path_traversal() -> None:
    with pytest.raises(RemoteBackupError):
        file_inside_directory("/home/user/db_autopro_backups", "../etc/passwd")
    with pytest.raises(RemoteBackupError):
        file_inside_directory("/home/user/db_autopro_backups", "..")
    with pytest.raises(RemoteBackupError):
        file_inside_directory("/home/user/db_autopro_backups", "nested/file.sql")
    with pytest.raises(RemoteBackupError):
        configured_backup_directory("/home/user/../etc")
    folder = "/home/user/db_autopro_backups"
    sftp = _Sftp(
        {folder: [_Attr("link.sql", stat.S_IFLNK | 0o777, 8, 10)]},
        files={f"{folder}/link.sql": (_Attr("link.sql", stat.S_IFLNK | 0o777, 8, 10), b"data")},
    )
    assert _browser(sftp).list_files({}, folder) == []
    with pytest.raises(RemoteBackupError):
        _browser(sftp).validate_file({}, folder, "link.sql")


def test_missing_directory_is_reported() -> None:
    browser = _browser(_Sftp({}))
    with pytest.raises(RemoteBackupError, match="not found"):
        browser.list_files({}, "/home/user/db_autopro_backups")


def test_ssh_failure_is_reported_without_a_password() -> None:
    sftp = _Sftp({}, fail=RuntimeError("login failed password=secret-value"))

    def opener(_ssh):
        raise RuntimeError("login failed password=secret-value")

    browser = RemoteBackupBrowser(open_sftp=opener)
    with pytest.raises(RemoteBackupError) as caught:
        browser.list_files({"password": "secret-value"}, "/home/user/db_autopro_backups")
    assert "secret-value" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    del sftp


def test_host_key_failure_is_not_rewritten() -> None:
    def opener(_ssh):
        raise SshHostKeyError(host="10.0.0.5", port=22, reason="unknown", detail="Host key is not trusted")

    browser = RemoteBackupBrowser(open_sftp=opener)
    with pytest.raises(SshHostKeyError):
        browser.list_files({}, "/home/user/db_autopro_backups")


def test_validation_reads_only_a_bounded_prefix() -> None:
    folder = "/home/user/db_autopro_backups"
    payload = b"A" * (READ_LIMIT_BYTES + 5000)
    attr = _regular("employees.sql", len(payload), 20)
    sftp = _Sftp({folder: [attr]}, {f"{folder}/employees.sql": (attr, payload)})
    check = _browser(sftp).validate_file({}, folder, "employees.sql")
    assert check.state == BackupReadinessState.READABLE_UNVERIFIED
    assert check.bytes_read == READ_LIMIT_BYTES
    assert sftp.reads[0].limit == READ_LIMIT_BYTES
    assert "does not" in check.summary.lower() or "not a proven restore" in check.summary.lower()
    assert "separate restore test" in check.summary


def test_empty_remote_file_is_not_trusted() -> None:
    folder = "/home/user/db_autopro_backups"
    attr = _regular("empty.sql", 0, 20)
    sftp = _Sftp({folder: [attr]}, {f"{folder}/empty.sql": (attr, b"")})
    check = _browser(sftp).validate_file({}, folder, "empty.sql")
    assert check.state == BackupReadinessState.UNREADABLE
    assert sftp.reads == []


def test_remote_path_is_not_opened_as_a_local_file(monkeypatch) -> None:
    def opened(*_args, **_kwargs):
        raise AssertionError("a remote path was opened locally")

    monkeypatch.setattr("backend.services.backup_service.BackupService.validate_backup", opened)
    monkeypatch.setattr("pathlib.Path.open", opened)
    state, summary = LocalBackupReadiness().assess("/home/user/db_autopro_backups/employees.sql")
    assert state == BackupReadinessState.UNREADABLE
    assert "Windows file" in summary

    folder = "/home/user/db_autopro_backups"
    payload = b"-- mysql dump\n"
    attr = _regular("employees.sql", len(payload), 30)
    check = _browser(_Sftp({folder: [attr]}, {f"{folder}/employees.sql": (attr, payload)})).validate_file(
        {}, folder, "employees.sql"
    )
    assert check.path.startswith("/")
    assert check.state == BackupReadinessState.READABLE_UNVERIFIED
