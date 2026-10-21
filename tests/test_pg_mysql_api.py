"""PostgreSQL-to-MySQL API tests. They do not open a real database."""

from __future__ import annotations

import threading
import time

import pytest

from backend.models.schemas import DatabaseType, MigrationRequest
from tests.test_pg_mysql_migration import _source, _target


@pytest.fixture(autouse=True)
def isolated_migration_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.services.pg_mysql_migration.operation_store.STORE_PATH",
        tmp_path / "migration_operations.sqlite",
    )


class _Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.itersize = 1

    def execute(self, _sql, _params=None):
        return None

    def fetchmany(self, size):
        batch, self.rows = self.rows[:size], self.rows[size:]
        return batch

    def close(self):
        return None


class _Connection:
    def __init__(self):
        self.autocommit = True
        self.statements = []

    def cursor(self, name=None, dictionary=False):
        self.dictionary = dictionary
        return self

    def execute(self, sql, _params=None):
        self.statements.append(sql)

    def executemany(self, sql, rows):
        self.statements.append(sql)
        self.rows = list(rows)

    def fetchall(self):
        if any("MAX(" in sql for sql in self.statements):
            return [{"max_id": 1}]
        return [{"row_count": 0}]

    def close(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None


class _Connector:
    def __init__(self, query):
        self.query = query
        self.connection = _Connection()

    def execute_query(self, sql, params=None):
        if sql.startswith("SELECT count(*)"):
            return [{"row_count": 0}]
        return self.query(sql)

    def open_connection(self):
        return self.connection

    def test_connection(self):
        return True


def _factory(source_query, target_query):
    def factory(db_type, _profile):
        if db_type == DatabaseType.POSTGRESQL:
            return _Connector(source_query)
        return _Connector(target_query)

    return factory


def _body(**extra):
    body = {
        "source_type": "postgresql",
        "target_type": "mysql",
        "scope": "schema_and_data",
        "strategy": "skip_existing",
        "source_database": "postgres",
        "target_database": "app",
        "tables": ["orders"],
        "batch_size": 10,
    }
    body.update(extra)
    return body


def test_missing_confirmation_does_not_open_a_connector(client, auth_headers):
    opened = []

    def factory(*_args):
        opened.append(True)
        raise AssertionError("connector opened")

    client.app.dependency_overrides.clear()
    from backend.services.migration_service import MigrationService
    from api.dependencies import get_migration_service

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(factory)
    response = client.post("/api/v1/migrations/", json=_body(), headers=auth_headers)
    assert response.status_code == 400
    assert "No schema or data was copied." in response.text
    assert opened == []
    client.app.dependency_overrides.clear()


def test_viewer_cannot_preview_or_execute(client):
    login = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "viewer123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.post("/api/v1/migrations/preview", json=_body(), headers=headers).status_code == 403
    assert client.post("/api/v1/migrations/", json=_body(confirm_plan=True), headers=headers).status_code == 403


def test_unsupported_pair_is_rejected_before_a_connector(client, auth_headers):
    opened = []

    def factory(*_args):
        opened.append(True)
        raise AssertionError("connector opened")

    from api.dependencies import get_migration_service
    from backend.services.migration_service import MigrationService

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(factory)
    response = client.post(
        "/api/v1/migrations/",
        json=_body(source_type="mysql", target_type="postgresql", confirm_plan=True, confirm_replace=True, accept_unproven_backup=True),
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "mysql-to-postgresql" in response.text
    assert "No schema or data was copied." in response.text
    assert opened == []
    client.app.dependency_overrides.clear()


def test_replace_without_confirmation_is_rejected(client, auth_headers):
    from api.dependencies import get_migration_service
    from backend.services.migration_service import MigrationService

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(lambda *_args: (_ for _ in ()).throw(AssertionError("opened")))
    response = client.post(
        "/api/v1/migrations/",
        json=_body(strategy="replace_existing", confirm_plan=True, accept_unproven_backup=True),
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Nothing was dropped." in response.text
    client.app.dependency_overrides.clear()


def test_preview_shows_plan_and_blocks_an_unmapped_column(client, auth_headers):
    from api.dependencies import get_migration_service
    from backend.services.migration_service import MigrationService

    def source(sql):
        if "information_schema.columns" in sql and "orders" in sql:
            return [{"column_name": "tags", "data_type": "ARRAY", "udt_name": "_text", "is_nullable": "YES", "column_default": None, "identity_generation": ""}]
        return _source(sql)

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(_factory(source, _target))
    response = client.post("/api/v1/migrations/preview", json=_body(scope="schema_only"), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["details"]["ready"] is False
    assert "was not mapped" in body["message"]
    assert body["details"]["backup_proven"] is False
    assert body["details"]["restore_verified"] is False
    assert "not a tested MySQL restore" in body["details"]["plan"]
    client.app.dependency_overrides.clear()


def test_preview_lists_a_lossy_conversion_before_execution(client, auth_headers):
    from api.dependencies import get_migration_service
    from backend.services.migration_service import MigrationService

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(_factory(_source, _target))
    response = client.post("/api/v1/migrations/preview", json=_body(scope="schema_only"), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["details"]["ready"] is True
    assert body["details"]["executed"] is False
    assert any("AUTO_INCREMENT" in item for item in body["details"]["conversions"])
    client.app.dependency_overrides.clear()


def test_confirmed_schema_only_execution_reports_each_table(client, auth_headers):
    from api.dependencies import get_migration_service
    from backend.services.migration_service import MigrationService

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(_factory(_source, _target))
    response = client.post("/api/v1/migrations/", json=_body(scope="schema_only", confirm_plan=True), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["details"]["status"] == "Completed and verified"
    assert body["details"]["outcomes"][0]["action"] == "schema_created"
    assert body["details"]["backup_proven"] is False
    assert "password" not in response.text
    client.app.dependency_overrides.clear()


def test_connector_errors_redact_passwords(client, auth_headers):
    from api.dependencies import get_migration_service
    from backend.services.migration_service import MigrationService

    def factory(*_args):
        raise RuntimeError("postgresql://migrator:secret-value@127.0.0.1/db password=secret failed")

    client.app.dependency_overrides[get_migration_service] = lambda: MigrationService(factory)
    response = client.post("/api/v1/migrations/", json=_body(scope="schema_only", confirm_plan=True), headers=auth_headers)
    assert response.status_code == 500
    assert "secret" not in response.text
    assert "secret-value" not in response.text
    assert "[REDACTED]" in response.text
    client.app.dependency_overrides.clear()


def test_postgresql_to_postgresql_api_does_not_open_a_connector():
    from backend.core.exceptions import MigrationRejected
    from backend.services.migration_service import MigrationService

    def fail(*_args, **_kwargs):
        raise AssertionError("connector opened")

    service = MigrationService(fail)
    request = MigrationRequest(source_type=DatabaseType.POSTGRESQL, target_type=DatabaseType.POSTGRESQL)
    try:
        service.migrate(request, user="operator")
    except MigrationRejected as exc:
        assert "did not open a connection" in str(exc)
        assert "copy schema or data" in str(exc)
    else:
        raise AssertionError("postgresql-to-postgresql api was not rejected")


def test_duplicate_client_request_does_not_run_twice():
    from backend.services.migration_service import MigrationService

    calls = []

    def factory(db_type, profile):
        calls.append(db_type)
        return _factory(_source, _target)(db_type, profile)

    service = MigrationService(factory)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id=f"duplicate-{__import__('uuid').uuid4()}",
    )
    first = service.migrate(request, user="operator")
    second = service.migrate(request, user="operator")
    assert first.operation_id == second.operation_id
    assert calls == [DatabaseType.POSTGRESQL, DatabaseType.MYSQL]
    assert first.details["tables"]["Created"] == ["orders"]
    assert first.details["backup_proven"] is False
    assert first.details["restore_verified"] is False


def test_retry_after_partial_completion_requires_confirmation():
    from backend.core.exceptions import MigrationRejected
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.workflow import clear_migration_guards

    creates = []

    class _Failing:
        def __init__(self):
            self.autocommit = False

        def execute_query(self, sql, params=None):
            if sql.startswith("SELECT count(*)"):
                return [{"row_count": 0}]
            if "version_comment" in sql or sql.startswith("SHOW GRANTS") or "information_schema.tables" in sql:
                return _target(sql)
            return _source(sql)

        def open_connection(self):
            return self

        def cursor(self, name=None, dictionary=False):
            return self

        def execute(self, sql, _params=None):
            if sql.startswith("CREATE TABLE"):
                creates.append(sql)
            if "CREATE TABLE `lines`" in sql:
                raise RuntimeError("lines failed")

        def executemany(self, sql, rows):
            return None

        def fetchall(self):
            return [{"row_count": 0}]

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    clear_migration_guards()
    try:
        service = MigrationService(lambda *_args: _Failing())
        request = MigrationRequest(
            source_type=DatabaseType.POSTGRESQL,
            target_type=DatabaseType.MYSQL,
            scope="schema_only",
            strategy="skip_existing",
            source_database="postgres",
            target_database="app",
            tables=["orders", "lines"],
            confirm_plan=True,
            client_request_id=f"partial-{__import__('uuid').uuid4()}",
        )
        first = service.migrate(request, user="operator")
        assert first.details["status"] == "Partially completed"
        assert "orders" in first.details["tables"].get("Created", [])
        assert "lines" in first.details["tables"].get("Failed", [])
        assert "not rolled back" in first.message
        retry = request.model_copy(update={"client_request_id": f"partial-retry-{__import__('uuid').uuid4()}"})
        try:
            service.migrate(retry, user="operator")
        except MigrationRejected as exc:
            assert "Reconcile" in str(exc)
            assert "Nothing new was copied." in str(exc)
        else:
            raise AssertionError("partial migration was retried without reconciliation")
        assert service.reconcile(first.operation_id)["reconciled"] == 1
        retry_again = retry.model_copy(update={"client_request_id": f"partial-retry-2-{__import__('uuid').uuid4()}"})
        try:
            service.migrate(retry_again, user="operator")
        except MigrationRejected as exc:
            assert "Confirm retry" in str(exc)
            assert "Nothing new was copied." in str(exc)
        else:
            raise AssertionError("partial migration was retried without confirmation")
        assert sum("CREATE TABLE `lines`" in sql for sql in creates) == 1
        confirmed = retry.model_copy(update={"confirm_retry": True, "client_request_id": f"partial-go-{__import__('uuid').uuid4()}"})
        service.migrate(confirmed, user="operator")
        assert sum("CREATE TABLE `lines`" in sql for sql in creates) == 2
    finally:
        clear_migration_guards()


def test_duplicate_request_is_rejected_across_service_instances(tmp_path):
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    calls = []
    store = MigrationOperationStore(tmp_path / "shared.sqlite")

    def factory(db_type, profile):
        calls.append(db_type)
        return _factory(_source, _target)(db_type, profile)

    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id="shared-request",
    )
    first = MigrationService(factory, store).migrate(request, user="operator")
    second = MigrationService(factory, store).migrate(request, user="operator")
    assert first.operation_id == second.operation_id
    assert calls == [DatabaseType.POSTGRESQL, DatabaseType.MYSQL]
    saved = store.get(first.operation_id)
    assert saved["source_database"] == "postgres"
    assert saved["target_database"] == "app"
    assert "password" not in str(saved)


def test_concurrent_claims_start_one_migration(tmp_path):
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    calls = []
    store = MigrationOperationStore(tmp_path / "shared.sqlite")
    barrier = threading.Barrier(2)

    def factory(db_type, profile):
        calls.append(db_type)
        return _factory(_source, _target)(db_type, profile)

    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id="concurrent-request",
    )
    results = []

    def run():
        barrier.wait()
        results.append(MigrationService(factory, store).migrate(request, user="operator"))

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert results[0].operation_id == results[1].operation_id
    assert calls == [DatabaseType.POSTGRESQL, DatabaseType.MYSQL]


def test_restart_requires_reconciliation_before_another_copy(tmp_path):
    from backend.core.exceptions import MigrationRejected
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    path = tmp_path / "ops.sqlite"
    store = MigrationOperationStore(path)
    store.claim(
        operation_id="interrupted-1",
        client_request_id="interrupted-request",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    restarted = MigrationOperationStore(path)
    import os
    conn = restarted._connect()
    try:
        conn.execute(
            "UPDATE migration_operations SET owner_pid = ?, owner_instance = ? WHERE operation_id = 'interrupted-1'",
            (os.getpid(), f"{os.getpid()}:reused-process"),
        )
    finally:
        conn.close()
    assert restarted.reconcile_interrupted() == 1
    saved = restarted.get("interrupted-1")
    assert saved["status"] == "reconciliation_required"
    assert "Do not assume MySQL changes were rolled back" in saved["recovery"]

    creates = []

    class _Recording:
        def execute_query(self, sql, params=None):
            if sql.startswith("SELECT count(*)"):
                return [{"row_count": 0}]
            if "version_comment" in sql or sql.startswith("SHOW GRANTS") or "information_schema.tables" in sql:
                return _target(sql)
            return _source(sql)

        def open_connection(self):
            return self

        def cursor(self, name=None, dictionary=False):
            return self

        def execute(self, sql, _params=None):
            creates.append(sql)

        def executemany(self, sql, rows):
            return None

        def fetchall(self):
            return [{"row_count": 0}]

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

        autocommit = False

    service = MigrationService(lambda *_args: _Recording(), restarted)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id="after-restart",
    )
    try:
        service.migrate(request, user="operator")
    except MigrationRejected as exc:
        assert "Reconcile" in str(exc)
        assert "Nothing new was copied." in str(exc)
    else:
        raise AssertionError("an interrupted migration was started again")
    assert creates == []
    assert restarted.reconcile("interrupted-1")["reconciled"] == 1
    retry = request.model_copy(update={"client_request_id": "after-restart-retry"})
    try:
        service.migrate(retry, user="operator")
    except MigrationRejected as exc:
        assert "Confirm retry" in str(exc)
    else:
        raise AssertionError("retry ran without confirmation")
    assert creates == []
    confirmed = retry.model_copy(update={"confirm_retry": True, "client_request_id": "after-restart-go"})
    result = service.migrate(confirmed, user="operator")
    assert result.details["status"] == "Completed and verified"
    assert creates


def test_cancel_is_checked_between_tables_and_does_not_claim_ddl_rollback(tmp_path):
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    store = MigrationOperationStore(tmp_path / "ops.sqlite")
    started = threading.Event()

    class _Slow:
        def execute_query(self, sql, params=None):
            if sql.startswith("SELECT count(*)"):
                return [{"row_count": 1}]
            if "version_comment" in sql or sql.startswith("SHOW GRANTS") or "information_schema.tables" in sql:
                return _target(sql)
            return _source(sql)

        def open_connection(self):
            return self

        def cursor(self, name=None, dictionary=False):
            return self

        def execute(self, sql, _params=None):
            if sql.startswith("CREATE TABLE"):
                started.set()
                deadline = time.time() + 5
                while time.time() < deadline and not store.cancel_requested(self.operation_id):
                    time.sleep(0.05)
                time.sleep(0.3)

        def executemany(self, sql, rows):
            return None

        def fetchall(self):
            return [{"row_count": 0}]

        def fetchmany(self, size):
            return []

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

        autocommit = False
        operation_id = ""

    target = _Slow()

    def factory(db_type, profile):
        if db_type == DatabaseType.MYSQL:
            return target
        return _factory(_source, _target)(db_type, profile)

    service = MigrationService(factory, store)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders", "lines"],
        confirm_plan=True,
        client_request_id="cancel-request",
    )

    def cancel_when_started():
        assert started.wait(5)
        running = store.get_by_client_request("cancel-request")
        target.operation_id = running["operation_id"]
        assert service.request_cancel(running["operation_id"]) is True

    thread = threading.Thread(target=cancel_when_started)
    thread.start()
    result = service.migrate(request, user="operator")
    thread.join()
    assert result.details["status"] == "Cancelled"
    assert "not rolled back" in result.message
    assert "fully rolled back" not in result.message
    saved = store.get(result.operation_id)
    assert saved["status"] == "cancelled"
    assert saved["cancel_requested"] == 1


def test_stored_summary_redacts_passwords(tmp_path):
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    path = tmp_path / "ops.sqlite"

    class _Secret:
        def execute_query(self, sql, params=None):
            if sql.startswith("SELECT count(*)"):
                return [{"row_count": 0}]
            if "version_comment" in sql or sql.startswith("SHOW GRANTS") or "information_schema.tables" in sql:
                return _target(sql)
            return _source(sql)

        def open_connection(self):
            return self

        def cursor(self, name=None, dictionary=False):
            return self

        def execute(self, sql, _params=None):
            raise RuntimeError("copy failed password=secret")

        def fetchall(self):
            return [{"row_count": 0}]

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

        autocommit = False

    service = MigrationService(lambda *_args: _Secret(), MigrationOperationStore(path))
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id="redaction-request",
    )
    result = service.migrate(request, user="operator")
    assert result.details["status"] == "Failed"
    saved = service._store.get(result.operation_id)
    assert "secret" not in saved["summary"]
    assert "[REDACTED]" in saved["summary"]
    blob = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        blob += wal.read_bytes()
    assert b"secret" not in blob


def _claim_in_process(path: str, operation_id: str, result_path: str) -> None:
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    created, _row = MigrationOperationStore(path).claim(
        operation_id=operation_id,
        client_request_id="same-client",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    from pathlib import Path

    Path(result_path).write_text("created" if created else "existing", encoding="utf-8")


def test_two_processes_cannot_claim_the_same_client_request(tmp_path):
    import multiprocessing
    from pathlib import Path

    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    path = tmp_path / "shared.sqlite"
    MigrationOperationStore(path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_claim_in_process, args=(str(path), "op-a", str(first))),
        context.Process(target=_claim_in_process, args=(str(path), "op-b", str(second))),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert {Path(first).read_text(encoding="utf-8"), Path(second).read_text(encoding="utf-8")} == {"created", "existing"}


def test_older_operation_table_gains_owner_pid_and_can_be_snapshotted(tmp_path):
    import sqlite3

    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE migration_operations (
            operation_id TEXT PRIMARY KEY,
            client_request_id TEXT UNIQUE,
            source_database TEXT NOT NULL,
            target_database TEXT NOT NULL,
            target_server_id TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL,
            tables_json TEXT NOT NULL DEFAULT '{}',
            recovery TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            confirm_retry INTEGER NOT NULL DEFAULT 0,
            reconciled INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    store = MigrationOperationStore(path)
    created, row = store.claim(
        operation_id="legacy-1",
        client_request_id="legacy-request",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    assert created is True
    assert row["owner_pid"] > 0
    copy = tmp_path / "history.sqlite"
    store.snapshot(copy)
    restored = MigrationOperationStore(copy).get("legacy-1")
    assert restored["client_request_id"] == "legacy-request"
    assert "password" not in str(restored)


def test_unavailable_operation_store_does_not_open_a_connector(tmp_path):
    import sqlite3

    from backend.core.exceptions import MigrationError
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    calls = []
    store = MigrationOperationStore(tmp_path / "ops.sqlite")

    def broken_claim(**_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    store.claim = broken_claim

    def factory(*_args):
        calls.append("opened")
        raise AssertionError("connector opened")

    service = MigrationService(factory, store)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id="store-down",
    )
    try:
        service.migrate(request, user="operator")
    except MigrationError as exc:
        assert "operation store is unavailable" in str(exc)
        assert "No schema or data was copied." in str(exc)
    else:
        raise AssertionError("unavailable store was ignored")
    assert calls == []


def test_cancel_before_the_first_statement_copies_nothing(tmp_path):
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    store = MigrationOperationStore(tmp_path / "ops.sqlite")
    started = threading.Event()
    statements = []

    class _Target:
        operation_id = ""

        def execute_query(self, sql, params=None):
            if sql.startswith("SELECT count(*)"):
                return [{"row_count": 0}]
            if "version_comment" in sql or sql.startswith("SHOW GRANTS") or "information_schema.tables" in sql:
                return _target(sql)
            return _source(sql)

        def open_connection(self):
            started.set()
            deadline = time.time() + 5
            while time.time() < deadline and not store.cancel_requested(self.operation_id):
                time.sleep(0.02)
            return self

        def cursor(self, name=None, dictionary=False):
            return self

        def execute(self, sql, _params=None):
            statements.append(sql)

        def executemany(self, sql, rows):
            statements.append(sql)

        def fetchall(self):
            return [{"row_count": 0}]

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

        autocommit = False

    target = _Target()

    def factory(db_type, profile):
        if db_type == DatabaseType.MYSQL:
            return target
        return _factory(_source, _target)(db_type, profile)

    service = MigrationService(factory, store)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders"],
        confirm_plan=True,
        client_request_id="cancel-before",
    )

    def cancel_when_connected():
        assert started.wait(5)
        running = store.get_by_client_request("cancel-before")
        target.operation_id = running["operation_id"]
        assert service.request_cancel(running["operation_id"]) is True

    thread = threading.Thread(target=cancel_when_connected)
    thread.start()
    result = service.migrate(request, user="operator")
    thread.join()
    assert result.details["status"] == "Cancelled"
    assert statements == []
    assert "not rolled back" in result.message


def test_confirmed_retry_with_skip_does_not_drop_existing_tables(tmp_path):
    from backend.core.exceptions import MigrationRejected
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    statements = []

    class _Target:
        def execute_query(self, sql, params=None):
            if sql.startswith("SELECT count(*)"):
                return [{"row_count": 0}]
            if "version_comment" in sql or sql.startswith("SHOW GRANTS") or "information_schema.tables" in sql:
                return _target(sql)
            return _source(sql)

        def open_connection(self):
            return self

        def cursor(self, name=None, dictionary=False):
            return self

        def execute(self, sql, _params=None):
            statements.append(sql)
            if "CREATE TABLE `lines`" in sql:
                raise RuntimeError("lines failed")

        def executemany(self, sql, rows):
            return None

        def fetchall(self):
            return [{"row_count": 0}]

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

        autocommit = False

    store = MigrationOperationStore(tmp_path / "ops.sqlite")
    service = MigrationService(lambda *_args: _Target(), store)
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="skip_existing",
        source_database="postgres",
        target_database="app",
        tables=["orders", "lines"],
        confirm_plan=True,
        client_request_id="retry-skip",
    )
    first = service.migrate(request, user="operator")
    assert first.details["status"] == "Partially completed"
    service.reconcile(first.operation_id)
    try:
        service.migrate(request.model_copy(update={"client_request_id": "retry-skip-2", "confirm_retry": False}), user="operator")
    except MigrationRejected as exc:
        assert "Confirm retry" in str(exc)
        assert "Nothing new was copied." in str(exc)
    else:
        raise AssertionError("retry ran after reconciliation without confirmation")
    service.migrate(
        request.model_copy(update={"client_request_id": "retry-skip-3", "confirm_retry": True}),
        user="operator",
    )
    assert not any(sql.startswith("DROP TABLE") for sql in statements)


def test_pid_reuse_does_not_keep_a_dead_migration_running(tmp_path):
    import os

    from backend.services.pg_mysql_migration.operation_store import (
        MigrationOperationStore,
        current_process_instance,
        instance_is_alive,
    )

    store = MigrationOperationStore(tmp_path / "ops.sqlite")
    store.claim(
        operation_id="live-1",
        client_request_id="live-request",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    store.claim(
        operation_id="stale-1",
        client_request_id="stale-request",
        source_database="postgres",
        target_database="other",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    reused = f"{os.getpid()}:not-this-process"
    conn = store._connect()
    try:
        conn.execute("UPDATE migration_operations SET owner_instance = ? WHERE operation_id = 'stale-1'", (reused,))
    finally:
        conn.close()
    assert instance_is_alive(current_process_instance()) is True
    assert instance_is_alive(reused) is False
    assert store.reconcile_interrupted() == 1
    assert store.get("live-1")["status"] == "running"
    assert store.get("stale-1")["status"] == "reconciliation_required"
    assert store.get("live-1")["owner_instance"] == current_process_instance()


def test_schema_version_upgrades_and_refuses_a_newer_store(tmp_path):
    import sqlite3

    from backend.services.pg_mysql_migration.operation_store import SCHEMA_VERSION, MigrationOperationStore, MigrationStoreError

    path = tmp_path / "ops.sqlite"
    store = MigrationOperationStore(path)
    assert store.schema_version() == SCHEMA_VERSION
    conn = sqlite3.connect(path)
    conn.execute("UPDATE migration_store_meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    try:
        MigrationOperationStore(path)
    except MigrationStoreError as exc:
        assert "was not modified" in str(exc)
    else:
        raise AssertionError("a newer operation store was opened")
    check = sqlite3.connect(path)
    version = check.execute("SELECT value FROM migration_store_meta WHERE key = 'schema_version'").fetchone()[0]
    check.close()
    assert version == "99"


def test_snapshot_stays_consistent_while_wal_writes_continue(tmp_path):
    import shutil
    import sqlite3
    import threading

    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    path = tmp_path / "ops.sqlite"
    store = MigrationOperationStore(path)
    store.claim(
        operation_id="row-0",
        client_request_id="client-0",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    reader = sqlite3.connect(path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("BEGIN")
    reader.execute("SELECT operation_id FROM migration_operations")
    store.claim(
        operation_id="row-1",
        client_request_id="client-1",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    raw = tmp_path / "main-only.sqlite"
    shutil.copy(path, raw)
    stop = threading.Event()
    errors = []

    def writer():
        index = 2
        while not stop.is_set():
            try:
                store.claim(
                    operation_id=f"row-{index}",
                    client_request_id=f"client-{index}",
                    source_database="postgres",
                    target_database="app",
                    scope="schema_only",
                    strategy="skip_existing",
                    confirm_retry=False,
                )
            except Exception as exc:
                errors.append(exc)
                return
            index += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        time.sleep(0.2)
        snapshot = tmp_path / "history.sqlite"
        store.snapshot(snapshot)
    finally:
        stop.set()
        thread.join(5)
        reader.close()
    assert errors == []
    copied = sqlite3.connect(snapshot)
    rows = copied.execute(
        "SELECT operation_id, status, client_request_id, owner_instance FROM migration_operations ORDER BY operation_id"
    ).fetchall()
    copied.close()
    snap_ids = [row[0] for row in rows]
    assert "row-0" in snap_ids
    assert "row-1" in snap_ids
    assert all(row[1] == "running" and row[2] and row[3] for row in rows)
    assert len(snap_ids) == len(set(snap_ids))
    final = sqlite3.connect(path)
    final_ids = [row[0] for row in final.execute("SELECT operation_id FROM migration_operations")]
    final.close()
    assert set(snap_ids) <= set(final_ids)
    raw_conn = sqlite3.connect(raw)
    raw_ids = [row[0] for row in raw_conn.execute("SELECT operation_id FROM migration_operations")]
    raw_conn.close()
    assert "row-1" not in raw_ids


def test_operation_database_is_limited_to_the_current_user(tmp_path):
    import os
    import stat
    import subprocess

    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    path = tmp_path / "ops.sqlite"
    store = MigrationOperationStore(path)
    store.claim(
        operation_id="acl-1",
        client_request_id="acl-request",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    assert os.access(path, os.R_OK | os.W_OK)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
        return
    output = subprocess.check_output(["icacls", str(path)], text=True)
    assert "Everyone:" not in output
    assert "BUILTIN\\Users:" not in output
    assert os.environ.get("USERNAME", "") in output


def test_duplicate_request_while_running_reports_running_not_failed(tmp_path):
    from backend.models.schemas import OperationStatus
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    store = MigrationOperationStore(tmp_path / "ops.sqlite")
    store.claim(
        operation_id="in-flight",
        client_request_id="in-flight-request",
        source_database="postgres",
        target_database="app",
        scope="schema_only",
        strategy="skip_existing",
        confirm_retry=False,
    )
    service = MigrationService(lambda *_args: (_ for _ in ()).throw(AssertionError("connector opened")), store)
    replay = service.migrate(
        MigrationRequest(
            source_type=DatabaseType.POSTGRESQL,
            target_type=DatabaseType.MYSQL,
            scope="schema_only",
            strategy="skip_existing",
            source_database="postgres",
            target_database="app",
            tables=["orders"],
            confirm_plan=True,
            client_request_id="in-flight-request",
        ),
        user="operator",
    )
    assert replay.operation_id == "in-flight"
    assert replay.status == OperationStatus.RUNNING
    assert replay.completed_at is None
    assert replay.details["status"] == "running"
    assert replay.details["executed"] is False
    assert replay.details["restore_verified"] is False
    assert replay.details["backup_proven"] is False
