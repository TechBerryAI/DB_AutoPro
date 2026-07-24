"""Persist saved database server profiles for the desktop connection dialog."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.models.schemas import DatabaseType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = PROJECT_ROOT / "config" / "saved_servers.json"


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
            password=data.get("password", ""),
            database=data.get("database", "postgres"),
            auth_database=data.get("auth_database", ""),
            ssh_enabled=bool(data.get("ssh_enabled", False)),
            linux_username=data.get("linux_username", ""),
            linux_password=data.get("linux_password", ""),
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


def _load_all_servers() -> list[SavedServer]:
    if not STORE_PATH.exists():
        return []
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        return [SavedServer.from_dict(item) for item in data.get("servers", [])]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
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


def save_servers(servers: list[SavedServer]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"servers": [asdict(server) for server in servers]}
    STORE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
