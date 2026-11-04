"""Port/engine mismatch helpers used by the Servers Connect form."""

from backend.models.schemas import DatabaseType
from desktop.connection import coerce_db_type_for_port


def test_postgres_tile_with_oracle_port_becomes_oracle():
    assert coerce_db_type_for_port(DatabaseType.POSTGRESQL, 1521) == DatabaseType.ORACLE


def test_oracle_tile_with_postgres_port_becomes_postgres():
    assert coerce_db_type_for_port(DatabaseType.ORACLE, 5432) == DatabaseType.POSTGRESQL


def test_matching_engine_and_port_unchanged():
    assert coerce_db_type_for_port(DatabaseType.ORACLE, 1521) == DatabaseType.ORACLE
    assert coerce_db_type_for_port(DatabaseType.POSTGRESQL, 5432) == DatabaseType.POSTGRESQL
