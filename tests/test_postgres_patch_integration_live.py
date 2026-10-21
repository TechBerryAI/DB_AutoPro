"""Opt-in checks against a disposable PostgreSQL database.

The normal test suite does not connect to PostgreSQL. These tests run only when
all of the following are true:

- ``DB_AUTOPRO_POSTGRES_PATCH_IT`` is exactly ``1``.
- ``DB_AUTOPRO_POSTGRES_PATCH_DSN`` is a libpq URI whose database path is exactly
  ``/db_autopro_patch_it``, with no query string. Example:
  ``postgresql://db_autopro_patch_it:<password>@127.0.0.1:5432/db_autopro_patch_it``.
- The connected database name returned by PostgreSQL is exactly ``db_autopro_patch_it``.
- The login can read ``pg_control_system()`` (membership in ``pg_monitor`` is enough).

Do not point the DSN at an application-saved server or at any other database name.
The tests create one temporary table, ``patch_it_probe``, and remove it before
disconnecting.
"""

from __future__ import annotations

import os

import pytest

_DSN = os.environ.get("DB_AUTOPRO_POSTGRES_PATCH_DSN", "")
_ENABLED = os.environ.get("DB_AUTOPRO_POSTGRES_PATCH_IT") == "1" and _DSN.endswith("/db_autopro_patch_it")


pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason=(
        "No isolated database named db_autopro_patch_it was configured. "
        "The normal suite does not connect to PostgreSQL."
    ),
)


def test_isolated_postgres_commit_rollback_and_identity():
    psycopg2 = pytest.importorskip("psycopg2")
    from backend.services.postgres_patch.admin_check import verify_after_patch
    from backend.services.postgres_patch.identity import read_database_identity
    from backend.services.postgres_patch.script_runner import PostgresScriptError, execute_statements

    connection = psycopg2.connect(_DSN)
    try:
        identity = read_database_identity(_cursor_query(connection))
        assert identity.database_name == "db_autopro_patch_it"
        assert identity.system_identifier
        assert identity.database_oid
        execute_statements(
            connection,
            "CREATE TEMP TABLE patch_it_probe (id int);\nINSERT INTO patch_it_probe VALUES (1);",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM patch_it_probe")
            assert cursor.fetchone()[0] == 1
        verification_status, verification_summary = verify_after_patch(
            _cursor_query(connection),
            "db_autopro_patch_it",
        )
        assert verification_status == "passed"
        assert "do not prove the SQL patch changed" in verification_summary
        try:
            execute_statements(connection, "INSERT INTO patch_it_probe VALUES (2);\nNOTSQL;")
        except PostgresScriptError as exc:
            assert exc.apply_result == "rolled_back"
        else:
            raise AssertionError("invalid SQL was committed")
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM patch_it_probe")
            assert cursor.fetchone()[0] == 1
            cursor.execute("DROP TABLE patch_it_probe")
        with psycopg2.connect(_DSN) as other:
            with other.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.patch_it_probe')")
                assert cursor.fetchone()[0] is None
    finally:
        connection.close()


def _cursor_query(connection):
    def query(sql: str):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    return query
