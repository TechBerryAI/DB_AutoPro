"""MongoDB database connector."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class MongoDBConnector(BaseConnector):
    """MongoDB connector using pymongo."""

    db_type = DatabaseType.MONGODB

    def _get_client(self) -> Any:
        try:
            from pymongo import MongoClient

            uri = (
                f"mongodb://{self.config.username}:{self.config.password}"
                f"@{self.config.host}:{self.config.port}/{self.config.database}"
            )
            return MongoClient(uri, serverSelectionTimeoutMS=5000)
        except ImportError as exc:
            raise ConnectorError("pymongo not installed") from exc
        except Exception as exc:
            raise ConnectorError(f"MongoDB connection failed: {exc}") from exc

    def test_connection(self) -> bool:
        client = self._get_client()
        try:
            client.admin.command("ping")
            return True
        finally:
            client.close()

    def execute_query(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        """For MongoDB, query is collection name; returns collection stats."""
        client = self._get_client()
        try:
            db = client[self.config.database]
            if query in db.list_collection_names():
                stats = db.command("collStats", query)
                return [{"collection": query, "count": stats.get("count", 0)}]
            return []
        finally:
            client.close()

    def execute_script(self, script: str) -> None:
        raise ConnectorError("MongoDB does not support SQL script execution")

    def get_version(self) -> str:
        client = self._get_client()
        try:
            info = client.server_info()
            return info.get("version", "unknown")
        finally:
            client.close()
