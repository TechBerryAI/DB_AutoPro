"""Dry-run executor tests. No package install is sent."""

from __future__ import annotations

import threading

from backend.services.mysql_patch.command_runner import apt_simulate_command
from backend.services.mysql_patch.executor import PatchExecutor, outcome_after_failure
from backend.services.mysql_patch.models import ExecutionStage, JobOutcome, PlanRevalidation
from tests.test_mysql_patch_discovery import CANDIDATE, FakeRunner
from tests.test_mysql_patch_plan import NEW_CANDIDATE, _Boom, _accepted, _approved_plan


def test_dry_run_matches_the_plan_and_installs_nothing():
    service, _plan, approved = _approved_plan()
    runner, _preview = _accepted()
    stages: list[ExecutionStage] = []
    report = PatchExecutor(plans=service).execute(
        runner,
        approved,
        backup_path="C:/backup.sql",
        on_stage=lambda stage, _message: stages.append(stage),
    )

    assert report.outcome == JobOutcome.DRY_RUN_PASSED
    assert report.stage == ExecutionStage.COMPLETED
    assert report.dry_run is True
    assert report.packages_installed is False
    assert report.retry_attempted is False
    assert report.stop_reason is None
    assert "No packages were installed" in (report.log_excerpt or "")
    assert stages == [ExecutionStage.REVALIDATED, ExecutionStage.EXECUTING, ExecutionStage.COMPLETED]
    assert any(command.startswith("apt-get -s ") for command in report.commands_run)
    assert all("apt-get install" not in command for command in report.commands_run)
    assert all("apt-get upgrade" not in command for command in report.commands_run)


def test_execute_mode_is_refused_before_any_remote_command():
    _service, _plan, approved = _approved_plan()
    report = PatchExecutor().execute(_Boom(), approved, backup_path="C:/backup.sql", mode="execute")

    assert report.outcome == JobOutcome.FAILED
    assert report.packages_installed is False
    assert report.stage == ExecutionStage.FAILED
    assert "not started" in (report.stop_reason or "")
    assert report.commands_run == []


def test_unapproved_plan_is_not_a_dry_run_success():
    service, plan, _approved = _approved_plan()
    report = PatchExecutor(plans=service).execute(_Boom(), plan, backup_path="C:/backup.sql")

    assert report.outcome == JobOutcome.FAILED
    assert report.stage == ExecutionStage.REVALIDATED
    assert "not approved" in (report.stop_reason or "")
    assert "No package install was sent" in (report.stop_reason or "")
    assert report.packages_installed is False


def test_changed_candidate_aborts_the_dry_run():
    service, _plan, approved = _approved_plan()
    runner, preview = _accepted(NEW_CANDIDATE)
    assert preview.accepted is True

    report = PatchExecutor(plans=service).execute(runner, approved, backup_path="C:/backup.sql")

    assert report.outcome == JobOutcome.FAILED
    assert report.packages_installed is False
    assert "no longer matches" in (report.stop_reason or "")


def test_timeout_is_interrupted_and_not_retried():
    service, _plan, approved = _approved_plan()
    report = PatchExecutor(plans=service).execute(
        FakeRunner({}, fail=TimeoutError("timed out talking to 10.0.0.8")),
        approved,
        backup_path="C:/backup.sql",
    )

    assert report.outcome == JobOutcome.INTERRUPTED
    assert report.stage == ExecutionStage.REVALIDATED
    assert report.packages_installed is False
    assert report.retry_attempted is False
    assert report.outcome != JobOutcome.PARTIALLY_COMPLETED
    assert "No package install was sent" in (report.stop_reason or "")


def test_disconnect_outcome_is_unknown():
    service, _plan, approved = _approved_plan()
    report = PatchExecutor(plans=service).execute(
        FakeRunner({}, fail=ConnectionResetError("Connection reset by peer")),
        approved,
        backup_path="C:/backup.sql",
    )

    assert report.outcome == JobOutcome.UNKNOWN
    assert report.packages_installed is False
    assert "No package install was sent" in (report.stop_reason or "")


def test_package_manager_failure_stays_failed():
    service, _plan, approved = _approved_plan()
    runner, _preview = _accepted()
    command = apt_simulate_command("mysql-server-8.0", CANDIDATE)
    runner.responses[command] = (100, "", "E: Could not get lock /var/lib/dpkg/lock-frontend\n")

    report = PatchExecutor(plans=service).execute(runner, approved, backup_path="C:/backup.sql")

    assert report.outcome == JobOutcome.FAILED
    assert report.stage == ExecutionStage.REVALIDATED
    assert report.packages_installed is False
    assert "lock" in (report.stop_reason or "").lower()


def test_secret_in_a_transport_error_is_redacted():
    service, _plan, approved = _approved_plan()
    report = PatchExecutor(plans=service).execute(
        FakeRunner({}, fail=RuntimeError("login failed password=hunter2")),
        approved,
        backup_path="C:/backup.sql",
    )

    assert "hunter2" not in (report.stop_reason or "")
    assert "hunter2" not in (report.log_excerpt or "")
    assert "[REDACTED]" in (report.stop_reason or "")


def test_second_job_for_the_same_server_is_rejected_while_the_first_runs():
    _service, _plan, approved = _approved_plan()
    started = threading.Event()
    release = threading.Event()

    class _Gate:
        def revalidate(self, runner, plan, *, backup_path=None):
            del runner, backup_path
            started.set()
            assert release.wait(2)
            return PlanRevalidation(
                plan_id=plan.plan_id,
                valid=True,
                execution_allowed=True,
                commands_run=[apt_simulate_command(plan.server_package, plan.target_version)],
            )

    executor = PatchExecutor(plans=_Gate())
    holder: list = []

    def _run() -> None:
        holder.append(executor.execute(_Boom(), approved, backup_path="C:/backup.sql"))

    worker = threading.Thread(target=_run)
    worker.start()
    assert started.wait(2)
    blocked = executor.execute(_Boom(), approved, backup_path="C:/backup.sql")
    release.set()
    worker.join(3)

    assert blocked.outcome == JobOutcome.FAILED
    assert "already running" in (blocked.stop_reason or "")
    assert blocked.commands_run == []
    assert holder[0].outcome == JobOutcome.DRY_RUN_PASSED
    assert not worker.is_alive()


def test_lock_is_released_after_a_failed_dry_run():
    service, _plan, approved = _approved_plan()
    executor = PatchExecutor(plans=service)
    failed = executor.execute(
        FakeRunner({}, fail=TimeoutError("timed out")),
        approved,
        backup_path="C:/backup.sql",
    )
    runner, _preview = _accepted()
    again = executor.execute(runner, approved, backup_path="C:/backup.sql")

    assert failed.outcome == JobOutcome.INTERRUPTED
    assert again.outcome == JobOutcome.DRY_RUN_PASSED
    assert again.retry_attempted is False


def test_observed_install_command_is_not_treated_as_a_clean_dry_run():
    _service, _plan, approved = _approved_plan()

    class _Bad:
        def revalidate(self, runner, plan, *, backup_path=None):
            del runner, backup_path
            return PlanRevalidation(
                plan_id=plan.plan_id,
                valid=True,
                execution_allowed=True,
                commands_run=["apt-get install mysql-server-8.0"],
            )

    report = PatchExecutor(plans=_Bad()).execute(_Boom(), approved, backup_path="C:/backup.sql")

    assert report.outcome == JobOutcome.PARTIALLY_COMPLETED
    assert report.packages_installed is False
    assert report.retry_attempted is False
    assert "retry" in (report.stop_reason or "").lower()


def test_failure_mapping_marks_a_sent_install_as_partial():
    assert outcome_after_failure(error_text="timed out", install_was_sent=True) == JobOutcome.PARTIALLY_COMPLETED
    assert outcome_after_failure(error_text="timed out", install_was_sent=False) == JobOutcome.INTERRUPTED
    assert (
        outcome_after_failure(error_text="connection reset", install_was_sent=False) == JobOutcome.UNKNOWN
    )
