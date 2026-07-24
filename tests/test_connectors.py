"""Database connector tests."""

from unittest.mock import MagicMock, patch

import pytest

from backend.connectors.base import get_connector, resolve_connection
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


def test_resolve_connection():
    conn = resolve_connection("default")
    assert conn.host == "localhost"
    assert conn.port == 5432

 
def test_get_connector_postgresql():
    connector = get_connector(DatabaseType.POSTGRESQL)
    assert connector.db_type == DatabaseType.POSTGRESQL


def test_get_connector_invalid_profile():
    with pytest.raises(ConnectorError):
        resolve_connection("nonexistent_profile")


def test_postgresql_health_check_mock():
    connector = get_connector(DatabaseType.POSTGRESQL)
    with patch.object(connector, "test_connection", return_value=True):
        with patch.object(connector, "get_version", return_value="PostgreSQL 15"):
            result = connector.health_check()
            assert result["status"] == "healthy"


