"""PostgreSQL to MongoDB mapping and copy path."""

from __future__ import annotations

import threading
from decimal import Decimal

from backend.services.pg_migration.precheck import SCOPE_SCHEMA, SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP
from backend.services.pg_mongo_migration.mapping import adapt_mongo, row_document
from backend.services.pg_mongo_migration.schema import load_collection
from backend.services.pg_mongo_migration.engine import run_pg_mongo_migration
from backend.services.pg_mongo_migration.precheck import assess_pg_mongo
from backend.services.pg_mongo_migration.workflow import build_pg_mongo_plan, start_pg_mongo_migration


def test_postgresql_values_become_mongodb_documents():
    assert adapt_mongo(None) is None
    assert adapt_mongo(True) is True
    assert isinstance(adapt_mongo(Decimal("1.50")), (str, object))
    document = row_document(["id", "note"], (1, "a"), ["id"])
    assert document["_id"] == 1
    assert document["id"] == 1
    assert document["note"] == "a"
    composite = row_document(["a", "b"], (1, 2), ["a", "b"])
    assert composite["_id"] == {"a": 1, "b": 2}


class _Target:
    def __init__(self):
        self.collections = {}
        self.dropped = []

    def drop_collection(self, name: str) -> None:
        self.dropped.append(name)
        self.collections.pop(name, None)

    def ensure_collection(self, name: str) -> None:
        self.collections.setdefault(name, [])

    def insert_many(self, name: str, documents: list[dict]) -> None:
        self.collections.setdefault(name, []).extend(documents)

    def count(self, name: str) -> int:
        return len(self.collections.get(name, []))


def test_postgresql_rows_are_inserted_as_mongo_documents():
    from backend.services.pg_mongo_migration.schema import CollectionSpec

    spec = CollectionSpec("orders", columns=["id", "note"], primary_key=["id"])
    target = _Target()

    def fetch(_table, _columns, _batch, _cancel):
        yield [(1, "a"), (2, None)]

    result = run_pg_mongo_migration(
        [spec], [], scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=2,
        fetch_batches=fetch, target=target, count_source=lambda _name: 2,
        cancel=threading.Event(), migration_id="pg-mongo-1",
    )
    assert result.status == "Completed and verified"
    assert target.collections["orders"][0]["_id"] == 1
    assert target.collections["orders"][1]["note"] is None


def test_existing_mongo_collection_can_be_skipped():
    from backend.services.pg_mongo_migration.schema import CollectionSpec

    spec = CollectionSpec("orders", columns=["id"], primary_key=["id"])
    target = _Target()
    result = run_pg_mongo_migration(
        [spec], ["orders"], scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=10,
        fetch_batches=lambda *_args: iter(()), target=target, count_source=lambda _name: 0,
        cancel=threading.Event(), migration_id="pg-mongo-skip",
    )
    assert result.status == "Completed with warnings"
    assert result.outcomes[0].action == "skipped"
    assert target.collections == {}


def test_precheck_accepts_mongodb_and_builds_a_plan():
    def source_query(sql):
        text = sql.lower()
        if "version()" in text:
            return [{"version": "PostgreSQL 16", "database_name": "shop"}]
        if "pg_class" in text:
            return [{"table_name": "orders"}]
        if "information_schema.columns" in text:
            return [{"column_name": "id", "data_type": "integer", "udt_name": "int4"}]
        if "primary key" in text:
            return [{"column_name": "id"}]
        return []

    report = assess_pg_mongo(
        source_query,
        list_databases=lambda: [],
        list_collections=lambda _name: [],
        get_version=lambda: "7.0.0",
        scope=SCOPE_SCHEMA,
        selected_tables=None,
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=True,
        batch_size=100,
        statement_timeout_ms=0,
        expected_database="shop_copy",
    )
    assert report.ready
    assert report.engine == "postgresql_to_mongodb"
    assert report.dest_will_create
    assert report.tables == ["orders"]
    plan = build_pg_mongo_plan(report, scope=SCOPE_SCHEMA, strategy=STRATEGY_SKIP, batch_size=100, statement_timeout_ms=0)
    assert "PostgreSQL to MongoDB" in plan.text
    started = start_pg_mongo_migration(
        plan, confirmed=True, operator="tester",
        fetch_batches=lambda *_args: iter(()), target=_Target(),
        count_source=lambda _name: 0, cancel=threading.Event(),
    )
    assert started.status == "Completed and verified"


def test_load_collection_blocks_user_defined_types():
    def source_query(sql):
        if "information_schema.columns" in sql.lower():
            return [{"column_name": "loc", "data_type": "user-defined", "udt_name": "geometry"}]
        return []

    spec = load_collection(source_query, "places")
    assert spec.blocked
    assert "cannot be stored" in spec.blocked
