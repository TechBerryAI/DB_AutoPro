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
        )


def load_saved_servers() -> list[SavedServer]:
    if not STORE_PATH.exists():
        return []
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        return [SavedServer.from_dict(item) for item in data.get("servers", [])]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return []


def save_servers(servers: list[SavedServer]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"servers": [asdict(server) for server in servers]}
    STORE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def upsert_server(server: SavedServer) -> None:
    servers = [s for s in load_saved_servers() if s.name != server.name]
    servers.append(server)
    save_servers(servers)


def remove_server(name: str) -> bool:
    servers = load_saved_servers()
    filtered = [s for s in servers if s.name != name]
    if len(filtered) == len(servers):
        return False
    save_servers(filtered)
    return True
