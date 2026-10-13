"""Verification and audit tests. The server is mocked and nothing is installed."""

from __future__ import annotations

from backend.services.mysql_patch.audit import MysqlPatchAuditStore, final_status
from backend.services.mysql_patch.command_runner import CMD_DPKG, CMD_DPKG_AUDIT, CMD_SERVICE_MYSQL
from backend.services.mysql_patch.executor import PatchExecutor
from backend.services.mysql_patch.models import BackupReadinessState, CheckStatus, DiscoveryCheck, JobOutcome
from backend.services.mysql_patch.precheck import PrecheckService
from backend.services.mysql_patch.verification import VerificationService
from tests.test_mysql_patch_discovery import CANDIDATE, INSTALLED, FakeRunner
from tests.test_mysql_patch_plan import _accepted, _approved_plan
from tests.test_mysql_patch_precheck import _Backups, _ready_responses


def _server(version: str, *, service: str = "active", audit: str = "", fail: Exception | None = None) -> FakeRunner:
    return FakeRunner(
        {
            CMD_DPKG: (0, f"mysql-server-8.0\t{version}\tinstalled\n", ""),
            CMD_SERVICE_MYSQL: (0, f"{service}\n", ""),
            CMD_DPKG_AUDIT: (0, audit, ""),
        },
        fail=fail,
    )


def _plan():
    _service, _original, approved = _approved_plan()
    return approved


def test_matching_version_service_and_select_one_is_success():
    report = VerificationService().verify(_server(CANDIDATE), _plan(), sql_probe=lambda: True, apt_exit_code=0)

    assert report.outcome == JobOutcome.SUCCESS
    assert report.target_reached is True
    assert report.service_active is True
    assert report.sql_ok is True
    assert report.recommended_next_steps == []
    assert all("apt-get install" not in command for command in report.commands_run)
    assert all("downgrade" not in command and "restore" not in command for command in report.commands_run)


def test_apt_exit_code_zero_does_not_mean_success_when_version_differs():
    report = VerificationService().verify(_server(INSTALLED), _plan(), sql_probe=lambda: True, apt_exit_code=0)

    assert report.outcome == JobOutcome.VERIFICATION_FAILED
    assert report.target_reached is False
    assert CANDIDATE in (report.stop_reason or "")
    assert any("downgrade" in step.lower() or "restore" in step.lower() for step in report.recommended_next_steps)


def test_inactive_service_fails_verification():
    report = VerificationService().verify(
        _server(CANDIDATE, service="inactive"),
        _plan(),
        sql_probe=lambda: True,
    )

    assert report.outcome == JobOutcome.VERIFICATION_FAILED
    assert report.service_active is False
    assert "inactive" in (report.stop_reason or "")


def test_select_one_failure_blocks_success_and_redacts_secrets():
    def _broken() -> bool:
        raise RuntimeError("SELECT 1 failed password=hunter2")

    report = VerificationService().verify(_server(CANDIDATE), _plan(), sql_probe=_broken)

    assert report.outcome == JobOutcome.VERIFICATION_FAILED
    assert report.sql_checked is True
    assert report.sql_ok is False
    assert "hunter2" not in (report.stop_reason or "")
    assert "hunter2" not in " ".join(item.summary for item in report.checks)


def test_skipped_sql_is_not_called_success():
    report = VerificationService().verify(_server(CANDIDATE), _plan(), sql_probe=None)

    assert report.sql_checked is False
    assert report.outcome == JobOutcome.VERIFICATION_FAILED
    assert "SELECT 1" in (report.stop_reason or "")
    assert any(item.code == "sql" and item.status == CheckStatus.SKIPPED for item in report.checks)


def test_dpkg_problem_fails_even_when_the_version_matches():
    report = VerificationService().verify(
        _server(CANDIDATE, audit="mysql-server-8.0: package is half-configured\n"),
        _plan(),
        sql_probe=lambda: True,
    )

    assert report.outcome == JobOutcome.VERIFICATION_FAILED
    assert report.package_manager_clean is False


def test_verification_transport_failure_is_not_success():
    report = VerificationService().verify(_server(CANDIDATE, fail=TimeoutError("timed out")), _plan())

    assert report.outcome == JobOutcome.VERIFICATION_FAILED
    assert "timed out" in (report.stop_reason or "")


def test_audit_history_redacts_secrets_and_keeps_the_plan_snapshot(tmp_path):
    service, _original, approved = _approved_plan()
    runner, _preview = _accepted()
    execution = PatchExecutor(plans=service).execute(runner, approved, backup_path="C:/backup.sql")
    approved.precheck_findings.append(
        DiscoveryCheck(code="note", status=CheckStatus.WARNING, summary="password=hunter2")
    )
    verification = VerificationService().verify(_server(INSTALLED), approved, sql_probe=lambda: True, apt_exit_code=0)
    store = MysqlPatchAuditStore(tmp_path / "mysql_patch_jobs.json")
    saved = store.record(execution, approved, initiated_by="alex", verification=verification)

    text = (tmp_path / "mysql_patch_jobs.json").read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert "password" not in saved.model_dump().keys()
    assert saved.plan_id == approved.plan_id
    assert saved.plan_digest == approved.plan_digest
    assert saved.approved_by == "alex"
    assert saved.current_version == approved.current_version
    assert saved.target_version == approved.target_version
    assert saved.execution_outcome == JobOutcome.DRY_RUN_PASSED.value
    assert saved.verification_outcome == JobOutcome.VERIFICATION_FAILED.value
    assert saved.final_status == JobOutcome.DRY_RUN_PASSED.value
    assert saved.packages_installed is False
    assert saved.dry_run is True
    assert store.history("db-01")[0].job_id == execution.job_id


def test_dry_run_without_verification_is_not_stored_as_success():
    service, _original, approved = _approved_plan()
    runner, _preview = _accepted()
    execution = PatchExecutor(plans=service).execute(runner, approved, backup_path="C:/backup.sql")

    assert final_status(execution, None) == JobOutcome.DRY_RUN_PASSED
    assert final_status(execution, None) != JobOutcome.SUCCESS


def test_stored_unsafe_job_blocks_the_next_precheck(tmp_path):
    service, _original, approved = _approved_plan()
    execution = PatchExecutor(plans=service).execute(
        FakeRunner({}, fail=TimeoutError("timed out")),
        approved,
        backup_path="C:/backup.sql",
    )
    store = MysqlPatchAuditStore(tmp_path / "jobs.json")
    store.record(execution, approved, initiated_by="alex")
    precheck = PrecheckService(
        backups=_Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        ),
        jobs=store,
    ).precheck(FakeRunner(_ready_responses()), server_id="db-01", backup_path="C:/backup.sql")

    history = next(item for item in precheck.checks if item.code == "job_history")
    assert history.status == CheckStatus.FAIL
    assert execution.job_id in history.summary
    assert precheck.ready_for_plan is False


def test_corrupt_history_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("{not json", encoding="utf-8")
    store = MysqlPatchAuditStore(path)

    assert store.history() == []
    assert store.jobs_for("db-01") == []
