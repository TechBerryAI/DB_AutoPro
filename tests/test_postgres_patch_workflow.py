"""Reviewed PostgreSQL SQL patch workflow. No live database and no real patch execution."""

from __future__ import annotations

import json

from backend.core.exceptions import ConnectorError
from backend.models.schemas import PatchHistoryEntry
from backend.services.patch_service import PatchService
from backend.services.postgres_patch.admin_check import assess_administrator
from backend.services.postgres_patch.sql_patch import inspect_sql_patch
from backend.services.postgres_patch.workflow import run_reviewed_patch
from desktop.postgres_patch_flow import execution_block_reason
from tests.test_postgres_patch_admin import _query


class _Script:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls = 0
        self.fail = fail

    def execute_script(self, script: str) -> None:
        self.calls += 1
        if self.fail is not None:
            raise self.fail


def _service(monkeypatch, tmp_path) -> PatchService:
    monkeypatch.setattr("backend.services.patch_service.HISTORY_FILE", tmp_path / "patch_history.json")
    return PatchService()


def _ready_report():
    return assess_administrator(_query())


def _review(
    tmp_path,
    service,
    sql="SELECT 1;\n",
    name="add_index.sql",
    database="postgres",
    system_identifier="1001",
    database_oid="2002",
    endpoint="192.168.1.113:5432",
    allow_repeat=False,
    reconcile_interrupted=False,
):
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    return inspect_sql_patch(
        str(path),
        description="add index",
        target_database=database,
        system_identifier=system_identifier,
        database_oid=database_oid,
        configured_endpoint=endpoint,
        completed_identities=service.completed_patch_identities(),
        running_identities=service.running_patch_identities(),
        legacy_running=service.legacy_running_patches(),
        allow_repeat=allow_repeat,
        reconcile_interrupted=reconcile_interrupted,
    )


def _run(
    monkeypatch,
    service,
    report,
    review,
    *,
    script,
    verify=None,
    confirmed=True,
    operator="dba",
    target_host=None,
):
    monkeypatch.setattr(
        "backend.services.patch_service.get_connector",
        lambda *_args, **_kwargs: script,
    )
    if verify is None:
        verify = _query(database=review.target_database)

    def script_executor(sql: str) -> None:
        script.execute_script(sql)

    return run_reviewed_patch(
        report=report,
        review=review,
        confirmed=confirmed,
        operator=operator,
        service=service,
        target_host=review.configured_endpoint if target_host is None else target_host,
        verify_query=verify,
        script_executor=script_executor,
    )


def test_a_reviewed_patch_records_execution_separately_from_verification(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service, sql="SELECT 1;\n-- marker-value-should-stay-out-of-history\n")
    result = _run(monkeypatch, service, _ready_report(), review, script=script)

    assert result.executed is True
    assert result.execution_status == "completed"
    assert result.verification_status == "passed"
    assert script.calls == 1
    assert "No separate approver was recorded" in result.summary
    assert "do not prove the SQL patch changed" in result.summary
    saved = json.loads((tmp_path / "patch_history.json").read_text(encoding="utf-8"))
    entry = saved[0]
    assert entry["executed_by"] == "dba"
    assert entry["confirmed_by"] == "dba"
    assert entry["target_database"] == "postgres"
    assert entry["target_host"] == "192.168.1.113:5432"
    assert entry["checksum_sha256"] == review.checksum_sha256
    assert entry["finished_at"]
    assert entry["duration_seconds"] is not None
    assert entry["verification_status"] == "passed"
    assert entry["apply_result"] == "committed"
    assert "approved_by" not in entry
    assert "marker-value-should-stay-out-of-history" not in json.dumps(saved)


def test_a_role_without_administration_privilege_does_not_execute(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    report = assess_administrator(_query(role=False))
    review = _review(tmp_path, service)
    result = _run(monkeypatch, service, report, review, script=script)

    assert report.ready_to_patch is False
    assert result.executed is False
    assert script.calls == 0
    assert not (tmp_path / "patch_history.json").exists()


def test_failed_prechecks_stop_execution(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    report = assess_administrator(_query(blocking=1))
    review = _review(tmp_path, service)
    result = _run(monkeypatch, service, report, review, script=script)

    assert result.executed is False
    assert result.execution_status == "not_run"
    assert script.calls == 0


def test_a_missing_sql_file_is_rejected(tmp_path):
    review = inspect_sql_patch(
        str(tmp_path / "missing.sql"),
        description="",
        target_database="postgres",
        system_identifier="1001",
        database_oid="2002",
        configured_endpoint="192.168.1.113:5432",
        completed_identities=set(),
    )

    assert review.valid is False
    assert review.message == "SQL file was not found."


def test_keyword_hints_do_not_reject_a_readable_file(tmp_path):
    path = tmp_path / "drop.sql"
    path.write_text("DROP TABLE employees;\n", encoding="utf-8")
    review = inspect_sql_patch(
        str(path),
        description="",
        target_database="postgres",
        system_identifier="1001",
        database_oid="2002",
        configured_endpoint="192.168.1.113:5432",
        completed_identities=set(),
    )

    assert review.valid is True
    assert any("DROP" in item for item in review.warnings)
    assert "not a parsed statement" in review.warnings[0]


def test_a_completed_checksum_is_a_duplicate_and_is_not_run(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)
    first = _run(monkeypatch, service, _ready_report(), review, script=script)
    assert first.execution_status == "completed"
    again = inspect_sql_patch(
        review.path,
        description="add index",
        target_database="postgres",
        system_identifier="1001",
        database_oid="2002",
        configured_endpoint="192.168.1.113:5432",
        completed_identities=service.completed_patch_identities(),
    )
    second = _run(monkeypatch, service, _ready_report(), again, script=script)

    assert again.duplicate_of
    assert second.executed is False
    assert script.calls == 1


def test_execution_failure_is_recorded_once_and_is_not_retried(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script(fail=ConnectorError("password=secret failed on 'row-secret'"))
    review = _review(tmp_path, service)
    result = _run(monkeypatch, service, _ready_report(), review, script=script)

    assert result.executed is True
    assert result.execution_status == "failed"
    assert result.verification_status == "not_run"
    assert script.calls == 1
    saved = json.loads((tmp_path / "patch_history.json").read_text(encoding="utf-8"))
    assert saved[0]["status"] == "failed"
    assert saved[0]["apply_result"] == "rolled_back"
    assert saved[0]["verification_status"] == "not_run"
    assert "secret" not in saved[0]["error"]
    assert "[REDACTED]" in saved[0]["error"]
    assert "'[value]'" in saved[0]["error"]
    assert "does not roll back SQL automatically" in saved[0]["verification_summary"]


def test_a_failed_attempt_is_not_described_as_partial_success(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script(fail=ConnectorError("statement failed"))
    review = _review(tmp_path, service)
    result = _run(monkeypatch, service, _ready_report(), review, script=script)
    saved = json.loads((tmp_path / "patch_history.json").read_text(encoding="utf-8"))

    assert result.execution_status == "failed"
    assert saved[0]["status"] == "failed"
    assert saved[0]["apply_result"] == "rolled_back"
    assert "partial" not in result.summary.lower()
    assert script.calls == 1


def test_verification_failure_stays_separate_from_a_committed_patch(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)
    recorded: list[str] = []

    class _Audit:
        def log(self, action, user, target, status, details=None):
            del action, user, target, details
            recorded.append(status)

    monkeypatch.setattr(
        "backend.services.postgres_patch.workflow.get_audit_logger",
        lambda: _Audit(),
    )

    def verify(sql: str):
        if "pg_control_system" in sql:
            return [{"database_name": "postgres", "database_oid": "2002", "system_identifier": "1001"}]
        if " AS ok" in sql:
            return [{"database_name": "other", "in_recovery": False, "ok": 1}]
        return [{"database_name": "postgres"}]

    result = _run(monkeypatch, service, _ready_report(), review, script=script, verify=verify)
    saved = json.loads((tmp_path / "patch_history.json").read_text(encoding="utf-8"))
    from backend.services.postgres_patch.workflow import format_history_lines

    assert result.execution_status == "completed"
    assert result.verification_status == "failed"
    assert "not verified" in result.summary
    assert "Verification passed" not in result.summary
    assert saved[0]["status"] == "completed"
    assert saved[0]["apply_result"] == "committed"
    assert saved[0]["verification_status"] == "failed"
    assert "does not match the patch target" in saved[0]["verification_summary"]
    assert "verification_failed" in format_history_lines(service.get_history())
    assert recorded == ["failed"]
    assert "success" not in recorded


def test_confirmation_is_required_before_execution(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)
    result = _run(monkeypatch, service, _ready_report(), review, script=script, confirmed=False)

    assert result.executed is False
    assert script.calls == 0
    assert execution_block_reason(
        connected=True,
        checks_ready=True,
        file_validated=True,
        duplicate=False,
        review_shown=True,
        confirmed=False,
        operator_known=True,
        busy=False,
    )


def test_older_history_rows_still_load(monkeypatch, tmp_path):
    history = tmp_path / "patch_history.json"
    history.write_text(
        json.dumps(
            [
                {
                    "patch_id": "old",
                    "sql_file": "old.sql",
                    "description": "",
                    "executed_at": "2026-10-01T00:00:00+00:00",
                    "status": "completed",
                    "executed_by": "dba",
                    "rollback_available": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.services.patch_service.HISTORY_FILE", history)
    service = PatchService()
    entry = service.get_history()[0]

    assert isinstance(entry, PatchHistoryEntry)
    assert entry.checksum_sha256 == ""
    assert entry.confirmed_by == ""
    assert service.completed_patch_identities() == set()


def test_the_connected_database_must_match_the_reviewed_database(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)

    def verify(sql: str):
        if "pg_control_system" in sql:
            return [{"database_name": "otherdb", "database_oid": "2002", "system_identifier": "1001"}]
        if " AS ok" in sql:
            return [{"database_name": "postgres", "in_recovery": False, "ok": 1}]
        return [{"database_name": "otherdb"}]

    result = _run(monkeypatch, service, _ready_report(), review, script=script, verify=verify)

    assert result.executed is False
    assert "otherdb" in result.summary
    assert script.calls == 0


def test_a_connection_failure_does_not_execute_sql(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)

    def verify(_sql: str):
        raise ConnectorError("PostgreSQL connection failed: password=secret")

    result = _run(monkeypatch, service, _ready_report(), review, script=script, verify=verify)

    assert result.executed is False
    assert script.calls == 0
    assert "secret" not in result.summary
    assert "[REDACTED]" in result.summary


def test_a_completed_patch_runs_again_only_when_repeat_is_explicit(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)
    _run(monkeypatch, service, _ready_report(), review, script=script)
    review.duplicate_of = review.checksum_sha256
    review.allow_repeat = False
    review.valid = True
    blocked = _run(monkeypatch, service, _ready_report(), review, script=script)
    review.allow_repeat = True
    repeated = _run(monkeypatch, service, _ready_report(), review, script=script)

    assert blocked.executed is False
    assert repeated.executed is True
    assert script.calls == 2


def test_a_partial_failure_names_committed_statements(monkeypatch, tmp_path):
    from backend.services.postgres_patch.script_runner import PostgresScriptError

    service = _service(monkeypatch, tmp_path)
    review = _review(tmp_path, service)

    def executor(_sql: str) -> None:
        raise PostgresScriptError(
            "Statement 2 of 2 failed after 1 statement(s) had already been committed.",
            apply_result="partial",
            statement_index=2,
            statement_count=2,
            completed_count=1,
        )

    result = run_reviewed_patch(
        report=_ready_report(),
        review=review,
        confirmed=True,
        operator="dba",
        service=service,
        verify_query=_query(),
        target_host=review.configured_endpoint,
        script_executor=executor,
    )
    saved = json.loads((tmp_path / "patch_history.json").read_text(encoding="utf-8"))

    assert result.execution_status == "failed"
    assert result.verification_status == "passed"
    assert saved[0]["apply_result"] == "partial"
    assert "not rolled back" in result.summary
    assert "partial" in result.summary.lower()


def test_a_run_is_recorded_before_sql_starts(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    review = _review(tmp_path, service, name="second.sql", sql="SELECT 2;\n")
    seen: dict[str, set] = {}

    def executor(_sql: str) -> None:
        seen["running"] = service.running_patch_identities()
        raise ConnectorError("connection dropped")

    result = run_reviewed_patch(
        report=_ready_report(),
        review=review,
        confirmed=True,
        operator="dba",
        service=service,
        verify_query=_query(),
        target_host=review.configured_endpoint,
        script_executor=executor,
    )

    assert seen["running"]
    assert result.execution_status == "failed"
    assert result.verification_status == "not_run"


def test_the_same_checksum_can_run_on_a_different_database(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    first = _review(tmp_path, service, name="same.sql")
    assert _run(monkeypatch, service, _ready_report(), first, script=script).execution_status == "completed"
    second = _review(tmp_path, service, name="same.sql", database_oid="3003")
    result = _run(
        monkeypatch,
        service,
        _ready_report(),
        second,
        script=script,
        verify=_query(database_oid="3003"),
    )

    assert second.duplicate_of is None
    assert result.executed is True
    assert script.calls == 2


def test_the_same_checksum_can_run_on_a_different_cluster(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    first = _review(tmp_path, service, name="cluster.sql")
    _run(monkeypatch, service, _ready_report(), first, script=script)
    second = _review(tmp_path, service, name="cluster.sql", system_identifier="9090")
    result = _run(
        monkeypatch,
        service,
        _ready_report(),
        second,
        script=script,
        verify=_query(system_identifier="9090"),
    )

    assert result.executed is True
    assert script.calls == 2


def test_database_name_case_and_server_mismatches_block_execution(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service, database="MyDb")
    case_result = _run(
        monkeypatch,
        service,
        _ready_report(),
        review,
        script=script,
        verify=_query(database="mydb"),
    )
    cluster_result = _run(
        monkeypatch,
        service,
        _ready_report(),
        review,
        script=script,
        verify=_query(database="MyDb", system_identifier="5555"),
    )
    endpoint_result = _run(
        monkeypatch,
        service,
        _ready_report(),
        review,
        script=script,
        verify=_query(database="MyDb"),
        target_host="10.0.0.8:5432",
    )

    assert case_result.executed is False
    assert "mydb" in case_result.summary
    assert cluster_result.executed is False
    assert "cluster" in cluster_result.summary
    assert endpoint_result.executed is False
    assert "configured server" in endpoint_result.summary
    assert script.calls == 0


def test_a_reloaded_service_still_blocks_the_completed_patch(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)
    assert _run(monkeypatch, service, _ready_report(), review, script=script).execution_status == "completed"
    reloaded = PatchService()
    again = _review(tmp_path, reloaded)

    assert (review.checksum_sha256, "1001", "2002") in reloaded.completed_patch_identities()
    assert again.duplicate_of == review.checksum_sha256
    blocked = _run(monkeypatch, reloaded, _ready_report(), again, script=script)
    assert blocked.executed is False
    assert script.calls == 1


def test_a_failed_history_write_does_not_execute_or_destroy_history(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service)
    _run(monkeypatch, service, _ready_report(), review, script=script)
    before = (tmp_path / "patch_history.json").read_text(encoding="utf-8")

    def fail_before_sql(self):
        raise OSError("disk full")

    monkeypatch.setattr(PatchService, "_save_history", fail_before_sql)
    fresh = _review(tmp_path, service, name="other.sql", sql="SELECT 2;\n", database_oid="3003")
    refused = _run(
        monkeypatch,
        service,
        _ready_report(),
        fresh,
        script=script,
        verify=_query(database_oid="3003"),
    )

    assert refused.executed is False
    assert "not executed" in refused.summary
    assert (tmp_path / "patch_history.json").read_text(encoding="utf-8") == before
    assert script.calls == 1


def test_a_history_write_failure_after_sql_is_not_called_success(monkeypatch, tmp_path):
    service = _service(monkeypatch, tmp_path)
    script = _Script()
    review = _review(tmp_path, service, name="late.sql")
    original = PatchService._save_history
    calls = {"n": 0}

    def fail_second(self):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("disk full")
        return original(self)

    monkeypatch.setattr(PatchService, "_save_history", fail_second)
    result = _run(monkeypatch, service, _ready_report(), review, script=script)
    reloaded = PatchService()

    assert script.calls == 1
    assert result.verification_status == "not_run"
    assert "not recorded as successful or verified" in result.summary
    assert reloaded.get_history()[-1].status.value == "running"


def test_an_interrupted_history_row_is_not_rerun_until_reconciled(monkeypatch, tmp_path):
    from hashlib import sha256

    history = tmp_path / "patch_history.json"
    path = tmp_path / "stopped.sql"
    path.write_text("SELECT 1;\n", encoding="utf-8")
    checksum = sha256(path.read_bytes()).hexdigest()
    history.write_text(
        json.dumps(
            [
                {
                    "patch_id": "stopped",
                    "sql_file": str(path),
                    "description": "",
                    "executed_at": "2026-10-16T00:00:00+00:00",
                    "status": "running",
                    "executed_by": "dba",
                    "rollback_available": False,
                    "target_database": "postgres",
                    "checksum_sha256": checksum,
                    "system_identifier": "1001",
                    "database_oid": "2002",
                    "patch_name": "stopped.sql",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.services.patch_service.HISTORY_FILE", history)
    service = PatchService()
    script = _Script()
    blocked = _review(tmp_path, service, name="stopped.sql")
    refused = _run(monkeypatch, service, _ready_report(), blocked, script=script)
    allowed = _review(tmp_path, service, name="stopped.sql", reconcile_interrupted=True)
    repeated = _run(monkeypatch, service, _ready_report(), allowed, script=script)

    assert blocked.interrupted is True
    assert blocked.valid is False
    assert refused.executed is False
    assert "not run again" in refused.summary
    assert repeated.executed is True
    assert script.calls == 1


