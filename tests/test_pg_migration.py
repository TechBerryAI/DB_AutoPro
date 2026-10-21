"""Migration page tests. They do not open a database."""

from __future__ import annotations

import threading

from backend.services.pg_migration.engine import build_create_table, run_migration
from backend.services.pg_migration.precheck import (
    SCOPE_DATA,
    SCOPE_SCHEMA,
    SCOPE_SCHEMA_AND_DATA,
    STRATEGY_FAIL,
    STRATEGY_REPLACE,
    STRATEGY_SKIP,
    assess_migration,
)
from backend.services.pg_migration.workflow import build_plan, start_migration
from desktop.pg_migration_flow import (
    BACKUP_ACK_TEXT,
    MigrationFormState,
    describe_server_endpoint,
    describe_verified_target,
    find_saved_server,
    live_session_matches_server,
    migration_connection_result,
    open_migration_endpoint,
    saved_server_choices,
    server_choice_label,
    outcome_counts,
    parse_batch_size,
    parse_timeout_ms,
    replace_requires_confirmation,
    same_instance_reason,
    start_block_reason,
    target_identity_mismatch,
    unsupported_migration_reason,
)


class _Audit:
    def __init__(self) -> None:
        self.rows = []

    def log(self, *args) -> None:
        self.rows.append(args)


def _query(side: str, *, tables=None, target_tables=None, same=False, version="160000", can_create=True, extensions=None, counts=None, views=None, array=False):
    tables = tables or ["orders"]
    target_tables = tables if target_tables is None else target_tables
    extensions = ["plpgsql"] if extensions is None else extensions
    counts = counts or {"orders": 2}
    views = views or []

    def query(sql: str):
        if "pg_control_system" in sql:
            if side == "source" or same:
                return [{"database_name": "app", "database_oid": "11", "system_identifier": "100"}]
            return [{"database_name": "app", "database_oid": "22", "system_identifier": "200"}]
        if "server_version_num" in sql:
            return [{"version_num": version if side == "source" else "160000"}]
        if "has_schema_privilege" in sql:
            return [{"can_create": can_create}]
        if "pg_extension" in sql:
            return [{"extname": name} for name in extensions]
        if "reltuples" in sql:
            return [{"table_name": name, "estimate": 5000} for name in tables]
        if "relkind = 'v'" in sql or "relkind = 'm'" in sql or "relkind = 'S'" in sql:
            return [{"view_name": name} for name in views] if "relkind = 'v'" in sql else []
        if "pg_indexes" in sql or "pg_constraint c" in sql or "pg_sequence" in sql or "nspname NOT IN" in sql:
            return []
        if "relkind = 'r'" in sql:
            names = tables if side == "source" else target_tables
            return [{"table_name": name} for name in names]
        if "information_schema.columns" in sql:
            data_type = "ARRAY" if array else "integer"
            return [
                {
                    "table_name": "orders",
                    "column_name": "id",
                    "data_type": data_type,
                    "udt_name": "_int4" if array else "int4",
                    "character_maximum_length": None,
                    "is_nullable": "NO",
                    "column_default": None,
                }
            ]
        if "pg_constraint" in sql:
            return [{"table_name": "orders", "definition": "PRIMARY KEY (id)"}]
        if "count(*)" in sql:
            return [{"row_count": counts.get("orders", 0)}]
        raise AssertionError(sql)

    return query


def _ready(**kwargs):
    source = _query("source", **{k: v for k, v in kwargs.items() if k in {"tables", "same", "version", "extensions", "views"}})
    target_kwargs = {k: v for k, v in kwargs.items() if k in {"tables", "target_tables", "same", "can_create", "extensions", "counts"}}
    target = _query("target", **target_kwargs)
    return assess_migration(
        source,
        target,
        scope=kwargs.get("scope", SCOPE_SCHEMA_AND_DATA),
        selected_tables=kwargs.get("selected"),
        strategy=kwargs.get("strategy", STRATEGY_SKIP),
        confirm_replace=kwargs.get("confirm_replace", False),
        accept_unproven_backup=kwargs.get("accept_backup", True),
        batch_size=kwargs.get("batch_size", 1000),
        statement_timeout_ms=kwargs.get("timeout", 0),
    )


def test_same_database_is_blocked():
    report = _ready(same=True)
    assert report.ready is False
    assert "same database" in report.stop_reason


def test_backup_file_is_not_treated_as_a_proven_restore():
    report = _ready(accept_backup=False)
    assert report.ready is False
    assert "has not been proven" in report.stop_reason


def test_schema_only_does_not_copy_rows():
    report = _ready(scope=SCOPE_SCHEMA, accept_backup=False, target_tables=[])
    assert report.ready is True
    copied = []
    result = run_migration(
        report,
        scope=SCOPE_SCHEMA,
        strategy=STRATEGY_SKIP,
        batch_size=100,
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=lambda sql: copied.append(sql),
        copy_rows=lambda *_args: (_ for _ in ()).throw(AssertionError("data copied")),
        cancel=threading.Event(),
        migration_id="m1",
    )
    assert result.status == "Completed and verified"
    assert any(sql.startswith("CREATE TABLE") for sql in copied)


def test_data_only_leaves_existing_table_in_place():
    report = _ready(scope=SCOPE_DATA, target_tables=["orders"], counts={"orders": 0})
    assert report.ready is True
    statements = []
    result = run_migration(
        report,
        scope=SCOPE_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=250,
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"], counts={"orders": 2}),
        target_execute=statements.append,
        copy_rows=lambda table, batch, _cancel: 2,
        cancel=threading.Event(),
        migration_id="m2",
    )
    assert result.status == "Completed and verified"
    assert statements == []


def test_data_only_does_not_delete_existing_rows():
    report = _ready(scope=SCOPE_DATA, target_tables=["orders"], counts={"orders": 4})
    assert report.ready is False
    assert "not deleted" in report.stop_reason


def test_existing_target_without_confirmation_is_not_dropped():
    report = _ready(target_tables=["orders"], strategy=STRATEGY_REPLACE, confirm_replace=False)
    assert report.ready is False
    assert "Nothing was dropped" in report.stop_reason


def test_conflict_skip_does_not_copy_over_target_rows():
    report = _ready(target_tables=["orders"], strategy=STRATEGY_SKIP)
    calls = []
    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=10,
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=lambda sql: None,
        copy_rows=lambda *args: calls.append(args),
        cancel=threading.Event(),
        migration_id="m3",
    )
    assert calls == []
    assert result.status == "Completed with warnings"


def test_large_copy_is_delegated_with_the_batch_size():
    report = _ready(target_tables=[])
    seen = []

    def copy_rows(table, batch, _cancel):
        seen.append((table, batch))
        return 100000

    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=1000,
        source_query=_query("source", counts={"orders": 100000}),
        target_query=_query("target", target_tables=["orders"], counts={"orders": 100000}),
        target_execute=lambda sql: None,
        copy_rows=copy_rows,
        cancel=threading.Event(),
        migration_id="m4",
    )
    assert seen == [("orders", 1000)]
    assert result.status == "Completed and verified"


def test_connection_failure_does_not_start_a_copy():
    def broken(_sql):
        raise ConnectionError("password=secret")

    report = assess_migration(
        broken,
        _query("target"),
        scope=SCOPE_SCHEMA,
        selected_tables=None,
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=False,
        batch_size=10,
        statement_timeout_ms=0,
    )
    assert report.ready is False
    assert "secret" not in report.stop_reason


def test_copy_interruption_is_partial_and_not_called_a_rollback():
    report = _ready(target_tables=[])

    def copy_rows(_table, _batch, _cancel):
        raise ConnectionError("password=secret network dropped")

    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=10,
        source_query=_query("source"),
        target_query=_query("target"),
        target_execute=lambda sql: None,
        copy_rows=copy_rows,
        cancel=threading.Event(),
        migration_id="m5",
    )
    assert result.status == "Partially completed"
    assert "not rolled back" in result.summary
    assert "secret" not in result.summary


def test_cancel_before_copy_cleans_the_run_without_a_rollback_claim():
    report = _ready(target_tables=[])
    cancel = threading.Event()
    cancel.set()
    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=10,
        source_query=_query("source"),
        target_query=_query("target"),
        target_execute=lambda sql: None,
        copy_rows=lambda *_args: 1,
        cancel=cancel,
        migration_id="m6",
    )
    assert result.status == "Cancelled"
    assert "not rolled back" in result.summary


def test_verification_mismatch_is_not_success():
    report = _ready(target_tables=[])
    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=10,
        source_query=_query("source", counts={"orders": 5}),
        target_query=_query("target", target_tables=["orders"], counts={"orders": 4}),
        target_execute=lambda sql: None,
        copy_rows=lambda *_args: 5,
        cancel=threading.Event(),
        migration_id="m7",
    )
    assert result.status == "Verification failed"


def test_start_requires_confirmation_and_records_redacted_audit(monkeypatch):
    audit = _Audit()
    monkeypatch.setattr("backend.services.pg_migration.workflow.get_audit_logger", lambda: audit)
    report = _ready(target_tables=[])
    plan = build_plan(report, scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=10, statement_timeout_ms=0)
    refused = start_migration(
        plan,
        confirmed=False,
        operator="dba",
        source_query=_query("source"),
        target_query=_query("target"),
        target_execute=lambda sql: None,
        copy_rows=lambda *_args: 2,
    )
    assert refused.status == "Failed"
    assert audit.rows == []
    done = start_migration(
        plan,
        confirmed=True,
        operator="dba",
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=lambda sql: None,
        copy_rows=lambda *_args: 2,
    )
    assert done.status == "Completed and verified"
    assert audit.rows[0][0] == "migration"
    assert "password" not in str(audit.rows)


def test_confirmed_replace_recreates_the_target_table():
    report = _ready(target_tables=["orders"], strategy=STRATEGY_REPLACE, confirm_replace=True)
    statements = []
    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_REPLACE,
        batch_size=10,
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=statements.append,
        copy_rows=lambda *_args: 2,
        cancel=threading.Event(),
        migration_id="replace",
    )
    assert any(sql.startswith("DROP TABLE") for sql in statements)
    assert any(sql.startswith("CREATE TABLE") for sql in statements)
    assert result.status == "Completed with warnings"


def test_fail_on_conflict_blocks_before_execution():
    report = _ready(target_tables=["orders"], strategy=STRATEGY_FAIL)
    assert report.ready is False
    assert "already has" in report.stop_reason


def test_plan_states_that_backup_readiness_is_not_proven():
    report = _ready(accept_backup=True)
    plan = build_plan(report, scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=10, statement_timeout_ms=0)
    assert report.backup_proven is False
    assert "not a tested restore" in plan.text
    assert "explicitly confirmed" in plan.text


def test_sequences_and_indexes_follow_dependency_order():
    from backend.services.pg_migration.engine import _sequence_ddl, _sequence_state, order_by_foreign_keys

    ordered = order_by_foreign_keys(["child", "parent"], [("child", "parent")])
    assert ordered == ["parent", "child"]
    assert order_by_foreign_keys(["a", "b"], [("a", "b"), ("b", "a")]) == ["a", "b"]
    ddl = _sequence_ddl(
        [
            {"sequence_name": "orders_id_seq", "start_value": 1, "increment_by": 1, "identity_kind": ""},
            {"sequence_name": "orders_id_seq", "start_value": 1, "increment_by": 1, "identity_kind": "d"},
        ]
    )
    assert ddl == ['CREATE SEQUENCE IF NOT EXISTS public."orders_id_seq" START 1 INCREMENT 1']
    state = _sequence_state([{"sequence_name": "orders_id_seq", "last_value": 4, "start_value": 1}])
    assert "setval" in state[0]
    assert "4" in state[0]


def test_in_progress_copy_cancellation_records_whether_rollback_was_verified():
    from backend.services.pg_migration.engine import COPY_CANCEL_LIMITATION, CopyCancelled

    assert "autocommit" in COPY_CANCEL_LIMITATION
    report = _ready(target_tables=[])

    def copy_rows(table, _batch, _cancel):
        raise CopyCancelled(table, rolled_back=True)

    result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=10,
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=lambda sql: None,
        copy_rows=copy_rows,
        cancel=threading.Event(),
        migration_id="cancel-copy",
    )
    assert result.status == "Cancelled"
    assert "rolled back in its transaction" in result.summary
    assert "Earlier committed tables were not rolled back" in result.summary

    def uncertain(table, _batch, _cancel):
        raise CopyCancelled(table, rolled_back=False)

    uncertain_result = run_migration(
        report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        batch_size=10,
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=lambda sql: None,
        copy_rows=uncertain,
        cancel=threading.Event(),
        migration_id="cancel-uncertain",
    )
    assert uncertain_result.outcomes[-1].action == "uncertain"
    assert "could not be verified as rolled back" in uncertain_result.summary


def test_partial_and_replace_audits_include_recovery(monkeypatch):
    audit = _Audit()
    monkeypatch.setattr("backend.services.pg_migration.workflow.get_audit_logger", lambda: audit)
    report = _ready(target_tables=[])
    plan = build_plan(report, scope=SCOPE_SCHEMA_AND_DATA, strategy=STRATEGY_SKIP, batch_size=10, statement_timeout_ms=0)

    def explode(_table, _batch, _cancel):
        raise ConnectionError("password=audit-secret dropped")

    partial = start_migration(
        plan,
        confirmed=True,
        operator="dba",
        source_query=_query("source"),
        target_query=_query("target"),
        target_execute=lambda sql: None,
        copy_rows=explode,
    )
    assert partial.status == "Partially completed"
    details = audit.rows[-1][4]
    assert details["backup_proven"] is False
    assert details["replace_confirmed"] is False
    assert "not rolled back" in details["recovery"]
    assert "audit-secret" not in str(audit.rows)
    replace_report = _ready(target_tables=["orders"], strategy=STRATEGY_REPLACE, confirm_replace=True)
    replace_plan = build_plan(
        replace_report,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_REPLACE,
        batch_size=10,
        statement_timeout_ms=0,
    )
    start_migration(
        replace_plan,
        confirmed=True,
        operator="dba",
        source_query=_query("source"),
        target_query=_query("target", target_tables=["orders"]),
        target_execute=lambda sql: None,
        copy_rows=lambda *_args: 2,
    )
    replaced = audit.rows[-1][4]
    assert replaced["replace_confirmed"] is True
    assert replaced["backup_proven"] is False
    assert "recovered from a backup" in replaced["recovery"]
    statement = build_create_table("orders", [{"column_name": "tags", "data_type": "ARRAY", "udt_name": "_text"}])
    assert statement is None


def test_array_column_is_reported_unsupported():
    statement = build_create_table("orders", [{"column_name": "tags", "data_type": "ARRAY", "udt_name": "_text"}])
    assert statement is None


def test_source_and_target_selection_are_independent():
    reason = start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
    )
    assert reason is None
    assert start_block_reason(
        source_type="postgresql",
        target_type="mysql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
    ) is None
    blocked = start_block_reason(
        source_type="mysql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
    )
    assert blocked
    assert "mysql-to-postgresql" in blocked
    assert "not implemented" in blocked
    assert "No schema or data was copied." in blocked
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=False,
        plan_current=False,
        confirmed=False,
        busy=False,
    )


def test_postgresql_to_mysql_api_does_not_open_a_connector(monkeypatch):
    assert unsupported_migration_reason("postgresql", "mysql") is None
    assert unsupported_migration_reason("postgresql", "postgresql") is None
    assert unsupported_migration_reason("mysql", "postgresql") is not None

    from backend.core.exceptions import MigrationError
    from backend.models.schemas import DatabaseType, MigrationRequest
    from backend.services.migration_service import MigrationService

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("connector was opened for an unsupported migration")

    monkeypatch.setattr("backend.services.migration_service.get_connector", fail_if_called)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        tables=["employees"],
    )
    try:
        MigrationService().migrate(request, user="operator")
    except MigrationError as exc:
        assert "postgresql-to-mysql" in str(exc)
        assert "No schema or data was copied." in str(exc) or "did not copy schema or data" in str(exc)
    else:
        raise AssertionError("cross-engine migration was not blocked")


def test_test_source_reuses_live_tunneled_session():
    from backend.models.schemas import DatabaseType
    from desktop.connection import ConnectionResult
    from desktop.connection_store import SavedServer

    server = SavedServer(
        name="postgres",
        db_type="postgresql",
        host="192.168.1.139",
        port=5432,
        username="postgres",
        password="secret",
        database="postgres",
        ssh_enabled=True,
        linux_username="root",
        ssh_host="192.168.1.139",
    )
    live = ConnectionResult(
        name="postgres",
        db_type=DatabaseType.POSTGRESQL,
        host="127.0.0.1",
        port=55490,
        username="postgres",
        password="secret",
        database="postgres",
        saved_name="postgres",
        ssh_enabled=True,
        linux_username="root",
        ssh_host="192.168.1.139",
        display_host="192.168.1.139",
        display_port=5432,
    )
    assert live_session_matches_server(live, server) is True
    info = migration_connection_result(server, "postgres", live).to_connection_info()
    assert info.host == "127.0.0.1"
    assert info.port == 55490
    assert info.ssl_mode == "prefer"
    assert info.database == "postgres"

    other = SavedServer(
        name="other-pg",
        db_type="postgresql",
        host="10.0.0.5",
        port=5432,
        username="postgres",
        password="secret",
        database="app",
    )
    assert live_session_matches_server(live, other) is False
    other_info = migration_connection_result(other, "app", live).to_connection_info()
    assert other_info.host == "10.0.0.5"
    assert other_info.ssl_mode == "prefer"
    assert other_info.database == "app"


def test_target_saved_servers_are_filtered_by_database_type():
    from desktop.connection_store import SavedServer
    from desktop.pg_migration_flow import servers_for_type

    servers = [
        SavedServer(name="postgres", db_type="postgresql", host="192.168.1.139", port=5432, username="postgres", password="secret", database="postgres"),
        SavedServer(name="mysql", db_type="mysql", host="192.168.1.50", port=3306, username="root", password="secret", database="app"),
        SavedServer(name="mongo", db_type="mongodb", host="192.168.1.50", port=27017, username="root", password="secret", database="admin"),
        SavedServer(name="looks-like-mysql", db_type="postgresql", host="10.0.0.9", port=5432, username="postgres", password="secret", database="app"),
    ]
    mysql_servers = servers_for_type(servers, "mysql")
    labels = saved_server_choices(mysql_servers)
    assert labels == ["mysql · mysql"]
    assert "postgres · postgresql" not in labels
    assert "looks-like-mysql · postgresql" not in labels
    assert "mongo · mongodb" not in labels
    chosen = find_saved_server(mysql_servers, "mysql · mysql")
    assert chosen is not None
    assert chosen.db_type == "mysql"
    assert chosen.host == "192.168.1.50"
    assert chosen.port == 3306
    details = describe_server_endpoint(chosen)
    assert "192.168.1.50:3306" in details
    assert "app" in details
    assert find_saved_server(mysql_servers, "postgres · postgresql") is None
    assert find_saved_server(servers, "mysql") is not None


def test_open_migration_endpoint_reuses_live_session_without_a_second_tunnel(monkeypatch):
    from backend.models.schemas import DatabaseType
    from desktop.connection import ConnectionResult
    from desktop.connection_store import SavedServer

    server = SavedServer(
        name="postgres",
        db_type="postgresql",
        host="192.168.1.139",
        port=5432,
        username="postgres",
        password="secret",
        database="postgres",
        ssh_enabled=True,
        linux_username="root",
        ssh_host="192.168.1.139",
    )
    live = ConnectionResult(
        name="postgres",
        db_type=DatabaseType.POSTGRESQL,
        host="127.0.0.1",
        port=55490,
        username="postgres",
        password="secret",
        database="postgres",
        saved_name="postgres",
        ssh_enabled=True,
        linux_username="root",
        ssh_host="192.168.1.139",
        display_host="192.168.1.139",
        display_port=5432,
    )

    def fail_tunnel(_result):
        raise AssertionError("a second SSH tunnel was opened for the live session")

    monkeypatch.setattr("desktop.ssh_tunnel.ssh_tunnel_session", fail_tunnel)
    with open_migration_endpoint(server, "postgres", live) as result:
        info = result.to_connection_info()
    assert info.host == "127.0.0.1"
    assert info.port == 55490
    assert info.ssl_mode == "prefer"


def test_typed_target_credentials_do_not_require_a_saved_server():
    from desktop.pg_migration_flow import (
        METHOD_TYPED,
        default_listen_port,
        manual_endpoint,
        typed_endpoint_error,
    )

    from desktop.pg_migration_flow import secret_entry_show

    assert default_listen_port("mysql") == 3306
    assert secret_entry_show(True) == ""
    assert secret_entry_show(False) == "*"
    assert typed_endpoint_error("", "3306", "root") == "Enter the server host."
    assert typed_endpoint_error("192.168.1.95", "3306", "root") is None
    assert typed_endpoint_error("192.168.1.95", "3306", "root", ssh_enabled=True, linux_username="") is not None
    server = manual_endpoint(
        db_type="mysql",
        host="192.168.1.95",
        port="3306",
        username="root",
        password="secret",
        ssh_enabled=True,
        linux_username="root",
        linux_password="linux",
        ssh_host="",
    )
    assert server.name == "typed"
    assert server.db_type == "mysql"
    assert server.host == "192.168.1.95"
    assert server.port == 3306
    assert server.ssh_host == "192.168.1.95"
    assert server.database == ""
    details = describe_server_endpoint(server)
    assert "Typed connection" in details
    assert "192.168.1.95:3306" in details
    assert "employees" not in details
    typed = MigrationFormState(
        "postgresql", "(none)", "postgres", "mysql", "(none)", "postgres",
        "Schema and data", "Skip existing tables", "", "1000", "0",
        target_method=METHOD_TYPED,
        target_endpoint="192.168.1.95|3306|root|ssh|root||6|1",
    )
    other = MigrationFormState(
        "postgresql", "(none)", "postgres", "mysql", "(none)", "postgres",
        "Schema and data", "Skip existing tables", "", "1000", "0",
        target_method=METHOD_TYPED,
        target_endpoint="10.0.0.8|3306|root|ssh|root||6|1",
    )
    assert typed.target_key() != other.target_key()


def test_form_changes_invalidate_previous_tests_and_plans():
    first = MigrationFormState(
        "postgresql", "postgres", "src", "postgresql", "other", "dst",
        "Schema and data", "Skip existing tables", "", "1000", "0",
    )
    changed_source = MigrationFormState(
        "postgresql", "postgres", "otherdb", "postgresql", "other", "dst",
        "Schema and data", "Skip existing tables", "", "1000", "0",
    )
    changed_policy = MigrationFormState(
        "postgresql", "postgres", "src", "postgresql", "other", "dst",
        "Schema and data", "Replace existing tables", "", "1000", "0",
    )
    assert first.source_key() != changed_source.source_key()
    assert first.target_key() == changed_source.target_key()
    assert first.plan_key() != changed_source.plan_key()
    assert first.plan_key() != changed_policy.plan_key()
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
        source_tested=False,
    ) == "Test the source connection before validating or starting."
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
        configuration_current=False,
    ) == (
        "Source or target settings changed after the last successful test or preview. "
        "Test both sides and preview the current plan again."
    )


def test_same_instance_is_rejected_by_endpoint_not_saved_name():
    from backend.models.schemas import DatabaseType
    from desktop.connection import ConnectionResult

    source = ConnectionResult(
        name="alpha",
        db_type=DatabaseType.POSTGRESQL,
        host="127.0.0.1",
        port=55490,
        username="postgres",
        password="secret",
        database="postgres",
        display_host="192.168.1.139",
        display_port=5432,
    )
    same_target = ConnectionResult(
        name="beta",
        db_type=DatabaseType.POSTGRESQL,
        host="192.168.1.139",
        port=5432,
        username="postgres",
        password="secret",
        database="postgres",
    )
    other_db = ConnectionResult(
        name="beta",
        db_type=DatabaseType.POSTGRESQL,
        host="192.168.1.139",
        port=5432,
        username="postgres",
        password="secret",
        database="app",
    )
    mysql_target = ConnectionResult(
        name="mysql",
        db_type=DatabaseType.MYSQL,
        host="192.168.1.139",
        port=3306,
        username="root",
        password="secret",
        database="postgres",
    )
    reason = same_instance_reason(source, same_target)
    assert reason is not None
    assert "same database instance" in reason
    assert "192.168.1.139:5432/postgres" in reason
    assert same_instance_reason(source, other_db) is None
    assert same_instance_reason(source, mysql_target) is None
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
        same_instance=True,
    ) == "Source and target are the same database instance. The migration was not started."


def test_target_identity_mismatch_and_stale_plan_confirmation():
    assert target_identity_mismatch("employees", "employees") is None
    mismatch = target_identity_mismatch("app", "employees")
    assert mismatch is not None
    assert "employees" in mismatch
    assert "app" in mismatch
    assert "was not started" in mismatch
    assert start_block_reason(
        source_type="postgresql",
        target_type="mysql",
        precheck_ready=True,
        plan_current=False,
        confirmed=True,
        busy=False,
    ) == "Preview the migration plan and confirm it before starting."
    assert start_block_reason(
        source_type="postgresql",
        target_type="mysql",
        precheck_ready=True,
        plan_current=True,
        confirmed=False,
        busy=False,
    ) == "Confirm the source, target, and plan before starting."
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
        identity_error=mismatch,
    ) == mismatch


def test_optional_backup_and_replace_confirmation_gates():
    assert "does not prove that a backup exists" in BACKUP_ACK_TEXT
    assert replace_requires_confirmation(STRATEGY_REPLACE, ["orders"]) is True
    assert replace_requires_confirmation(STRATEGY_REPLACE, []) is False
    assert replace_requires_confirmation(STRATEGY_SKIP, ["orders"]) is False
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
        replace_needs_confirm=True,
        replace_confirmed=False,
    ) == "Replacing existing target tables requires explicit confirmation. Nothing was dropped."
    assert start_block_reason(
        source_type="postgresql",
        target_type="postgresql",
        precheck_ready=True,
        plan_current=True,
        confirmed=True,
        busy=False,
        replace_needs_confirm=True,
        replace_confirmed=True,
    ) is None
    assert parse_batch_size("0")[1]
    assert parse_batch_size("100001")[1]
    assert parse_batch_size("1000") == (1000, None)
    assert parse_timeout_ms("-1")[1]
    assert parse_timeout_ms("0") == (0, None)
    assert describe_verified_target("postgresql", "192.168.1.139", 5432, "app", "PostgreSQL 17.10.0") == (
        "postgresql 192.168.1.139:5432 / app (PostgreSQL 17.10.0)"
    )
    counts = outcome_counts(
        [
            type("Row", (), {"action": "schema_created"})(),
            type("Row", (), {"action": "data_copied"})(),
            type("Row", (), {"action": "skipped"})(),
            type("Row", (), {"action": "replaced"})(),
            type("Row", (), {"action": "failed"})(),
        ]
    )
    assert counts == {"created": 1, "copied": 1, "skipped": 1, "failed": 1, "replaced": 1}


def test_backup_file_states_do_not_claim_a_verified_restore(tmp_path):
    from backend.services.pg_migration.backup_proof import inspect_backup_file

    missing = inspect_backup_file(tmp_path / "absent.dump")
    assert missing.file_exists is False
    assert missing.format_checked is False
    assert missing.restore_verified is False
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    empty_state = inspect_backup_file(empty)
    assert empty_state.file_exists is True
    assert empty_state.format_checked is False
    assert empty_state.restore_verified is False
    custom = tmp_path / "custom.dump"
    custom.write_bytes(b"PGDMP\x00rest")
    custom_state = inspect_backup_file(custom)
    assert custom_state.file_exists is True
    assert custom_state.format_checked is True
    assert custom_state.restore_verified is False
    assert "not a completed restore" in custom_state.format_summary
    plain = tmp_path / "plain.sql"
    plain.write_bytes(b"-- PostgreSQL database dump\n")
    assert inspect_backup_file(plain).format_checked is True
    assert inspect_backup_file(plain).restore_verified is False


def test_restore_command_success_without_matching_rows_is_not_verified(tmp_path):
    from backend.services.pg_migration.backup_proof import DISPOSABLE_RESTORE_DATABASE, verify_postgresql_restore

    class Result:
        def __init__(self) -> None:
            self.returncode = 0
            self.stderr = "password=secret-value"

    def runner(args, _env):
        if "-f" in args:
            Path_index = args.index("-f") + 1
            from pathlib import Path
            Path(args[Path_index]).write_bytes(b"PGDMP\x00ok")
        return Result()

    readiness = verify_postgresql_restore(
        source_host="127.0.0.1",
        source_port=1,
        source_user="role",
        source_password="secret-value",
        source_database="db_autopro_migration_src",
        target_host="127.0.0.1",
        target_port=2,
        target_user="role",
        target_password="secret-value",
        target_database=DISPOSABLE_RESTORE_DATABASE,
        backup_path=tmp_path / "proof.dump",
        target_is_empty=lambda: True,
        target_query=lambda _sql: [{"row_count": 0}],
        expectations=[("SELECT count(*) AS row_count FROM public.orders", "2")],
        command_runner=runner,
        pg_dump="pg_dump",
        pg_restore="pg_restore",
    )
    assert readiness.file_exists is True
    assert readiness.format_checked is True
    assert readiness.restore_verified is False
    assert "exited successfully" in readiness.restore_summary
    assert "secret-value" not in readiness.restore_summary


def test_restore_is_not_run_against_a_non_disposable_database():
    from backend.services.pg_migration.backup_proof import verify_postgresql_restore

    calls = []
    readiness = verify_postgresql_restore(
        source_host="127.0.0.1",
        source_port=1,
        source_user="role",
        source_password="secret-value",
        source_database="postgres",
        target_host="127.0.0.1",
        target_port=1,
        target_user="role",
        target_password="secret-value",
        target_database="postgres",
        backup_path=__import__("pathlib").Path("ignored.dump"),
        target_is_empty=lambda: True,
        target_query=lambda _sql: [],
        expectations=[],
        command_runner=lambda *_args: calls.append(True),
    )
    assert calls == []
    assert readiness.restore_verified is False
    assert "not been proven" in readiness.restore_summary


def test_views_and_non_public_schemas_are_explicitly_unsupported():
    report = _ready(scope=SCOPE_SCHEMA, accept_backup=False, target_tables=[], views=["customer_orders"])
    codes = {item.code: item for item in report.checks}
    assert codes["views"].status == "unsupported"
    assert "search_path" in codes["views"].summary
    assert codes["materialized_views"].status == "pass"
    assert codes["schemas"].status == "pass"
    assert report.ready is True
