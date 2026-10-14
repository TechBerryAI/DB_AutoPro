"""List and lightly check backup files in one saved remote directory.

This module does not browse the rest of the server, download a full backup,
or treat a readable file as a proven restore.
"""

from __future__ import annotations

import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from backend.services.mysql_patch.models import BackupReadinessState
from backend.services.mysql_patch.redaction import redact_text

DEFAULT_REMOTE_BACKUP_DIR = "/home/user/db_autopro_backups"
READ_LIMIT_BYTES = 4096

_NOT_A_RESTORE = (
    "The first bytes were read from the remote file. "
    "A file that exists and is non-empty is not a proven restore. "
    "A separate restore test, or another approved recovery-readiness process, is still required."
)


class RemoteBackupError(Exception):
    """A remote backup path or SSH listing could not be used."""


@dataclass(frozen=True)
class RemoteBackupFile:
    """One regular file directly inside the configured backup directory."""

    name: str
    path: str
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class RemoteBackupCheck:
    """Result of a bounded read-only check."""

    path: str
    state: BackupReadinessState
    summary: str
    bytes_read: int = 0


def configured_backup_directory(remote_backup_dir: str | None) -> str:
    """Use the saved directory, or the page default when it is empty."""
    text = str(remote_backup_dir or "").strip()
    if not text:
        return DEFAULT_REMOTE_BACKUP_DIR
    return normalize_directory(text)


def normalize_directory(path: str) -> str:
    """Absolute directory with no parent segments."""
    text = str(path or "").strip()
    if not text.startswith("/") or "\\" in text or "\x00" in text:
        raise RemoteBackupError("The remote backup directory must be an absolute Linux path")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise RemoteBackupError("The remote backup directory cannot contain '..'")
        parts.append(part)
    if not parts:
        raise RemoteBackupError("The remote backup directory cannot be the filesystem root")
    return "/" + "/".join(parts)


def is_remote_linux_path(path: str | None) -> bool:
    """True when the text is an absolute Linux path rather than a Windows path."""
    text = str(path or "").strip()
    return text.startswith("/") and "\\" not in text


def file_inside_directory(directory: str, name: str) -> str:
    """Join one file name. Reject directories, parent segments, and other paths."""
    folder = normalize_directory(directory)
    filename = str(name or "").strip()
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
    ):
        raise RemoteBackupError("Only a regular file directly inside the backup directory can be selected")
    return f"{folder}/{filename}"


def _modified_at(attr) -> datetime:
    stamp = getattr(attr, "st_mtime", None)
    if stamp is None:
        raise RemoteBackupError("The remote file has no modification time")
    return datetime.fromtimestamp(float(stamp), tz=timezone.utc)


def _is_symlink(mode: int) -> bool:
    return stat.S_ISLNK(mode)


def _is_regular(mode: int) -> bool:
    return stat.S_ISREG(mode) and not _is_symlink(mode)


@contextmanager
def open_verified_sftp(ssh: dict) -> Iterator[object]:
    """Open SFTP through the existing host-key check. Callers must not log ssh."""
    from backend.services.ssh_client import _open_ssh_client

    client = _open_ssh_client(ssh)
    sftp = None
    try:
        sftp = client.open_sftp()
        yield sftp
    finally:
        if sftp is not None:
            sftp.close()
        client.close()


class RemoteBackupBrowser:
    """Read one backup directory. The opener is replaced in tests."""

    def __init__(self, open_sftp=open_verified_sftp) -> None:
        self._open_sftp = open_sftp

    def list_files(self, ssh: dict, directory: str) -> list[RemoteBackupFile]:
        folder = normalize_directory(directory)
        try:
            with self._open_sftp(ssh) as sftp:
                attributes = sftp.listdir_attr(folder)
        except FileNotFoundError as exc:
            raise RemoteBackupError(f"Remote backup directory was not found: {folder}") from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == 2:
                raise RemoteBackupError(f"Remote backup directory was not found: {folder}") from exc
            raise RemoteBackupError(redact_text(str(exc)) or "SSH could not list the backup directory") from exc
        except RemoteBackupError:
            raise
        except Exception as exc:
            from backend.services.ssh_host_keys import SshHostKeyError

            if isinstance(exc, SshHostKeyError):
                raise
            raise RemoteBackupError(redact_text(str(exc)) or "SSH could not list the backup directory") from exc

        files: list[RemoteBackupFile] = []
        for attr in attributes:
            name = str(getattr(attr, "filename", "") or "")
            if name in {"", ".", ".."} or "/" in name or "\\" in name or ".." in name.split("/"):
                continue
            mode = int(getattr(attr, "st_mode", 0) or 0)
            if not _is_regular(mode):
                continue
            files.append(
                RemoteBackupFile(
                    name=name,
                    path=f"{folder}/{name}",
                    size=int(getattr(attr, "st_size", 0) or 0),
                    modified_at=_modified_at(attr),
                )
            )
        files.sort(key=lambda item: item.name.lower())
        return files

    def validate_file(self, ssh: dict, directory: str, name: str) -> RemoteBackupCheck:
        """Stat the file and read at most READ_LIMIT_BYTES. Do not download the file."""
        path = file_inside_directory(directory, name)
        try:
            with self._open_sftp(ssh) as sftp:
                attr = sftp.lstat(path)
                mode = int(getattr(attr, "st_mode", 0) or 0)
                if _is_symlink(mode) or not _is_regular(mode):
                    raise RemoteBackupError("Only a regular file inside the backup directory can be selected")
                size = int(getattr(attr, "st_size", 0) or 0)
                if size <= 0:
                    return RemoteBackupCheck(
                        path=path,
                        state=BackupReadinessState.UNREADABLE,
                        summary="The remote backup file is empty. An empty file is not accepted as a backup.",
                    )
                with sftp.open(path, "rb") as handle:
                    chunk = handle.read(READ_LIMIT_BYTES)
        except RemoteBackupError:
            raise
        except FileNotFoundError as exc:
            raise RemoteBackupError("The selected remote backup file was not found") from exc
        except Exception as exc:
            from backend.services.ssh_host_keys import SshHostKeyError

            if isinstance(exc, SshHostKeyError):
                raise
            raise RemoteBackupError(redact_text(str(exc)) or "The remote backup file could not be read") from exc

        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="replace")
        chunk = bytes(chunk or b"")[:READ_LIMIT_BYTES]
        if not chunk:
            return RemoteBackupCheck(
                path=path,
                state=BackupReadinessState.UNREADABLE,
                summary="The remote backup file produced no readable bytes.",
            )
        if name.lower().endswith(".gz") and not chunk.startswith(b"\x1f\x8b"):
            return RemoteBackupCheck(
                path=path,
                state=BackupReadinessState.UNREADABLE,
                summary=(
                    "The remote gzip file does not start with a gzip header. "
                    "This limited read does not prove the backup can be restored."
                ),
                bytes_read=len(chunk),
            )
        return RemoteBackupCheck(
            path=path,
            state=BackupReadinessState.READABLE_UNVERIFIED,
            summary=_NOT_A_RESTORE,
            bytes_read=len(chunk),
        )
