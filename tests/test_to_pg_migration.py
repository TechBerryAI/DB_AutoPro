"""Inbound PostgreSQL migration mapping and copy path."""

from __future__ import annotations

import json
import threading

from backend.services.pg_migration.precheck import SCOPE_SCHEMA, SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP
from backend.services.to_pg_migration.mapping import adapt_value, map_column, quote_pg
from backend.services.to_pg_migration.schema import TableSpec, ColumnSpec, create_table_sql, insert_sql, mongo_spec
from backend.services.to_pg_migration.engine import run_to_pg_migration
from backend.services.to_pg_migration.precheck import assess_to_pg
from backend.services.to_pg_migration.workflow import build_to_pg_plan, start_to_pg_migration


def test_mysql_and_mssql_types_map_into_postgresql():
    pg_type, lossy, error = map_column("mysql", "bigint")
    assert pg_type == "BIGINT" and error is None
    varchar, _note, error = map_column("mysql", "varchar", length=40)
    assert varchar == "VARCHAR(40)" and error is None
    enum_type, lossy, error = map_column("mariadb", "enum")
    assert enum_type == "TEXT" and lossy and error is None
    bit, lossy, error = map_column("mssql", "bit")
    assert bit == "BOOLEAN" and error is None
    xml, lossy, error = map_column("mssql", "xml")
    assert xml == "TEXT" and lossy and error is None
    number, _note, error = map_column("oracle", "number", precision=10, scale=2)
    assert number == "NUMERIC(10,2)" and error is None
    unknown, _note, error = map_column("mysql", "geometry")
    assert unknown is None and "cannot be mapped" in error
    assert quote_pg('ord"ers') == '"ord""ers"'
    assert json.loads(adapt_value({"a": 1}, "JSONB")) == {"a": 1}


def test_mongo_collection_becomes_id_and_jsonb():
    spec = mongo_spec("orders")
    sql = create_table_sql(spec)
    assert '"_id" TEXT NOT NULL' in sql
    assert '"doc" JSONB NOT NULL' in sql
    assert "PRIMARY KEY" in sql
    assert insert_sql(spec).startswith('INSERT INTO "orders"')


class _Target:
    def __init__(self):
        self.statements = []
        self.batches = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def executemany(self, sql, rows):
        self.batches.append(list(rows))

    def query(self, sql, params=None):
        return [{"row_count": sum(len(batch) for batch in self.batches)}]

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_mysql_table_copy_inserts_into_postgresql():
    spec = TableSpec(
        "orders",
        columns=[ColumnSpec("id", "INTEGER", False, True), ColumnSpec("note", "TEXT", True)],
        primary_key=["id"],
    )
    target = _Target()

    def fetch(_table, _columns, _batch, _cancel):
        yield [(1, "a"), (2, None)]

    result = run_to_pg_migration(
        [spec], [], scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=2,
        fetch_batches=fetch, target=target, count_source=lambda _name: 2,
        cancel=threading.Event(), migration_id="to-pg-1",
    )
    assert result.status == "Completed and verified"
    assert any(sql.startswith('CREATE TABLE "orders"') for sql in target.statements)
    assert target.batches == [[(1, "a"), (2, None)]]


def test_existing_postgresql_table_can_be_skipped():
    spec = TableSpec("orders", columns=[ColumnSpec("id", "INTEGER", False)], primary_key=["id"])
    target = _Target()
    result = run_to_pg_migration(
        [spec], ["orders"], scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=10,
        fetch_batches=lambda *_args: iter(()), target=target, count_source=lambda _name: 0,
        cancel=threading.Event(), migration_id="to-pg-skip",
    )
    assert result.status == "Completed with warnings"
    assert result.outcomes[0].action == "skipped"
    assert target.statements == []


def test_precheck_accepts_mongodb_collections_and_builds_a_plan():
    def target_query(sql):
        if "version()" in sql:
            return [{"version": "PostgreSQL 16", "database_name": "app"}]
        return []

    report = assess_to_pg(
        lambda _sql: [],
        target_query,
        source_kind="mongodb",
        scope=SCOPE_SCHEMA,
        selected_tables=None,
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=True,
        batch_size=100,
        statement_timeout_ms=0,
        expected_database="app",
        mongo_collections=["orders", "items"],
        source_database="shop",
    )
    assert report.ready
    assert report.tables == ["orders", "items"]
    plan = build_to_pg_plan(report, scope=SCOPE_SCHEMA, strategy=STRATEGY_SKIP, batch_size=100, statement_timeout_ms=0)
    assert "mongodb to PostgreSQL" in plan.text
    started = start_to_pg_migration(
        plan, confirmed=True, operator="tester",
        fetch_batches=lambda *_args: iter(()), target=_Target(),
        count_source=lambda _name: 0, cancel=threading.Event(),
    )
    assert started.status == "Completed and verified"
