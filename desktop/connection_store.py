"""Persist saved database server profiles for the desktop connection dialog.

Plaintext profiles still load. A file is encrypted only by migrate_servers_file(),
which refuses the live saved_servers.json path. After that migration, later
saves of an already protected file keep the passwords encrypted.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.core.paths import DATA_DIR
from backend.models.schemas import DatabaseType
from desktop.windows_dpapi import CredentialProtectionError, protect_bytes, unprotect_bytes

STORE_PATH = DATA_DIR / "saved_servers.json"
PROTECTION_ID = "dpapi-user-v1"
_PASSWORD_KEYS = ("password", "linux_password")


@dataclass
class SavedServer:
    """Named connection profile stored on disk."""

    name: str
    db_type: str
    host: str
    port: int
    username: str
    password: str
    database: str
    auth_database: str = ""
    ssh_enabled: bool = False
    linux_username: str = ""
    linux_password: str = ""
    ssh_host: str = ""
    ssh_port: int = 22
    mongodump_path: str = ""
    remote_backup_dir: str = ""
    local_output_dir: str = ""
    backup_folder_name: str = ""
    backup_collection: str = ""
    owner_username: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> SavedServer:
        return cls(
            name=data["name"],
            db_type=data.get("db_type", DatabaseType.POSTGRESQL.value),
            host=data.get("host", "localhost"),
            port=int(data.get("port", 5432)),
            username=data.get("username", ""),
            password=_reveal_secret(data, "password"),
            database=data.get("database", "postgres"),
            auth_database=data.get("auth_database", ""),
            ssh_enabled=bool(data.get("ssh_enabled", False)),
            linux_username=data.get("linux_username", ""),
            linux_password=_reveal_secret(data, "linux_password"),
            ssh_host=data.get("ssh_host", ""),
            ssh_port=int(data.get("ssh_port", 22)),
            mongodump_path=data.get("mongodump_path", ""),
            remote_backup_dir=data.get("remote_backup_dir", ""),
            local_output_dir=data.get("local_output_dir", ""),
            backup_folder_name=data.get("backup_folder_name", ""),
            backup_collection=data.get("backup_collection", ""),
            owner_username=data.get("owner_username", ""),
        )

    def to_connection_result(self):
        """Build a runtime connection from this saved profile."""
        from desktop.connection import ConnectionResult

        return ConnectionResult(
            name=self.name,
            db_type=DatabaseType(self.db_type),
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            database=self.database,
            saved_name=self.name,
            auth_database=self.auth_database or None,
            ssh_enabled=self.ssh_enabled,
            linux_username=self.linux_username,
            linux_password=self.linux_password,
            ssh_host=self.ssh_host or None,
            ssh_port=self.ssh_port,
            mongodump_path=self.mongodump_path,
            remote_backup_dir=self.remote_backup_dir,
        )


def _reveal_secret(data: dict, name: str) -> str:
    """Use a protected field when present. Otherwise keep the legacy plaintext."""
    protected = data.get(f"{name}_protected") or ""
    if protected:
        try:
            raw = base64.b64decode(str(protected), validate=True)
        except (ValueError, TypeError) as exc:
            raise CredentialProtectionError("Saved credential is not valid protected data.") from exc
        try:
            return unprotect_bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialProtectionError("Saved credential could not be read.") from exc
    return str(data.get(name) or "")


def _protected_server_dict(server: SavedServer) -> dict:
    payload = asdict(server)
    for name in _PASSWORD_KEYS:
        secret = str(payload.get(name) or "")
        payload[name] = ""
        payload[f"{name}_protected"] = base64.b64encode(protect_bytes(secret.encode("utf-8"))).decode("ascii") if secret else ""
    return payload


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _is_protected_file(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    return isinstance(data, dict) and data.get("credential_protection") == PROTECTION_ID


def _write_legacy(path: Path, servers: list[SavedServer]) -> None:
    payload = {"servers": [asdict(server) for server in servers]}
    _atomic_write(path, json.dumps(payload, indent=2))


def _write_protected(path: Path, servers: list[SavedServer]) -> None:
    payload = {
        "credential_protection": PROTECTION_ID,
        "servers": [_protected_server_dict(server) for server in servers],
    }
    _atomic_write(path, json.dumps(payload, indent=2))


def load_servers_from_path(path: Path) -> list[SavedServer]:
    """Load profiles from an explicit file. Used by tests and migration."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [SavedServer.from_dict(item) for item in data.get("servers", [])]


def migrate_servers_file(path: Path) -> Path:
    """Encrypt passwords in a chosen file and keep a byte-for-byte backup.

    Refuses the live application store. Call this only for a file you intend
    to migrate, after taking your own copy if you need one outside this backup.
    """
    target = Path(path)
    if target.resolve() == STORE_PATH.resolve():
        raise CredentialProtectionError(
            "Refusing to migrate the live saved_servers.json file. "
            "That step needs a separate approval."
        )
    if not target.exists():
        raise CredentialProtectionError("Saved server file was not found.")
    original = target.read_bytes()
    try:
        data = json.loads(original.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialProtectionError("Saved server file is not valid JSON.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("servers", []), list):
        raise CredentialProtectionError("Saved server file is not a server list.")
    if data.get("credential_protection") == PROTECTION_ID:
        raise CredentialProtectionError("This file is already protected. It was not changed.")
    backup = target.with_name(target.name + ".bak")
    backup.write_bytes(original)
    try:
        servers = [SavedServer.from_dict(item) for item in data.get("servers", [])]
        _write_protected(target, servers)
    except Exception:
        target.write_bytes(original)
        raise
    return backup


def _load_all_servers() -> list[SavedServer]:
    if not STORE_PATH.exists():
        return []
    try:
        return load_servers_from_path(STORE_PATH)
    except CredentialProtectionError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError, UnicodeError):
        return []


def _server_belongs_to_user(server: SavedServer, owner_username: str) -> bool:
    owner = (server.owner_username or "").strip().lower()
    user = owner_username.strip().lower()
    if not owner:
        # Legacy / shared profiles without an owner are visible to every signed-in user
        # (Servers sidebar already lists them unfiltered).
        return bool(user)
    return owner == user


def load_saved_servers(owner_username: str | None = None) -> list[SavedServer]:
    servers = _load_all_servers()
    if not owner_username:
        return servers
    return [s for s in servers if _server_belongs_to_user(s, owner_username)]


def save_servers(servers: list[SavedServer], *, path: Path | None = None) -> None:
    """Write profiles. An unprotected file stays plaintext until migrate_servers_file()."""
    target = Path(path) if path is not None else STORE_PATH
    if _is_protected_file(target):
        _write_protected(target, servers)
    else:
        _write_legacy(target, servers)


def upsert_server(server: SavedServer, owner_username: str | None = None) -> None:
    if owner_username and not server.owner_username:
        server.owner_username = owner_username
    owner_key = (server.owner_username or owner_username or "").strip().lower()
    servers = [
        s
        for s in _load_all_servers()
        if not (
            s.name == server.name
            and (s.owner_username or "").strip().lower() == owner_key
        )
    ]
    servers.append(server)
    save_servers(servers)


def remove_server(name: str, owner_username: str | None = None) -> bool:
    servers = _load_all_servers()
    if owner_username:
        user = owner_username.strip().lower()
        filtered = [
            s
            for s in servers
            if not (s.name == name and _server_belongs_to_user(s, user))
        ]
    else:
        filtered = [s for s in servers if s.name != name]
    if len(filtered) == len(servers):
        return False
    save_servers(filtered)
    return True
