"""PostgreSQL script execution without a database server."""

from backend.services.postgres_patch.script_runner import (
    PostgresScriptError,
    execute_statements,
    split_sql,
)
from backend.services.postgres_patch.sql_patch import inspect_sql_patch


class _Cursor:
    def __init__(self, connection) -> None:
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def execute(self, statement: str) -> None:
        self.connection.statements.append(statement)
        fail_on = self.connection.fail_on
        if fail_on is not None and len(self.connection.statements) == fail_on:
            raise RuntimeError("syntax error at or near 'NOTSQL' password=secret")


class _Connection:
    def __init__(self, fail_on: int | None = None) -> None:
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = fail_on
        self.autocommit = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_semicolons_inside_quotes_and_dollar_quotes_stay_in_one_statement():
    script = "SELECT 'a;b';\nDO $$ BEGIN PERFORM 1; END $$;\nSELECT 2;"
    statements = split_sql(script)

    assert len(statements) == 3
    assert "a;b" in statements[0]
    assert "PERFORM 1;" in statements[1]


def test_a_transactional_script_commits_once():
    connection = _Connection()
    execute_statements(connection, "CREATE TABLE patch_demo (id int);\nINSERT INTO patch_demo VALUES (1);")

    assert len(connection.statements) == 2
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_invalid_sql_rolls_the_transaction_back():
    connection = _Connection(fail_on=2)
    try:
        execute_statements(connection, "CREATE TABLE patch_demo (id int);\nNOTSQL;")
    except PostgresScriptError as exc:
        assert exc.apply_result == "rolled_back"
        assert exc.statement_index == 2
        assert "secret" not in str(exc)
    else:
        raise AssertionError("invalid SQL was accepted")

    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_non_transactional_sql_is_refused_until_confirmed():
    connection = _Connection()
    try:
        execute_statements(connection, "VACUUM patch_demo;")
    except PostgresScriptError as exc:
        assert exc.apply_result == "not_committed"
    else:
        raise AssertionError("VACUUM ran without confirmation")
    assert connection.statements == []


def test_a_confirmed_non_transactional_failure_reports_a_partial_apply():
    connection = _Connection(fail_on=2)
    try:
        execute_statements(
            connection,
            "VACUUM patch_demo;\nVACUUM missing_table;",
            allow_non_transactional=True,
        )
    except PostgresScriptError as exc:
        assert exc.apply_result == "partial"
        assert exc.completed_count == 1
    else:
        raise AssertionError("the second VACUUM should fail")

    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_an_explicit_commit_is_not_executed():
    connection = _Connection()
    try:
        execute_statements(connection, "SELECT 1;\nCOMMIT;")
    except PostgresScriptError as exc:
        assert exc.apply_result == "not_committed"
    else:
        raise AssertionError("COMMIT was executed")
    assert connection.statements == []


def test_vacuum_requires_an_explicit_review_flag(tmp_path):
    path = tmp_path / "vacuum.sql"
    path.write_text("VACUUM patch_demo;\n", encoding="utf-8")
    blocked = inspect_sql_patch(
        str(path),
        description="",
        target_database="postgres",
        system_identifier="1001",
        database_oid="2002",
        configured_endpoint="192.168.1.113:5432",
        completed_identities=set(),
    )
    allowed = inspect_sql_patch(
        str(path),
        description="",
        target_database="postgres",
        system_identifier="1001",
        database_oid="2002",
        configured_endpoint="192.168.1.113:5432",
        completed_identities=set(),
        allow_non_transactional=True,
    )

    assert blocked.valid is False
    assert blocked.needs_non_transactional is True
    assert allowed.valid is True
    assert allowed.allow_non_transactional is True


def test_non_transactional_statements_are_rejected_before_execution():
    from backend.services.postgres_patch.script_runner import statement_effect

    samples = {
        "CREATE INDEX CONCURRENTLY idx ON patch_demo (id)": "non_transactional",
        "DROP INDEX CONCURRENTLY idx": "non_transactional",
        "REINDEX TABLE CONCURRENTLY patch_demo": "non_transactional",
        "VACUUM patch_demo": "non_transactional",
        "CLUSTER patch_demo USING idx": "non_transactional",
        "CHECKPOINT": "non_transactional",
        "ALTER SYSTEM SET work_mem = '8MB'": "non_transactional",
        "CREATE DATABASE patch_copy": "non_transactional",
        "DROP DATABASE patch_copy": "non_transactional",
        "CREATE TABLESPACE patch_space LOCATION '/tmp/patch-space'": "non_transactional",
        "DROP TABLESPACE patch_space": "non_transactional",
        "BEGIN": "explicit_transaction",
        "START TRANSACTION": "explicit_transaction",
        "COMMIT": "explicit_transaction",
        "ROLLBACK": "explicit_transaction",
        "END": "explicit_transaction",
        "CREATE INDEX idx ON patch_demo (id)": "transactional",
    }
    for sql, effect in samples.items():
        assert statement_effect(sql) == effect, sql

    connection = _Connection()
    try:
        execute_statements(
            connection,
            "SELECT 1;\nCREATE INDEX CONCURRENTLY idx ON patch_demo (id);",
        )
    except PostgresScriptError as exc:
        assert exc.apply_result == "not_committed"
    else:
        raise AssertionError("CREATE INDEX CONCURRENTLY was allowed")
    assert connection.statements == []

