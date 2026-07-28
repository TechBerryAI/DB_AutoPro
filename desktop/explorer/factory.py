"""Factory for database explorer metadata providers."""

from __future__ import annotations

from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult
from desktop.explorer.provider import ExplorerProvider
from desktop.explorer.stub_provider import StubExplorerProvider


def get_explorer_provider(connection: ConnectionResult) -> ExplorerProvider:
    """Return the metadata provider for the active connection engine."""
    if connection.db_type == DatabaseType.POSTGRESQL:
        from desktop.explorer.postgres_provider import PostgresExplorerProvider

        return PostgresExplorerProvider(connection)
    if connection.db_type == DatabaseType.MYSQL:
        from desktop.explorer.mysql_provider import MySQLExplorerProvider

        return MySQLExplorerProvider(connection)
    return StubExplorerProvider(connection)
