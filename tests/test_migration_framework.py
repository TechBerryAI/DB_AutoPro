"""Unit tests for the shared migration registry and discovery helpers."""

from __future__ import annotations

from backend.services.migration_framework.discovery import list_accessible_databases
from backend.services.migration_framework.registry import (
    implemented_pairs,
    pair_capability,
    servers_matching_type,
    source_types,
    target_types_for,
    unsupported_pair_reason,
)
from desktop.pg_migration_flow import (
    SOURCE_TYPES,
    TARGET_TYPES,
    pair_summary,
    servers_for_type,
    unsupported_migration_reason,
)


class _Server:
    def __init__(self, name: str, db_type: str) -> None:
        self.name = name
        self.db_type = db_type


def test_only_implemented_pairs_are_registered():
    pairs = {(item.source, item.target) for item in implemented_pairs()}
    assert pairs == {("postgresql", "postgresql"), ("postgresql", "mysql")}
    assert source_types() == ("postgresql",)
    assert target_types_for("postgresql") == ("postgresql", "mysql")
    assert target_types_for("mysql") == ()
    assert SOURCE_TYPES == ("postgresql",)
    assert TARGET_TYPES == ("postgresql", "mysql")


def test_pair_capability_reports_limitations():
    pg = pair_capability("postgresql", "postgresql")
    mysql = pair_capability("PostgreSQL", "MySQL")
    assert pg is not None and pg.implemented
    assert mysql is not None and mysql.implemented
    assert pg.views is False
    assert mysql.type_conversion is True
    assert mysql.views is False
    assert mysql.programmable_objects is False
    assert "MySQL DDL commits immediately" in mysql.summary()
    assert pair_capability("mysql", "postgresql") is None
    assert pair_capability("mongodb", "postgresql") is None


def test_unsupported_pairs_are_blocked_with_an_explanation():
    mysql_source = unsupported_pair_reason("mysql", "postgresql")
    mongo = unsupported_pair_reason("mongodb", "mongodb")
    redis = unsupported_pair_reason("redis", "postgresql")
    oracle = unsupported_pair_reason("oracle", "mssql")
    assert mysql_source and "not implemented" in mysql_source
    assert "No schema or data was copied." in mysql_source
    assert mongo and "MongoDB" in mongo and "not treated as a relational" in mongo
    assert redis and "Redis keys are not mapped" in redis
    assert oracle and "oracle-to-mssql" in oracle
    assert unsupported_migration_reason("postgresql", "mysql") is None
    assert "postgresql to mysql" in pair_summary("postgresql", "mysql").lower()
    assert "not implemented" in pair_summary("sqlite", "postgresql")


def test_saved_servers_filter_by_stored_type_not_display_name():
    servers = [
        _Server("mysql-prod", "postgresql"),
        _Server("postgres", "postgresql"),
        _Server("app", "mysql"),
        _Server("Maria", "mariadb"),
        _Server("docs", "mongodb"),
    ]
    postgres = servers_matching_type(servers, "postgresql")
    mysql = servers_for_type(servers, "mysql")
    assert [item.name for item in postgres] == ["mysql-prod", "postgres"]
    assert [item.name for item in mysql] == ["app", "Maria"]
    assert servers_matching_type(servers, "mongodb")[0].name == "docs"
    assert servers_matching_type(servers, "oracle") == []


def test_list_accessible_databases_skips_system_catalogs():
    def pg(_sql: str):
        return [
            {"name": "postgres"},
            {"name": "template0"},
            {"name": "template1"},
            {"name": "app"},
        ]

    def mysql(_sql: str):
        return [
            {"Database": "mysql"},
            {"Database": "information_schema"},
            {"Database": "employees"},
            {"Database": "sys"},
        ]

    def broken(_sql: str):
        raise RuntimeError("disconnected")

    assert list_accessible_databases("postgresql", pg) == ["postgres", "app"]
    assert list_accessible_databases("mysql", mysql) == ["employees"]
    assert list_accessible_databases("mongodb", pg) == []
    assert list_accessible_databases("postgresql", broken) == []


def test_postgresql_connector_does_not_fall_back_to_disable_when_prefer_is_set(monkeypatch):
    import types

    from backend.connectors.base import ConnectionInfo
    from backend.connectors.postgresql import PostgreSQLConnector

    tried: list[str] = []

    def connect(**kwargs):
        tried.append(str(kwargs.get("sslmode")))
        raise RuntimeError(f"rejected {kwargs.get('sslmode')}")

    fake = types.SimpleNamespace(
        connect=connect,
        extras=types.SimpleNamespace(RealDictCursor=object),
    )
    monkeypatch.setitem(__import__("sys").modules, "psycopg2", fake)
    monkeypatch.setitem(__import__("sys").modules, "psycopg2.extras", fake.extras)

    connector = PostgreSQLConnector(
        ConnectionInfo(
            host="127.0.0.1",
            port=5432,
            database="postgres",
            username="postgres",
            password="x",
            ssl_mode="prefer",
        )
    )
    try:
        connector._connect()
    except Exception:
        pass
    assert tried == ["prefer", "require"]
    assert "disable" not in tried


def test_destination_copy_is_named_from_the_source_and_not_a_saved_default():
    from backend.services.migration_framework.destination import (
        create_mysql_destination,
        destination_copy_name,
        destination_identity_error,
        destination_name_error,
        mysql_destination_exists,
        server_probe_database,
    )

    assert destination_copy_name("postgres", "mysql") == "postgres"
    assert destination_copy_name("sales-app", "mysql") == "sales_app"
    assert destination_copy_name("mysql", "mysql") == "mysql_copy"
    assert destination_copy_name("postgres", "postgresql") == "postgres"
    assert destination_name_error("employees", "mysql") is None
    assert destination_name_error("sys", "mysql") is not None
    assert destination_name_error("", "mysql") is not None
    assert server_probe_database("mysql") == ""
    assert server_probe_database("postgresql") == "postgres"
    assert destination_identity_error("postgres", "", dest_exists=False) is None
    assert destination_identity_error("postgres", "employees", dest_exists=True, discovered=["employees"]) is not None
    assert destination_identity_error("postgres", "", dest_exists=True, discovered=["postgres", "app"]) is None

    created = []
    assert mysql_destination_exists(lambda _sql: [{"Database": "app"}], "postgres") is False
    assert mysql_destination_exists(lambda _sql: [{"Database": "postgres"}], "postgres") is True
    create_mysql_destination(created.append, "postgres")
    assert created == ["CREATE DATABASE IF NOT EXISTS `postgres` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"]


def test_changing_target_type_changes_the_plan_key():
    from desktop.pg_migration_flow import MigrationFormState

    postgres = MigrationFormState(
        source_type="postgresql",
        source_server="postgres · postgresql",
        source_database="postgres",
        target_type="postgresql",
        target_server="postgres · postgresql",
        target_database="postgres",
        scope="Schema and data",
        strategy="Skip existing tables",
        tables="",
        batch_size="1000",
        timeout_ms="0",
    )
    mysql = MigrationFormState(
        source_type="postgresql",
        source_server="postgres · postgresql",
        source_database="postgres",
        target_type="mysql",
        target_server="mysql · mysql",
        target_database="app",
        scope="Schema and data",
        strategy="Skip existing tables",
        tables="",
        batch_size="1000",
        timeout_ms="0",
    )
    assert postgres.plan_key() != mysql.plan_key()
    assert postgres.target_key() != mysql.target_key()
