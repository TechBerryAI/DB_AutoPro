"""Database connector base class and factory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from backend.core.config import DatabaseConnectionConfig, get_settings
from backend.core.exceptions import ConnectorError
from backend.core.logging import get_logger
from backend.models.schemas import DatabaseType

logger = get_logger("connectors")


@dataclass
class ConnectionInfo:
    """Resolved connection parameters."""

    host: str
    port: int
    database: str
    username: str
    password: str
    ssl_mode: str | None = None
    extra_options: dict[str, Any] | None = None


class BaseConnector(ABC):
    """Abstract base for all database connectors."""

    db_type: DatabaseType

    def __init__(self, config: ConnectionInfo) -> None:
        self.config = config
        self._logger = get_logger(f"connectors.{self.db_type.value}")

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify connectivity to the database."""

    @abstractmethod
    def execute_query(self, query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """Execute a read query and return results."""

    @abstractmethod
    def execute_script(self, script: str) -> None:
        """Execute a SQL/script without returning results."""

    @abstractmethod
    def get_version(self) -> str:
        """Return database server version string."""

    def health_check(self) -> dict[str, Any]:
        """Perform health check and return status dict."""
        import time

        start = time.perf_counter()
        try:
            ok = self.test_connection()
            version = self.get_version() if ok else "unknown"
            latency = (time.perf_counter() - start) * 1000
            return {
                "status": "healthy" if ok else "unhealthy",
                "version": version,
                "latency_ms": round(latency, 2),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }


def resolve_connection(profile: str = "default") -> ConnectionInfo:
    """Resolve connection config from settings profile."""
    settings = get_settings()
    if profile not in settings.connections:
        raise ConnectorError(f"Connection profile '{profile}' not found")
    cfg: DatabaseConnectionConfig = settings.connections[profile]
    return ConnectionInfo(
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
        username=cfg.username,
        password=cfg.password.get_secret_value(),
        ssl_mode=cfg.ssl_mode,
        extra_options=cfg.extra_options,
    )


def get_connector(db_type: DatabaseType, profile: str = "default") -> BaseConnector:
    """Factory method to create appropriate database connector."""
    config = resolve_connection(profile)
    return get_connector_from_info(db_type, config)


def get_connector_from_info(db_type: DatabaseType, config: ConnectionInfo) -> BaseConnector:
    """Create a connector directly from resolved connection parameters."""
    from backend.connectors.postgresql import PostgreSQLConnector
    from backend.connectors.mysql import MySQLConnector
    from backend.connectors.mongodb import MongoDBConnector
    from backend.connectors.mssql import MSSQLConnector
    from backend.connectors.oracle import OracleConnector

    connectors: dict[DatabaseType, type[BaseConnector]] = {
        DatabaseType.POSTGRESQL: PostgreSQLConnector,
        DatabaseType.MYSQL: MySQLConnector,
        DatabaseType.MONGODB: MongoDBConnector,
        DatabaseType.MSSQL: MSSQLConnector,
        DatabaseType.ORACLE: OracleConnector,
    }

    connector_cls = connectors.get(db_type)
    if not connector_cls:
        raise ConnectorError(f"Unsupported database type: {db_type}")
    return connector_cls(config)
