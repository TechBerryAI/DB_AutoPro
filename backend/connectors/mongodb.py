"""MongoDB database connector."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class MongoDBConnector(BaseConnector):
    """MongoDB connector using pymongo."""

    db_type = DatabaseType.MONGODB

    def _auth_source_candidates(self) -> list[str]:
        opts = self.config.extra_options or {}
        explicit = (opts.get("auth_source") or opts.get("authSource") or "").strip()
        if explicit:
            return [explicit]

        # Auth database is optional. MongoDB users are usually created in admin,
        # so try that before the database named in the form.
        database = (self.config.database or "admin").strip() or "admin"
        candidates = ["admin"]
        if database not in candidates:
            candidates.append(database)
        return candidates

    @staticmethod
    def _is_auth_failure(exc: BaseException) -> bool:
        from pymongo.errors import OperationFailure

        if isinstance(exc, OperationFailure) and exc.code == 18:
            return True
        message = str(exc).lower()
        return "authentication failed" in message or "authenticationfailed" in message

    def _build_uri(self, auth_source: str | None = None) -> str:
        """Build a MongoDB URI with RFC 3986–escaped credentials."""
        host = self.config.host
        port = self.config.port
        database = self.config.database or "admin"
        username = self.config.username
        password = self.config.password
        params = f"?authSource={quote_plus(auth_source)}" if auth_source and username else ""

        if username:
            user = quote_plus(username)
            if password:
                creds = f"{user}:{quote_plus(password)}@"
            else:
                creds = f"{user}@"
            return f"mongodb://{creds}{host}:{port}/{database}{params}"
        return f"mongodb://{host}:{port}/{database}"

    def _get_client(self) -> Any:
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise ConnectorError("pymongo not installed") from exc

        last_error: Exception | None = None
        for auth_source in self._auth_source_candidates():
            try:
                client = MongoClient(
                    self._build_uri(auth_source),
                    serverSelectionTimeoutMS=5000,
                )
                client.admin.command("ping")
                return client
            except Exception as exc:
                if self._is_auth_failure(exc):
                    last_error = exc
                    continue
                raise ConnectorError(f"MongoDB connection failed: {exc}") from exc

        if last_error:
            tried = ", ".join(self._auth_source_candidates())
            raise ConnectorError(
                f"MongoDB authentication failed for user '{self.config.username}'. "
                f"Tried authSource: {tried}. "
                "Leave Auth database empty to use admin, or set it to the database where this user was created."
            ) from last_error
        raise ConnectorError("MongoDB connection failed")

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

    def list_collections(self, database: str | None = None) -> list[dict[str, Any]]:
        """Return collection names and document counts for a database."""
        db_name = database or self.config.database or "admin"
        client = self._get_client()
        try:
            db = client[db_name]
            collections: list[dict[str, Any]] = []
            for name in sorted(db.list_collection_names()):
                try:
                    count = db[name].estimated_document_count()
                except Exception:
                    count = 0
                collections.append({"name": name, "count": count})
            return collections
        finally:
            client.close()
