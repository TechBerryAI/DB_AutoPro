"""Precheck tests. SSH and package installation are mocked."""

from __future__ import annotations

from backend.services.mysql_patch.command_runner import (
    CMD_DF,
    CMD_DPKG_AUDIT,
    CMD_ID,
    CMD_LOCKS,
    CMD_SUDO_CHECK,
    MUTATING_MARKERS,
    allowed_commands,
)
from backend.services.mysql_patch.models import BackupReadinessState, JobOutcome, PatchJobSnapshot
from backend.services.mysql_patch.precheck import (
    MIN_FREE_BYTES,
    LocalBackupReadiness,
    PrecheckService,
)
from tests.test_mysql_patch_discovery import (
    CANDIDATE,
    INSTALLED,
    FakeRunner,
    _dpkg,
    _os_release,
    _policy,
    _responses,
)

_LOCK_PATHS = (
    "/var/lib/dpkg/lock-frontend",
    "/var/lib/dpkg/lock",
    "/var/lib/apt/lists/lock",
    "/var/cache/apt/archives/lock",
)


class _Backups:
    def __init__(self, state: BackupReadinessState, summary: str) -> None:
        self.state = state
        self.summary = summary

    def assess(self, backup_path: str | None) -> tuple[BackupReadinessState, str]:
        del backup_path
        return self.state, self.summary


class _Jobs:
    def __init__(self, jobs: list[PatchJobSnapshot]) -> None:
        self._jobs = jobs

    def jobs_for(self, server_id: str) -> list[PatchJobSnapshot]:
        del server_id
        return list(self._jobs)


def _df(available: int) -> str:
    line = f"/dev/root 20000000000 1000 {available} 50% /"
    header = "Filesystem 1B-blocks Used Available Capacity Mounted on"
    return "\n".join([header, line, line, line]) + "\n"


def _locks(state: str = "free") -> str:
    return "".join(f"{state} {path}\n" for path in _LOCK_PATHS)


def _ready_responses(**overrides: tuple[int, str, str]) -> dict[str, tuple[int, str, str]]:
    policy = _policy(INSTALLED, CANDIDATE, ["http://archive.ubuntu.com/ubuntu"])
    data = _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    data[CMD_ID] = (0, "0\n", "")
    data[CMD_DF] = (0, _df(MIN_FREE_BYTES), "")
    data[CMD_LOCKS] = (0, _locks(), "")
    data[CMD_DPKG_AUDIT] = (0, "", "")
    data.update(overrides)
    return data


def _service(**kwargs: object) -> PrecheckService:
    backups = kwargs.get("backups")
    jobs = kwargs.get("jobs", _Jobs([]))
    if backups is None:
        backups = _Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        )
    return PrecheckService(backups=backups, jobs=jobs)  # type: ignore[arg-type]


def _run(responses: dict[str, tuple[int, str, str]], service: PrecheckService | None = None, fail: Exception | None = None):
    runner = FakeRunner(responses, fail=fail)
    report = (service or _service()).precheck(runner, server_id="db-01", backup_path="C:/backup.sql")
    return runner, report


def test_successful_precheck_is_ready_for_a_plan():
    runner, report = _run(_ready_responses())

    assert report.precheck_passed is True
    assert report.ready_for_plan is True
    assert report.recovery_proven is False
    assert report.host_key_verified is False
    assert report.service_interruption_expected is True
    assert report.backup_state == BackupReadinessState.READABLE_UNVERIFIED
    assert any(item.code == "host_key" and item.status.value == "warning" for item in report.checks)
    assert "prove" in next(item.summary for item in report.checks if item.code == "backup")
    assert not any(marker in command for command in runner.calls for marker in MUTATING_MARKERS)
    assert set(report.commands_run).issubset(allowed_commands())


def test_unsupported_os_skips_remote_safety_commands():
    responses = _responses(os_text=_os_release("debian", "12"))
    runner, report = _run(responses)

    assert report.ready_for_plan is False
    assert report.precheck_passed is False
    assert CMD_ID not in runner.calls
    assert CMD_DF not in runner.calls
    assert any(item.code == "sudo" and item.status.value == "skipped" for item in report.checks)


def test_ssh_failure_fails_closed():
    _, report = _run({}, fail=TimeoutError("timed out"))

    assert report.precheck_passed is False
    assert report.ready_for_plan is False
    assert report.stop_reason == "timed out"


def test_missing_sudo_fails():
    responses = _ready_responses()
    responses[CMD_ID] = (0, "1000\n", "")
    responses[CMD_SUDO_CHECK] = (1, "", "password=secret a password is required")
    runner, report = _run(responses)

    sudo = next(item for item in report.checks if item.code == "sudo")
    assert sudo.status.value == "fail"
    assert "secret" not in sudo.summary
    assert "[REDACTED]" in sudo.summary
    assert report.ready_for_plan is False
    assert CMD_DF not in runner.calls


def test_low_disk_fails():
    _, report = _run(_ready_responses(**{CMD_DF: (0, _df(1024), "")}))

    disk = next(item for item in report.checks if item.code == "disk")
    assert disk.status.value == "fail"
    assert report.ready_for_plan is False


def test_unreadable_disk_output_fails():
    _, report = _run(_ready_responses(**{CMD_DF: (0, "not-df\n", "")}))

    assert next(item for item in report.checks if item.code == "disk").status.value == "fail"


def test_held_package_lock_fails():
    _, report = _run(_ready_responses(**{CMD_LOCKS: (0, _locks("held"), "")}))

    lock = next(item for item in report.checks if item.code == "locks")
    assert lock.status.value == "fail"
    assert "held" in lock.summary
    assert report.ready_for_plan is False


def test_missing_fuser_fails_closed():
    _, report = _run(_ready_responses(**{CMD_LOCKS: (0, _locks("unknown"), "")}))

    assert "fuser" in next(item.summary for item in report.checks if item.code == "locks")
    assert report.precheck_passed is False


def test_pending_dpkg_work_fails():
    _, report = _run(
        _ready_responses(**{CMD_DPKG_AUDIT: (1, "mysql-server: package needs reconfiguration\n", "")})
    )

    assert next(item for item in report.checks if item.code == "pending_packages").status.value == "fail"


def test_missing_backup_fails_and_is_not_called_verified():
    service = _service(backups=_Backups(BackupReadinessState.MISSING, "No backup path was provided"))
    _, report = _run(_ready_responses(), service=service)

    assert report.backup_state == BackupReadinessState.MISSING
    assert report.recovery_proven is False
    assert report.ready_for_plan is False
    assert "verified" not in next(item.summary for item in report.checks if item.code == "backup").lower()


def test_readable_backup_does_not_prove_restore():
    _, report = _run(_ready_responses())

    assert report.backup_state == BackupReadinessState.READABLE_UNVERIFIED
    assert report.recovery_proven is False
    assert report.ready_for_plan is True


def test_no_patch_available_is_not_ready():
    policy = _policy(INSTALLED, INSTALLED, ["http://archive.ubuntu.com/ubuntu"])
    responses = _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    responses.update(
        {
            CMD_ID: (0, "0\n", ""),
            CMD_DF: (0, _df(MIN_FREE_BYTES), ""),
            CMD_LOCKS: (0, _locks(), ""),
            CMD_DPKG_AUDIT: (0, "", ""),
        }
    )
    _, report = _run(responses)

    assert report.discovery.v1_patch_eligible is False
    assert report.ready_for_plan is False
    assert any(item.code == "candidate" and item.status.value == "fail" for item in report.checks)


def test_inactive_mysql_service_fails():
    from backend.services.mysql_patch.command_runner import CMD_SERVICE_MYSQL

    responses = _ready_responses()
    responses[CMD_SERVICE_MYSQL] = (3, "inactive\n", "")
    _, report = _run(responses)

    assert next(item for item in report.checks if item.code == "mysql_service").status.value == "fail"
    assert report.ready_for_plan is False


def test_unsafe_job_blocks_precheck():
    jobs = _Jobs(
        [PatchJobSnapshot(job_id="job-9", server_id="db-01", outcome=JobOutcome.INTERRUPTED)]
    )
    _, report = _run(_ready_responses(), service=_service(jobs=jobs))

    history = next(item for item in report.checks if item.code == "job_history")
    assert history.status.value == "fail"
    assert "job-9" in history.summary
    assert report.ready_for_plan is False


def test_completed_job_does_not_block():
    jobs = _Jobs([PatchJobSnapshot(job_id="job-1", server_id="db-01", outcome=JobOutcome.SUCCESS)])
    _, report = _run(_ready_responses(), service=_service(jobs=jobs))

    assert next(item for item in report.checks if item.code == "job_history").status.value == "pass"
    assert report.ready_for_plan is True


def test_missing_job_store_is_a_warning_not_a_clean_bill():
    service = PrecheckService(
        backups=_Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        )
    )
    _, report = _run(_ready_responses(), service=service)

    summary = next(item.summary for item in report.checks if item.code == "job_history")
    assert "not stored yet" in summary
    assert report.ready_for_plan is True


def test_local_backup_file_is_readable_but_not_a_proven_restore(tmp_path):
    backup = tmp_path / "employees.sql"
    backup.write_text("SELECT 1;\n", encoding="utf-8")

    state, summary = LocalBackupReadiness().assess(str(backup))

    assert state == BackupReadinessState.READABLE_UNVERIFIED
    assert "does not prove" in summary

    empty = tmp_path / "empty.sql"
    empty.write_text("", encoding="utf-8")
    empty_state, _empty_summary = LocalBackupReadiness().assess(str(empty))
    assert empty_state == BackupReadinessState.UNREADABLE

    missing_state, missing_summary = LocalBackupReadiness().assess(str(tmp_path / "missing.sql"))
    assert missing_state == BackupReadinessState.MISSING or missing_state == BackupReadinessState.UNREADABLE
    assert "verified" not in missing_summary.lower()
