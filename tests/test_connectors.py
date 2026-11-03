"""Database connector tests."""

from unittest.mock import MagicMock, patch

import pytest

from backend.connectors.base import ConnectionInfo, get_connector, resolve_connection
from backend.connectors.mongodb import MongoDBConnector
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


def test_mongodb_auth_sources_include_username_and_destination():
    connector = MongoDBConnector(
        ConnectionInfo(
            host="127.0.0.1",
            port=27017,
            database="postgres",
            username="denabank",
            password="secret",
            extra_options={"auth_source_hints": ["shop"]},
        )
    )
    assert connector._auth_source_candidates() == ["admin", "postgres", "denabank", "shop"]
    explicit = MongoDBConnector(
        ConnectionInfo(
            host="127.0.0.1",
            port=27017,
            database="postgres",
            username="denabank",
            password="secret",
            extra_options={"auth_source": "denabank"},
        )
    )
    assert explicit._auth_source_candidates() == ["denabank"]


def test_mongodb_auth_failure_lists_tried_sources():
    connector = MongoDBConnector(
        ConnectionInfo(
            host="127.0.0.1",
            port=27017,
            database="",
            username="denabank",
            password="secret",
        )
    )

    class _Admin:
        def command(self, _name):
            raise Exception("Authentication failed")

    class _FailingClient:
        def __init__(self, *_args, **_kwargs):
            self.admin = _Admin()

        def close(self):
            return None

    with patch("pymongo.MongoClient", side_effect=_FailingClient):
        with pytest.raises(ConnectorError, match="Tried authSource: admin, denabank"):
            connector._get_client()


