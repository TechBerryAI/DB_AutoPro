"""PostgreSQL administrator patch checks. No database connection."""

from __future__ import annotations

from types import SimpleNamespace

from backend.services.postgres_patch.admin_check import CheckStatus, assess_administrator, format_report
from desktop.app import CONNECTION_REQUIRED_KEYS, PATCH_SUBNAV
from desktop.views.patch_view import uses_postgres_patch_workflow


def _query(
    role=True,
    recovery=False,
    sessions=0,
    prepared=0,
    slots=0,
    restart=0,
    version="18.1",
    database="postgres",
    blocking=0,
    replicas=0,
    not_streaming=0,
    system_identifier="1001",
    database_oid="2002",
):
    def query(sql: str):
        if "pg_roles" in sql:
            return [
                {
                    "login_role": "postgres",
                    "is_superuser": role,
                    "server_version": version,
                    "database_name": database,
                    "in_recovery": recovery,
                }
            ]
        if "pg_stat_activity" in sql:
            return [{"other_sessions": sessions}]
        if "pg_prepared_xacts" in sql:
            return [{"prepared": prepared}]
        if "pg_locks" in sql:
            return [{"blocking_locks": blocking}]
        if "pg_replication_slots" in sql:
            return [{"inactive_slots": slots}]
        if "pg_stat_replication" in sql:
            return [{"replicas": replicas, "not_streaming": not_streaming}]
        if "pg_settings" in sql:
            return [{"pending_restart": restart}]
        if "pg_control_system" in sql:
            return [
                {
                    "database_name": database,
                    "database_oid": database_oid,
                    "system_identifier": system_identifier,
                }
            ]
        if " AS ok" in sql:
            return [{"database_name": database, "in_recovery": recovery, "ok": 1}]
        if "current_database()" in sql:
            return [{"database_name": database}]
        raise AssertionError(sql)

    return query


def test_superuser_on_the_primary_can_run_a_sql_patch():
    report = assess_administrator(_query())

    assert report.is_administrator is True
    assert report.ready_to_patch is True
    text = format_report(report)
    assert "[pass] administrator: Role postgres is a database administrator" in text
    assert "[not_applicable] backup:" in text
    assert "not proven" in text
    assert "verified" not in text
    assert "SQL patch execution stays closed." not in text


def test_a_role_that_is_not_a_superuser_cannot_patch():
    report = assess_administrator(_query(role=False))

    assert report.ready_to_patch is False
    codes = [item.code for item in report.checks]
    assert "prepared_transactions" not in codes
    assert "replication_slots" not in codes
    failed = next(item for item in report.checks if item.code == "administrator")
    assert failed.status is CheckStatus.FAIL
    assert "not a database administrator" in failed.summary


def test_a_standby_blocks_the_sql_patch():
    report = assess_administrator(_query(recovery=True))

    assert report.is_administrator is True
    assert report.ready_to_patch is False
    recovery = next(item for item in report.checks if item.code == "recovery")
    assert recovery.status is CheckStatus.FAIL


def test_open_sessions_are_a_warning_and_do_not_block():
    report = assess_administrator(_query(sessions=2, restart=1))

    assert report.ready_to_patch is True
    sessions = next(item for item in report.checks if item.code == "sessions")
    restart = next(item for item in report.checks if item.code == "pending_restart")
    assert sessions.status is CheckStatus.WARNING
    assert "2 other session(s)" in sessions.summary
    assert restart.status is CheckStatus.WARNING


def test_an_unreadable_role_fails_closed_without_keeping_a_password():
    def query(_sql: str):
        raise RuntimeError("password=secret failed")

    report = assess_administrator(query)
    assert report.ready_to_patch is False
    assert "secret" not in report.checks[0].summary
    assert "[REDACTED]" in report.checks[0].summary


def test_administration_page_is_not_in_the_patching_sidebar():
    assert "postgres_patch" not in CONNECTION_REQUIRED_KEYS
    assert [key for key, _label, _cls in PATCH_SUBNAV] == ["mysql_patch"]


def test_patching_page_uses_postgres_workflow_only_for_postgresql():
    postgres = SimpleNamespace(db_type=SimpleNamespace(value="postgresql"))
    mysql = SimpleNamespace(db_type=SimpleNamespace(value="mysql"))
    assert uses_postgres_patch_workflow(postgres) is True
    assert uses_postgres_patch_workflow(mysql) is False
    assert uses_postgres_patch_workflow(None) is False


def test_blocking_locks_stop_the_patch():
    report = assess_administrator(_query(blocking=2))

    assert report.ready_to_patch is False
    locks = next(item for item in report.checks if item.code == "blocking_locks")
    assert locks.status is CheckStatus.FAIL


def test_an_unreadable_session_count_is_a_warning():
    def query(sql: str):
        if "pg_roles" in sql:
            return _query()(sql)
        if "pg_stat_activity" in sql:
            raise RuntimeError("permission denied")
        return _query()(sql)

    report = assess_administrator(query)
    sessions = next(item for item in report.checks if item.code == "sessions")
    assert sessions.status is CheckStatus.WARNING
    assert report.ready_to_patch is True
