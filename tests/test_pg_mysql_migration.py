"""PostgreSQL-to-MySQL migration tests. They do not open a database."""

from __future__ import annotations

import threading
import time

from backend.services.pg_migration.precheck import SCOPE_DATA, SCOPE_SCHEMA, SCOPE_SCHEMA_AND_DATA, STRATEGY_FAIL, STRATEGY_REPLACE, STRATEGY_SKIP
from backend.services.pg_mysql_migration.engine import run_pg_mysql_migration
from backend.services.pg_mysql_migration.mapping import adapt_value, map_column, quote_mysql, translate_default
from backend.services.pg_mysql_migration.precheck import assess_pg_mysql
from backend.services.pg_mysql_migration.schema import ColumnSpec, ForeignKey, TableSpec, create_table_sql, insert_sql
from backend.services.pg_mysql_migration.workflow import build_pg_mysql_plan, start_pg_mysql_migration


def test_cancel_before_the_first_table_runs_no_sql():
    cancel = threading.Event()
    cancel.set()
    calls = []

    class Target:
        def execute(self, sql, _params=None):
            calls.append(sql)

        def executemany(self, sql, rows):
            calls.append(("insert", len(list(rows))))

        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

    spec = TableSpec("orders", columns=[ColumnSpec("id", "INT", False, None, True)], primary_key=["id"])
    result = run_pg_mysql_migration(
        [spec],
        [],
        scope=SCOPE_SCHEMA,
        strategy=STRATEGY_SKIP,
        batch_size=1,
        fetch_batches=lambda *_args: iter(()),
        target=Target(),
        count_source=lambda _name: 0,
        cancel=cancel,
        migration_id="before",
    )
    assert result.status == "Cancelled"
    assert calls == []
    assert "not rolled back" in result.summary


def test_cancel_between_batches_rolls_back_only_the_open_insert():
    cancel = threading.Event()
    inserts = []
    events = []

    def fetch(_table, _columns, _batch_size, _cancel):
        yield [(1,)]
        cancel.set()
        yield [(2,)]

    class Target:
        def execute(self, sql, _params=None):
            events.append(sql)

        def executemany(self, sql, rows):
            inserts.append(list(rows))

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    spec = TableSpec("orders", columns=[ColumnSpec("id", "INT", False, None, True)], primary_key=["id"])
    result = run_pg_mysql_migration(
        [spec],
        [],
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=1,
        fetch_batches=fetch,
        target=Target(),
        count_source=lambda _name: 2,
        cancel=cancel,
        migration_id="batches",
    )
    assert result.status == "Cancelled"
    assert inserts == [[(1,)]]
    assert "rollback" in events
    assert "commit" not in events
    assert "not rolled back" in result.summary


def test_cancel_does_not_interrupt_a_statement_already_running():
    cancel = threading.Event()
    finished = threading.Event()
    started = threading.Event()
    statements = []

    class Target:
        def execute(self, sql, _params=None):
            statements.append(sql)
            if sql.startswith("CREATE TABLE"):
                started.set()
                while not cancel.is_set():
                    time.sleep(0.01)
                time.sleep(0.05)
                finished.set()

        def executemany(self, sql, rows):
            statements.append("insert")

        def commit(self):
            statements.append("commit")

        def rollback(self):
            statements.append("rollback")

    def request_cancel():
        assert started.wait(5)
        cancel.set()

    threading.Thread(target=request_cancel).start()
    spec = TableSpec("orders", columns=[ColumnSpec("id", "INT", False, None, True)], primary_key=["id"])
    other = TableSpec("lines", columns=[ColumnSpec("id", "INT", False, None, True)], primary_key=["id"])
    result = run_pg_mysql_migration(
        [spec, other],
        [],
        scope=SCOPE_SCHEMA,
        strategy=STRATEGY_SKIP,
        batch_size=1,
        fetch_batches=lambda *_args: iter(()),
        target=Target(),
        count_source=lambda _name: 0,
        cancel=cancel,
        migration_id="inflight",
    )
    assert finished.is_set()
    assert result.status == "Cancelled"
    assert sum(sql.startswith("CREATE TABLE") for sql in statements if isinstance(sql, str)) == 1
    assert "not rolled back" in result.summary


def test_type_mapping_preserves_nulls_and_reports_unmapped_types():
    mysql_type, lossy, error = map_column({"data_type": "integer", "udt_name": "int4", "is_nullable": "YES"})
    assert mysql_type == "INT" and error is None and lossy is None
    varchar, note, error = map_column({"data_type": "character varying", "character_maximum_length": 20})
    assert varchar == "VARCHAR(20)" and note is None and error is None
    boolean, lossy, error = map_column({"data_type": "boolean"})
    assert boolean == "TINYINT(1)" and "TINYINT(1)" in lossy and error is None
    zoned, lossy, error = map_column({"data_type": "timestamp with time zone"})
    assert zoned == "DATETIME(6)" and "time zone" in lossy
    unbounded, _note, error = map_column({"data_type": "numeric"})
    assert unbounded is None and "without precision" in error
    array, _note, error = map_column({"data_type": "ARRAY", "udt_name": "_int4"})
    assert array is None and "cannot be mapped" in error
    assert adapt_value(None, "INT") is None
    assert adapt_value(True, "TINYINT(1)") == 1
    default_sql, default_error = translate_default("'abc'::text", mysql_type="TEXT", auto_increment=False)
    assert default_sql == "'abc'" and default_error is None
    _ignored, default_error = translate_default("now() + interval '1 day'", mysql_type="DATETIME(6)", auto_increment=False)
    assert "cannot be translated" in default_error


def test_mysql_identifiers_are_quoted_and_cannot_inject_sql():
    assert quote_mysql("orders") == "`orders`"
    assert quote_mysql("ord`ers") == "`ord``ers`"
    try:
        quote_mysql("orders\x00")
    except ValueError:
        pass
    else:
        raise AssertionError("null byte was accepted")
    table = TableSpec(
        "orders",
        columns=[ColumnSpec("id", "INT", False, None, True), ColumnSpec("note", "VARCHAR(20)", True, "'a''b'", False)],
        primary_key=["id"],
    )
    sql = create_table_sql(table)
    assert "CREATE TABLE `orders`" in sql
    assert "AUTO_INCREMENT" in sql
    assert "DEFAULT 'a''b'" in sql
    assert "COPY " not in sql
    assert insert_sql(table) == "INSERT INTO `orders` (`id`, `note`) VALUES (%s, %s)"


def _column(name="id", data_type="integer", **extra):
    row = {
        "column_name": name,
        "data_type": data_type,
        "udt_name": "int4",
        "character_maximum_length": None,
        "numeric_precision": None,
        "numeric_scale": None,
        "is_nullable": "NO",
        "column_default": None,
        "identity_generation": "BY DEFAULT" if name == "id" else "",
    }
    row.update(extra)
    return row


def _source(sql: str):
    if sql.startswith("SELECT version()"):
        return [{"version": "PostgreSQL 16.1", "database_name": "postgres"}]
    if "relkind = 'r'" in sql:
        return [{"table_name": "orders"}, {"table_name": "lines"}]
    if "information_schema.columns" in sql and "orders" in sql:
        return [
            _column(),
            _column("note", "character varying", udt_name="varchar", character_maximum_length=20, is_nullable="YES", identity_generation=""),
        ]
    if "information_schema.columns" in sql and "lines" in sql:
        return [
            _column(),
            _column("order_id", "integer", is_nullable="NO", identity_generation=""),
        ]
    if "PRIMARY KEY" in sql:
        return [{"constraint_name": "pkey", "column_name": "id", "ordinal_position": 1}]
    if "UNIQUE" in sql:
        return []
    if "indexprs IS NOT NULL" in sql and "attname" not in sql:
        return []
    if "pg_index" in sql and "orders" in sql:
        return [{"index_name": "orders_note_idx", "column_name": "note", "indisunique": False, "expression_index": False, "ordinality": 1}]
    if "pg_index" in sql:
        return []
    if "FOREIGN KEY" in sql and "lines" in sql:
        return [{
            "constraint_name": "lines_order_fk",
            "column_name": "order_id",
            "ordinal_position": 1,
            "foreign_table": "orders",
            "foreign_column": "id",
            "delete_rule": "CASCADE",
            "update_rule": "NO ACTION",
        }]
    if "FOREIGN KEY" in sql:
        return []
    if "relkind = 'v'" in sql or "relkind = 'm'" in sql or "pg_namespace" in sql:
        return []
    if "db_autopro_inventory" in sql:
        return []
    raise AssertionError(sql)


def _target(sql: str, *, existing=None, counts=None, databases=None, database_name="app"):
    existing = existing or []
    counts = counts or {}
    databases = ["app"] if databases is None else databases
    if "version_comment" in sql:
        return [{"version": "8.0.36", "version_comment": "MySQL Community Server", "database_name": database_name, "server_uuid": "uuid-1"}]
    if sql.startswith("SHOW DATABASES"):
        return [{"Database": name} for name in databases]
    if sql.startswith("SHOW GRANTS"):
        return [{"grant": "GRANT ALL PRIVILEGES ON *.* TO `app`@`%`"}]
    if "information_schema.tables" in sql:
        return [{"table_name": name} for name in existing]
    if sql.startswith("SELECT COUNT"):
        name = sql.split("`")[1]
        return [{"row_count": counts.get(name, 0)}]
    raise AssertionError(sql)


def _assess(**kwargs):
    defaults = dict(
        scope=SCOPE_SCHEMA_AND_DATA,
        selected_tables=["orders", "lines"],
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=True,
        batch_size=2,
        statement_timeout_ms=0,
    )
    defaults.update(kwargs)
    existing = defaults.pop("existing", [])
    counts = defaults.pop("counts", {})
    databases = defaults.pop("databases", None)
    database_name = defaults.pop("database_name", "app")
    return assess_pg_mysql(
        _source,
        lambda sql: _target(sql, existing=existing, counts=counts, databases=databases, database_name=database_name),
        **defaults,
    )


def test_missing_mysql_destination_is_planned_as_a_source_copy():
    report = _assess(
        expected_database="postgres",
        database_name="",
        databases=["mysql", "app"],
        existing=[],
    )
    assert report.ready, report.stop_reason
    assert report.dest_will_create is True
    assert report.target_database == "postgres"
    plan = build_pg_mysql_plan(report, scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=2, statement_timeout_ms=0)
    assert "create MySQL database postgres" in plan.text
    assert "Users, roles, files, views, and routines are not copied." in plan.text
    data_only = _assess(
        scope=SCOPE_DATA,
        expected_database="postgres",
        database_name="",
        databases=["mysql"],
        existing=[],
    )
    assert not data_only.ready
    assert "does not exist" in data_only.stop_reason


def test_preview_lists_unsupported_objects_before_execution():
    def source(sql):
        if "db_autopro_inventory" in sql and "relkind IN" in sql:
            return [
                {"object_kind": "v", "object_name": "order_view"},
                {"object_kind": "m", "object_name": "order_summary"},
                {"object_kind": "f", "object_name": "remote_orders"},
                {"object_kind": "p", "object_name": "orders_partitioned"},
                {"object_kind": "S", "object_name": "standalone_seq"},
            ]
        if "db_autopro_inventory" in sql and "pg_proc" in sql:
            return [{"object_name": "total_qty", "object_kind": "f"}]
        if "db_autopro_inventory" in sql and "pg_trigger" in sql:
            return [{"object_name": "audit_row", "table_name": "orders"}]
        if "db_autopro_inventory" in sql and "pg_policy" in sql:
            return [{"object_name": "reader", "table_name": "orders"}]
        if "db_autopro_inventory" in sql and "pg_type" in sql:
            return [{"object_name": "mood", "object_kind": "e"}, {"object_name": "label", "object_kind": "d"}]
        if "db_autopro_inventory" in sql and "pg_extension" in sql:
            return [{"object_name": "postgis"}]
        if "schema_name" in sql:
            return [{"schema_name": "sales"}]
        return _source(sql)

    report = assess_pg_mysql(
        source,
        lambda sql: _target(sql),
        scope=SCOPE_SCHEMA,
        selected_tables=["orders"],
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=False,
        batch_size=1,
        statement_timeout_ms=0,
    )
    assert report.ready, report.stop_reason
    plan = build_pg_mysql_plan(report, scope=SCOPE_SCHEMA, strategy=STRATEGY_SKIP, batch_size=1, statement_timeout_ms=0)
    for item in (
        "view order_view",
        "materialized view order_summary",
        "foreign table remote_orders",
        "partitioned table orders_partitioned",
        "sequence standalone_seq",
        "function total_qty",
        "trigger audit_row",
        "policy reader",
        "enum mood",
        "domain label",
        "extension postgis",
        "schema sales",
    ):
        assert item in plan.text
    assert "Not migrated:" in plan.text


def test_backup_creation_validation_and_restore_stay_separate(tmp_path):
    from backend.services.pg_migration.backup_proof import inspect_backup_file

    dump = tmp_path / "orders.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 20)
    readiness = inspect_backup_file(dump)
    assert readiness.file_exists is True
    assert readiness.format_checked is True
    assert readiness.restore_verified is False
    report = _assess(scope=SCOPE_SCHEMA, backup=readiness)
    assert report.backup_proven is False
    plan = build_pg_mysql_plan(report, scope=SCOPE_SCHEMA, strategy=STRATEGY_SKIP, batch_size=2, statement_timeout_ms=0)
    assert "Backup file exists: True" in plan.text
    assert "Backup format checked: True" in plan.text
    assert "Restore verified: False" in plan.text
    assert "Migration completion, backup creation, backup validation, and restore verification are separate." in plan.text


def test_plan_orders_foreign_keys_and_states_mysql_restore_is_unproven():
    report = _assess()
    assert report.ready
    assert report.tables == ["orders", "lines"]
    assert report.backup_proven is False
    plan = build_pg_mysql_plan(report, scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=2, statement_timeout_ms=0)
    assert "not a tested MySQL restore" in plan.text
    assert "not PostgreSQL COPY" in plan.text
    assert any("AUTO_INCREMENT" in item for item in report.conversions)
    assert "orders_note_idx" in [name for spec in report.table_specs for name, _columns in spec.indexes]


def test_conflict_policies_do_not_drop_without_confirmation():
    blocked = _assess(existing=["orders"], strategy=STRATEGY_REPLACE, confirm_replace=False)
    assert not blocked.ready
    assert "Nothing was dropped" in blocked.stop_reason
    failed = _assess(existing=["orders"], strategy=STRATEGY_FAIL)
    assert not failed.ready
    skipped = _assess(existing=["orders"], strategy=STRATEGY_SKIP)
    assert skipped.ready and skipped.conflicts == ["orders"]
    data_only = _assess(scope=SCOPE_DATA, existing=["orders", "lines"], counts={"orders": 1, "lines": 0})
    assert not data_only.ready
    assert "not deleted" in data_only.stop_reason
    empty = _assess(scope=SCOPE_DATA, existing=["orders", "lines"], counts={"orders": 0, "lines": 0})
    assert empty.ready


def test_unsupported_column_blocks_before_execution():
    def source(sql: str):
        if "information_schema.columns" in sql and "orders" in sql:
            return [_column(), _column("tags", "ARRAY", udt_name="_text", identity_generation="")]
        return _source(sql)

    report = assess_pg_mysql(
        source,
        lambda sql: _target(sql),
        scope=SCOPE_SCHEMA,
        selected_tables=["orders"],
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=False,
        batch_size=10,
        statement_timeout_ms=0,
    )
    assert not report.ready
    assert "was not mapped" in report.stop_reason


class _Target:
    def __init__(self, fail_sql=None):
        self.statements = []
        self.batches = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_sql = fail_sql

    def execute(self, sql, params=None):
        if self.fail_sql and self.fail_sql in sql:
            raise RuntimeError("password=secret write failed")
        self.statements.append(sql)

    def executemany(self, sql, rows):
        self.batches.append(list(rows))

    def query(self, sql, params=None):
        if "MAX(" in sql:
            return [{"max_id": 2}]
        if "COUNT(" in sql:
            return [{"row_count": sum(len(batch) for batch in self.batches)}]
        return []

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _orders():
    return TableSpec(
        "orders",
        columns=[
            ColumnSpec("id", "INT", False, None, True),
            ColumnSpec("note", "VARCHAR(20)", True, None, False),
        ],
        primary_key=["id"],
        indexes=[("orders_note_idx", ("note",))],
        foreign_keys=[],
    )


def test_schema_and_data_inserts_nulls_without_copy():
    target = _Target()
    cancel = threading.Event()

    def fetch(_table, _columns, _batch, _cancel):
        yield [(1, None), (2, "kept")]

    result = run_pg_mysql_migration(
        [_orders()],
        [],
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=2,
        fetch_batches=fetch,
        target=target,
        count_source=lambda _name: 2,
        cancel=cancel,
        migration_id="mig-1",
    )
    assert result.status == "Completed and verified"
    assert target.batches == [[(1, None), (2, "kept")]]
    assert all("COPY " not in sql and "pg_" not in sql for sql in target.statements)
    assert any(sql.startswith("CREATE TABLE `orders`") for sql in target.statements)
    assert any("AUTO_INCREMENT = 3" in sql for sql in target.statements)
    assert any("CREATE INDEX `orders_note_idx`" in sql for sql in target.statements)


def test_schema_only_does_not_insert():
    target = _Target()
    result = run_pg_mysql_migration(
        [_orders()],
        [],
        scope=SCOPE_SCHEMA,
        strategy=STRATEGY_SKIP,
        batch_size=2,
        fetch_batches=lambda *_args: (_ for _ in ()),
        target=target,
        count_source=lambda _name: 5,
        cancel=threading.Event(),
        migration_id="mig-2",
    )
    assert result.status == "Completed and verified"
    assert target.batches == []
    assert "No rows were copied" in result.summary


def test_partial_failure_does_not_claim_rollback_and_redacts_the_secret():
    parent = _orders()
    child = TableSpec("lines", columns=[ColumnSpec("id", "INT", False, None, True)], primary_key=["id"], foreign_keys=[
        ForeignKey("lines_order_fk", ("order_id",), "orders", ("id",), "CASCADE", "NO ACTION")
    ])
    # The child column list does not include order_id; keep the foreign key name for the failure path on CREATE.
    target = _Target(fail_sql="CREATE TABLE `lines`")

    def fetch(table, _columns, _batch, _cancel):
        if table == "orders":
            yield [(1, None)]

    result = run_pg_mysql_migration(
        [parent, child],
        [],
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=1,
        fetch_batches=fetch,
        target=target,
        count_source=lambda _name: 1,
        cancel=threading.Event(),
        migration_id="mig-3",
    )
    assert result.status == "Partially completed"
    assert "not rolled back" in result.summary
    assert "secret" not in result.summary
    assert "[REDACTED]" in result.summary


def test_cancel_during_insert_rolls_back_only_the_open_table():
    target = _Target()
    cancel = threading.Event()

    def fetch(_table, _columns, _batch, _cancel):
        yield [(1, "a")]
        cancel.set()
        yield [(2, "b")]

    result = run_pg_mysql_migration(
        [_orders()],
        [],
        scope=SCOPE_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=1,
        fetch_batches=fetch,
        target=target,
        count_source=lambda _name: 2,
        cancel=cancel,
        migration_id="mig-4",
    )
    assert result.status == "Cancelled"
    assert target.rollbacks == 1
    assert target.commits == 0
    assert "not rolled back" in result.summary


def test_replace_is_explicit_in_the_audit_and_backup_is_not_marked_proven(monkeypatch):
    report = _assess(existing=["orders"], strategy=STRATEGY_REPLACE, confirm_replace=True, selected_tables=["orders"])
    assert report.ready
    plan = build_pg_mysql_plan(report, scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_REPLACE, batch_size=2, statement_timeout_ms=0)
    rows = []
    monkeypatch.setattr("backend.services.pg_mysql_migration.workflow.get_audit_logger", lambda: type("A", (), {"log": lambda self, *args: rows.append(args)})())
    target = _Target()
    result = start_pg_mysql_migration(
        plan,
        confirmed=True,
        operator="dba",
        fetch_batches=lambda *_args: iter([[(1, None)]]),
        target=target,
        count_source=lambda _name: 1,
        cancel=threading.Event(),
    )
    assert result.status == "Completed with warnings"
    assert any(sql.startswith("DROP TABLE `orders`") for sql in target.statements)
    details = rows[-1][4]
    assert details["backup_proven"] is False
    assert details["restore_verified"] is False
    assert details["replace_confirmed"] is True
    assert "password" not in str(details)
