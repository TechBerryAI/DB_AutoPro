"""Opt-in copy between two disposable PostgreSQL databases.

The test stays skipped unless both DSNs end with the disposable database names.
It never reads the application's saved server file.

Required environment variables:
- DB_AUTOPRO_PG_MIGRATION_SOURCE_DSN must end with /db_autopro_migration_src
- DB_AUTOPRO_PG_MIGRATION_TARGET_DSN must end with /db_autopro_migration_dst

A second PostgreSQL server is not required by the skip rule. When both databases
are on one cluster, system identifiers match and database OIDs must still differ.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

SOURCE = os.environ.get("DB_AUTOPRO_PG_MIGRATION_SOURCE_DSN", "")
TARGET = os.environ.get("DB_AUTOPRO_PG_MIGRATION_TARGET_DSN", "")

pytestmark = pytest.mark.skipif(
    not (SOURCE.endswith("/db_autopro_migration_src") and TARGET.endswith("/db_autopro_migration_dst")),
    reason="Set disposable source and target DSNs ending in the migration test databases.",
)

_TABLES = ("migration_child", "migration_parent", "migration_probe", "migration_existing")


def _password(dsn: str) -> str:
    return unquote(urlparse(dsn).password or "")


def _connect(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn)


def _query_from(connection):
    from psycopg2.extras import RealDictCursor

    def query(sql: str):
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql)
            if cursor.description:
                return [dict(row) for row in cursor.fetchall()]
            return []

    return query


def _execute(connection, sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)
    connection.commit()


def _reset(connection) -> None:
    with connection.cursor() as cursor:
        for name in _TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS public.{name} CASCADE")
    connection.commit()


def _seed_related(source) -> None:
    _execute(
        source,
        """
        CREATE TABLE public.migration_parent (
            id integer PRIMARY KEY,
            code text NOT NULL UNIQUE,
            active boolean NOT NULL DEFAULT true,
            note text NULL
        );
        CREATE TABLE public.migration_child (
            id integer PRIMARY KEY,
            parent_id integer NOT NULL REFERENCES public.migration_parent (id)
        );
        INSERT INTO public.migration_parent (id, code, note) VALUES (1, 'a', NULL), (2, 'b', 'kept');
        INSERT INTO public.migration_child (id, parent_id) VALUES (10, 1);
        """,
    )


def _run(source, target, *, scope, strategy, tables, confirm_replace=False, accept_backup=True, copy_rows=None, cancel=None):
    from backend.services.pg_migration.engine import stream_copy_table
    from backend.services.pg_migration.precheck import assess_migration
    from backend.services.pg_migration.workflow import build_plan, start_migration

    report = assess_migration(
        _query_from(source),
        _query_from(target),
        scope=scope,
        selected_tables=tables,
        strategy=strategy,
        confirm_replace=confirm_replace,
        accept_unproven_backup=accept_backup,
        batch_size=100,
        statement_timeout_ms=0,
    )
    plan = build_plan(report, scope=scope, strategy=strategy, batch_size=100, statement_timeout_ms=0)
    if not report.ready:
        return report, plan, None

    def live_copy(table, batch, token):
        return stream_copy_table(source, target, table, batch, token)

    result = start_migration(
        plan,
        confirmed=True,
        operator="migration-it",
        source_query=_query_from(source),
        target_query=_query_from(target),
        target_execute=lambda sql: _execute(target, sql),
        copy_rows=copy_rows or live_copy,
        cancel=cancel or threading.Event(),
    )
    return report, plan, result


def _constraint_types(connection, table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.contype
            FROM pg_constraint c
            JOIN pg_class r ON r.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = r.relnamespace
            WHERE n.nspname = 'public' AND r.relname = %s
            """,
            (table,),
        )
        return {row[0] for row in cursor.fetchall()}


def _columns(connection, table: str) -> dict[str, dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, is_nullable, column_default, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return {
            row[0]: {"nullable": row[1], "default": row[2], "data_type": row[3]}
            for row in cursor.fetchall()
        }


def _count(connection, table: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM public.{table}")
        return int(cursor.fetchone()[0])


@pytest.fixture()
def databases():
    source = _connect(SOURCE)
    target = _connect(TARGET)
    _reset(source)
    _reset(target)
    try:
        yield source, target
    finally:
        for connection in (source, target):
            try:
                _reset(connection)
            except Exception:
                connection.rollback()
            connection.close()


def test_live_identities_and_preview_are_independent(databases):
    source, target = databases
    _seed_related(source)
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    report, plan, _result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
        accept_backup=False,
    )
    assert report.source_identity.database_name == "db_autopro_migration_src"
    assert report.target_identity.database_name == "db_autopro_migration_dst"
    assert report.source_identity.database_name != report.target_identity.database_name
    assert (report.source_identity.system_identifier, report.source_identity.database_oid) != (
        report.target_identity.system_identifier,
        report.target_identity.database_oid,
    )
    assert report.ready is False
    assert "has not been proven" in report.stop_reason
    assert "The migration is blocked" in plan.text
    assert _count(source, "migration_parent") == 2


def test_live_schema_only_creates_constraints_without_copying_rows(databases):
    source, target = databases
    _seed_related(source)
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA, STRATEGY_SKIP

    report, plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
        accept_backup=False,
    )
    assert report.ready is True
    assert "Confirm this source, target, and plan" in plan.text
    assert result.status == "Completed and verified"
    assert _count(target, "migration_parent") == 0
    assert _count(target, "migration_child") == 0
    assert _count(source, "migration_parent") == 2
    parent = _columns(target, "migration_parent")
    assert parent["code"]["nullable"] == "NO"
    assert parent["note"]["nullable"] == "YES"
    assert parent["active"]["default"] is not None
    assert "p" in _constraint_types(target, "migration_parent")
    assert "u" in _constraint_types(target, "migration_parent")
    assert "f" in _constraint_types(target, "migration_child")


def test_live_schema_and_data_matches_row_counts(databases):
    source, target = databases
    _seed_related(source)
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    _report, _plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
    )
    assert result.status == "Completed and verified"
    assert _count(target, "migration_parent") == 2
    assert _count(target, "migration_child") == 1
    assert _count(source, "migration_child") == 1
    with target.cursor() as cursor:
        cursor.execute("SELECT note FROM public.migration_parent WHERE id = 1")
        assert cursor.fetchone()[0] is None
        cursor.execute("SELECT active FROM public.migration_parent WHERE id = 2")
        assert cursor.fetchone()[0] is True


def test_live_data_only_fills_empty_tables_and_refuses_existing_rows(databases):
    source, target = databases
    _seed_related(source)
    from backend.services.pg_migration.precheck import SCOPE_DATA, SCOPE_SCHEMA, STRATEGY_SKIP

    _report, _plan, schema_result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
        accept_backup=False,
    )
    assert schema_result.status == "Completed and verified"
    _report, _plan, data_result = _run(
        source,
        target,
        scope=SCOPE_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
    )
    assert data_result.status == "Completed and verified"
    assert _count(target, "migration_parent") == 2
    blocked, _plan, _result = _run(
        source,
        target,
        scope=SCOPE_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent"],
    )
    assert blocked.ready is False
    assert "not deleted" in blocked.stop_reason
    assert _count(target, "migration_parent") == 2


def test_live_replace_requires_explicit_confirmation(databases):
    source, target = databases
    _execute(source, "CREATE TABLE public.migration_existing (id integer PRIMARY KEY, note text); INSERT INTO public.migration_existing VALUES (1, 'source');")
    _execute(target, "CREATE TABLE public.migration_existing (id integer PRIMARY KEY, note text); INSERT INTO public.migration_existing VALUES (9, 'keep');")
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_FAIL, STRATEGY_REPLACE

    blocked, _plan, _result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_REPLACE,
        tables=["migration_existing"],
        confirm_replace=False,
    )
    assert blocked.ready is False
    assert "Nothing was dropped" in blocked.stop_reason
    assert _count(target, "migration_existing") == 1
    refused, _plan, _result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_FAIL,
        tables=["migration_existing"],
    )
    assert refused.ready is False
    assert _count(target, "migration_existing") == 1
    _report, _plan, replaced = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_REPLACE,
        tables=["migration_existing"],
        confirm_replace=True,
    )
    assert replaced.status == "Completed with warnings"
    with target.cursor() as cursor:
        cursor.execute("SELECT note FROM public.migration_existing")
        assert cursor.fetchone()[0] == "source"


def test_live_connection_failure_is_blocked(databases):
    _source, target = databases
    import psycopg2

    from backend.services.pg_migration.precheck import SCOPE_SCHEMA, STRATEGY_SKIP, assess_migration

    with pytest.raises(psycopg2.OperationalError) as missing:
        psycopg2.connect(
            host=urlparse(SOURCE).hostname or "127.0.0.1",
            port=urlparse(SOURCE).port or 5432,
            dbname="db_autopro_migration_missing",
            user=urlparse(SOURCE).username or "postgres",
            password=_password(SOURCE),
            connect_timeout=3,
        )
    assert "db_autopro_migration_missing" in str(missing.value)

    def broken(_sql: str):
        psycopg2.connect(
            host=urlparse(SOURCE).hostname or "127.0.0.1",
            port=59999,
            dbname="db_autopro_migration_src",
            user=urlparse(SOURCE).username or "postgres",
            password=_password(SOURCE),
            connect_timeout=2,
        )

    report = assess_migration(
        broken,
        _query_from(target),
        scope=SCOPE_SCHEMA,
        selected_tables=None,
        strategy=STRATEGY_SKIP,
        confirm_replace=False,
        accept_unproven_backup=False,
        batch_size=100,
        statement_timeout_ms=0,
    )
    assert report.ready is False
    assert "db_autopro_migration_missing" not in report.stop_reason


def test_live_mid_copy_failure_is_partial_and_not_rolled_back(databases):
    source, target = databases
    _seed_related(source)
    from backend.services.pg_migration.engine import stream_copy_table
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    def copy_rows(table, batch, cancel):
        if table == "migration_child":
            raise ConnectionError("password=midcopy-secret network dropped")
        return stream_copy_table(source, target, table, batch, cancel)

    _report, _plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
        copy_rows=copy_rows,
    )
    assert result.status == "Partially completed"
    assert "not rolled back" in result.summary
    assert "midcopy-secret" not in result.summary
    assert _count(target, "migration_parent") == 2
    assert _count(source, "migration_parent") == 2


def test_live_cancel_keeps_completed_table(databases):
    source, target = databases
    _seed_related(source)
    from backend.services.pg_migration.engine import stream_copy_table
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    cancel = threading.Event()

    def copy_rows(table, batch, token):
        copied = stream_copy_table(source, target, table, batch, token)
        cancel.set()
        return copied

    _report, _plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_parent", "migration_child"],
        copy_rows=copy_rows,
        cancel=cancel,
    )
    assert result.status == "Cancelled"
    assert "not rolled back" in result.summary
    assert _count(target, "migration_parent") == 2
    assert _count(source, "migration_child") == 1
    with target.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.migration_child')")
        assert cursor.fetchone()[0] is None


def test_live_in_progress_copy_is_rolled_back_only_when_verified(databases):
    import time

    source, target = databases
    _execute(
        source,
        "CREATE TABLE public.migration_probe (id integer PRIMARY KEY); "
        "INSERT INTO public.migration_probe SELECT generate_series(1, 20);",
    )
    _execute(
        target,
        """
        CREATE TABLE public.migration_probe (id integer PRIMARY KEY);
        CREATE FUNCTION migration_pause() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_sleep(0.25);
            RETURN NEW;
        END $$;
        CREATE TRIGGER migration_pause BEFORE INSERT ON public.migration_probe
        FOR EACH ROW EXECUTE FUNCTION migration_pause();
        """,
    )
    from backend.services.pg_migration.engine import CopyCancelled, stream_copy_table

    cancel = threading.Event()

    def request_cancel() -> None:
        time.sleep(0.4)
        cancel.set()

    threading.Thread(target=request_cancel, daemon=True).start()
    try:
        with pytest.raises(CopyCancelled) as caught:
            stream_copy_table(source, target, "migration_probe", 100, cancel)
        assert caught.value.rolled_back is True
        target.rollback()
        assert _count(target, "migration_probe") == 0
        assert _count(source, "migration_probe") == 20
    finally:
        for connection in (source, target):
            try:
                connection.rollback()
                _execute(connection, "DROP FUNCTION IF EXISTS migration_pause() CASCADE")
            except Exception:
                connection.rollback()


def test_live_sequence_and_secondary_index(databases):
    source, target = databases
    _execute(
        source,
        """
        CREATE TABLE public.migration_probe (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            note text
        );
        CREATE INDEX migration_probe_note_idx ON public.migration_probe (note);
        INSERT INTO public.migration_probe (note) VALUES ('one'), ('two');
        """,
    )
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    _report, _plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_probe"],
    )
    assert result.status == "Completed and verified"
    assert _count(target, "migration_probe") == 2
    with target.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.migration_probe_note_idx')")
        assert cursor.fetchone()[0] is not None
        cursor.execute("SELECT nextval(pg_get_serial_sequence('public.migration_probe', 'id'))")
        assert int(cursor.fetchone()[0]) == 3
    assert _count(source, "migration_probe") == 2


def _endpoint(dsn: str) -> tuple[str, int | None]:
    parsed = urlparse(dsn)
    return (parsed.hostname or "", parsed.port)


@pytest.mark.skipif(
    not SOURCE or _endpoint(SOURCE) == _endpoint(TARGET),
    reason="Separate-server coverage requires source and target DSNs on different host or port values.",
)
def test_live_separate_servers_have_different_cluster_identifiers(databases):
    source, target = databases
    _execute(source, "CREATE TABLE public.migration_probe (id integer PRIMARY KEY); INSERT INTO public.migration_probe VALUES (1);")
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    report, _plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_probe"],
    )
    assert report.source_identity.system_identifier != report.target_identity.system_identifier
    assert result.status == "Completed and verified"
    assert _count(target, "migration_probe") == 1


AUTH_DSN = os.environ.get("DB_AUTOPRO_PG_MIGRATION_AUTH_DSN", "")


@pytest.mark.skipif(
    not AUTH_DSN.endswith("/db_autopro_migration_src"),
    reason="Password-authentication coverage requires DB_AUTOPRO_PG_MIGRATION_AUTH_DSN on a non-trust server.",
)
def test_live_wrong_password_is_rejected():
    import psycopg2

    parsed = urlparse(AUTH_DSN)
    marker = "wrong-auth-test"
    with pytest.raises(psycopg2.OperationalError) as refused:
        psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=parsed.path.lstrip("/"),
            user=parsed.username,
            password=marker,
            connect_timeout=3,
        )
    message = str(refused.value)
    assert "password authentication failed" in message
    assert marker not in message
    assert _password(AUTH_DSN) not in message


def test_live_restore_is_verified_only_after_a_disposable_restore(databases):
    import psycopg2

    source, _target = databases
    _execute(
        source,
        """
        CREATE TABLE public.migration_parent (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            code text NOT NULL UNIQUE,
            note text NULL
        );
        CREATE TABLE public.migration_child (
            id integer PRIMARY KEY,
            parent_id bigint NOT NULL REFERENCES public.migration_parent (id)
        );
        CREATE INDEX migration_parent_note_idx ON public.migration_parent (note);
        INSERT INTO public.migration_parent (code, note) VALUES ('a', NULL), ('b', 'kept');
        INSERT INTO public.migration_child (id, parent_id) VALUES (10, 1);
        """,
    )
    from backend.services.pg_migration.backup_proof import inspect_backup_file, verify_postgresql_restore

    parsed = urlparse(TARGET)
    password = _password(TARGET)
    admin = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname="postgres",
        user=parsed.username,
        password=password,
        connect_timeout=10,
    )
    admin.autocommit = True
    restore = None
    dump_path = Path(os.environ.get("TEMP", ".")) / "db_autopro_migration_restore.dump"
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'db_autopro_migration_restore' AND pid <> pg_backend_pid()"
            )
            cursor.execute("DROP DATABASE IF EXISTS db_autopro_migration_restore")
            cursor.execute("CREATE DATABASE db_autopro_migration_restore")
        assert inspect_backup_file(dump_path).restore_verified is False
        restore = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname="db_autopro_migration_restore",
            user=parsed.username,
            password=password,
            connect_timeout=10,
        )
        source_parsed = urlparse(SOURCE)
        readiness = verify_postgresql_restore(
            source_host=source_parsed.hostname,
            source_port=source_parsed.port,
            source_user=source_parsed.username,
            source_password=_password(SOURCE),
            source_database=source_parsed.path.lstrip("/"),
            target_host=parsed.hostname,
            target_port=parsed.port,
            target_user=parsed.username,
            target_password=password,
            target_database="db_autopro_migration_restore",
            backup_path=dump_path,
            target_is_empty=lambda: not _query_from(restore)(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' LIMIT 1"
            ),
            target_query=_query_from(restore),
            expectations=[
                ("SELECT count(*)::text AS value FROM public.migration_parent", "2"),
                ("SELECT count(*)::text AS value FROM public.migration_child", "1"),
                ("SELECT count(*)::text AS value FROM public.migration_parent WHERE note IS NULL", "1"),
                ("SELECT count(*)::text AS value FROM pg_constraint WHERE contype = 'f'", "1"),
                ("SELECT count(*)::text AS value FROM pg_indexes WHERE indexname = 'migration_parent_note_idx'", "1"),
            ],
        )
        assert readiness.file_exists is True
        assert readiness.format_checked is True
        assert readiness.restore_verified is True
        assert password not in readiness.restore_summary
        assert _password(SOURCE) not in readiness.restore_summary
        assert _count(restore, "migration_parent") == 2
        with restore.cursor() as cursor:
            cursor.execute("SELECT code FROM public.migration_parent WHERE id = 2")
            assert cursor.fetchone()[0] == "b"
            cursor.execute("SELECT nextval(pg_get_serial_sequence('public.migration_parent', 'id'))")
            assert int(cursor.fetchone()[0]) == 3
        assert _count(source, "migration_parent") == 2
    finally:
        if restore is not None:
            restore.close()
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = 'db_autopro_migration_restore' AND pid <> pg_backend_pid()"
                )
                cursor.execute("DROP DATABASE IF EXISTS db_autopro_migration_restore")
                cursor.execute("SELECT count(*) FROM pg_database WHERE datname = 'db_autopro_migration_restore'")
                assert cursor.fetchone()[0] == 0
        finally:
            admin.close()
        dump_path.unlink(missing_ok=True)


def test_live_audit_does_not_record_the_dsn_password(databases):
    source, target = databases
    _execute(source, "CREATE TABLE public.migration_probe (id integer PRIMARY KEY, note text); INSERT INTO public.migration_probe VALUES (1, 'ok');")
    from backend.core.config import get_settings
    from backend.services.pg_migration.precheck import SCOPE_SCHEMA_AND_DATA, STRATEGY_SKIP

    audit_path = get_settings().logging.audit_log_path
    before = os.path.getsize(audit_path) if os.path.exists(audit_path) else 0
    _report, _plan, result = _run(
        source,
        target,
        scope=SCOPE_SCHEMA_AND_DATA,
        strategy=STRATEGY_SKIP,
        tables=["migration_probe"],
    )
    assert result.status == "Completed and verified"
    with open(audit_path, encoding="utf-8") as handle:
        handle.seek(before)
        added = handle.read()
    secret = _password(SOURCE)
    assert secret
    assert secret not in added
    assert "db_autopro_migration_src" in added
    assert "db_autopro_migration_dst" in added
    assert "backup_proven" in added
    assert "not a restore test" in added or "not proven" in added
