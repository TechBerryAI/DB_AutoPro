"""Read-only prechecks before a MySQL patch plan.

These checks do not install packages, change repositories, or restart services.
Package transaction preview is a later phase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from backend.services.mysql_patch.command_runner import (
    CMD_DF,
    CMD_DF_DNF,
    CMD_DNF_CHECK,
    CMD_DNF_LOCKS,
    CMD_DPKG_AUDIT,
    CMD_ID,
    CMD_LOCKS,
    CMD_SUDO_CHECK,
    MAX_OUTPUT_CHARS,
    MUTATING_MARKERS,
    CommandResult,
    CommandRunner,
    allowed_commands,
)
from backend.services.mysql_patch.discovery import EnvironmentDiscoveryService
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import (
    BackupReadinessState,
    CheckStatus,
    DiscoveryCheck,
    EnvironmentReport,
    JobOutcome,
    PackageManagerKind,
    PatchJobSnapshot,
    PrecheckReport,
)
from backend.services.mysql_patch.redaction import redact_text

# Application safety floor for the filesystems that hold the OS and APT cache.
# This is not a MySQL-published minimum.
MIN_FREE_BYTES = 1_073_741_824

_UNSAFE_OUTCOMES = {
    JobOutcome.FAILED,
    JobOutcome.PARTIALLY_COMPLETED,
    JobOutcome.VERIFICATION_FAILED,
    JobOutcome.INTERRUPTED,
    JobOutcome.UNKNOWN,
}
_REMOTE_SAFETY = ("sudo", "disk", "locks", "pending_packages")


class BackupAssessor(Protocol):
    """Local backup readability check."""

    def assess(self, backup_path: str | None) -> tuple[BackupReadinessState, str]:
        """Return a readiness state and a summary that must not claim a proven restore."""


class JobHistorySource(Protocol):
    """Supplies known patch jobs for one server."""

    def jobs_for(self, server_id: str) -> list[PatchJobSnapshot]:
        """Return jobs already recorded for this server."""


class LocalBackupReadiness:
    """Uses BackupService.validate_backup. A readable file is not a proven restore."""

    def assess(self, backup_path: str | None) -> tuple[BackupReadinessState, str]:
        if backup_path is None or not str(backup_path).strip():
            return (
                BackupReadinessState.MISSING,
                "No backup path was provided",
            )
        from backend.services.mysql_patch.remote_backup import is_remote_linux_path

        if is_remote_linux_path(str(backup_path)):
            return (
                BackupReadinessState.UNREADABLE,
                "A remote Linux path is not opened as a Windows file. Use Browse Remote Backups.",
            )
        from backend.services.backup_service import BackupService

        try:
            readable = BackupService().validate_backup(str(backup_path).strip())
        except Exception as exc:
            message = redact_text(str(exc)) or "Backup could not be read"
            return BackupReadinessState.UNREADABLE, message
        if not readable:
            return BackupReadinessState.UNREADABLE, "Backup file failed the existing readability check"
        return (
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. "
            "This does not prove the backup can be restored.",
        )


class PreparedBackupReadiness:
    """Reuse a remote check that already ran. It does not open a local file."""

    def __init__(self, path: str, state: BackupReadinessState, summary: str) -> None:
        self.path = path
        self.state = state
        self.summary = summary

    def assess(self, backup_path: str | None) -> tuple[BackupReadinessState, str]:
        if str(backup_path or "").strip() != self.path:
            return (
                BackupReadinessState.UNREADABLE,
                "The remote backup selection changed before this check finished",
            )
        return self.state, self.summary


class UnavailableJobHistory:
    """Placeholder until patch jobs are stored."""

    def jobs_for(self, server_id: str) -> list[PatchJobSnapshot]:
        del server_id
        return []


class PrecheckService:
    """Run discovery, then the extra read-only safety checks."""

    def __init__(
        self,
        discovery: EnvironmentDiscoveryService | None = None,
        backups: BackupAssessor | None = None,
        jobs: JobHistorySource | None = None,
    ) -> None:
        self._discovery = discovery or EnvironmentDiscoveryService()
        self._backups = backups or LocalBackupReadiness()
        self._jobs = jobs
        self._jobs_available = jobs is not None

    def use_backup_assessor(self, backups: BackupAssessor) -> None:
        """Switch between the Windows file check and a finished remote check."""
        self._backups = backups

    def precheck(
        self,
        runner: CommandRunner,
        *,
        server_id: str,
        backup_path: str | None = None,
    ) -> PrecheckReport:
        """Fail closed. Does not change the remote server."""
        discovery = self._discovery.discover(runner, server_id=server_id)
        checks: list[DiscoveryCheck] = []
        commands = list(discovery.commands_run)
        report = PrecheckReport(
            server_id=discovery.server_id,
            checked_at=datetime.now(timezone.utc),
            discovery=discovery,
            commands_run=commands,
            host_key_verified=False,
            recovery_proven=False,
        )

        if discovery.transport_error:
            checks.append(
                DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=discovery.transport_error)
            )
            self._skip_remote(checks)
        elif not discovery.environment_supported:
            checks.append(
                DiscoveryCheck(
                    code="environment",
                    status=CheckStatus.FAIL,
                    summary=discovery.stop_reason or "Environment is not supported",
                )
            )
            self._skip_remote(checks)
        else:
            self._remote_safety(runner, report, checks, discovery)
            self._candidate_check(checks, discovery)
            self._service_check(checks, discovery)
            if discovery.transport_error is None and report.stop_reason is None:
                pinned = bool(getattr(runner, "host_key_verified", False))
                report.host_key_verified = pinned
                if pinned:
                    checks.append(
                        DiscoveryCheck(
                            code="host_key",
                            status=CheckStatus.PASS,
                            summary="SSH host key matched the trusted key for this server.",
                        )
                    )
                else:
                    checks.append(
                        DiscoveryCheck(
                            code="host_key",
                            status=CheckStatus.WARNING,
                            summary=(
                                "SSH responded, but this check cannot confirm a pinned host key. "
                                "Enter the fingerprint you read on the server before trusting it."
                            ),
                        )
                    )

        self._backup_check(checks, report, backup_path)
        self._job_check(checks, discovery.server_id)
        self._finish(report, checks)
        return report

    def _remote_safety(
        self,
        runner: CommandRunner,
        report: PrecheckReport,
        checks: list[DiscoveryCheck],
        discovery: EnvironmentReport,
    ) -> None:
        try:
            identity = self._run(runner, report, CMD_ID)
            if identity.exit_code != 0 or not identity.stdout.strip().isdigit():
                checks.append(
                    DiscoveryCheck(
                        code="sudo",
                        status=CheckStatus.FAIL,
                        summary="Remote user id could not be read",
                    )
                )
                return
            if identity.stdout.strip() == "0":
                checks.append(
                    DiscoveryCheck(
                        code="sudo",
                        status=CheckStatus.PASS,
                        summary="Remote user is root",
                    )
                )
            else:
                sudo = self._run(runner, report, CMD_SUDO_CHECK)
                if sudo.exit_code != 0:
                    detail = redact_text(sudo.stderr.strip()) or "passwordless sudo was refused"
                    checks.append(
                        DiscoveryCheck(
                            code="sudo",
                            status=CheckStatus.FAIL,
                            summary=f"Passwordless sudo is not available ({detail})",
                        )
                    )
                    return
                checks.append(
                    DiscoveryCheck(
                        code="sudo",
                        status=CheckStatus.PASS,
                        summary="Passwordless sudo is available",
                    )
                )

            disk_command, lock_command, audit_command, disk_summary, lock_summary, audit_summary = (
                _safety_commands(discovery)
            )
            disk = self._run(runner, report, disk_command)
            disk_problem = _disk_problem(disk)
            checks.append(
                DiscoveryCheck(
                    code="disk",
                    status=CheckStatus.FAIL if disk_problem else CheckStatus.PASS,
                    summary=disk_problem or disk_summary,
                )
            )

            locks = self._run(runner, report, lock_command)
            lock_problem = _lock_problem(locks)
            checks.append(
                DiscoveryCheck(
                    code="locks",
                    status=CheckStatus.FAIL if lock_problem else CheckStatus.PASS,
                    summary=lock_problem or lock_summary,
                )
            )

            audit = self._run(runner, report, audit_command)
            pending = audit.stdout.strip()
            if audit.exit_code != 0 or pending:
                detail = redact_text(pending.splitlines()[0]) if pending else "package check failed"
                checks.append(
                    DiscoveryCheck(
                        code="pending_packages",
                        status=CheckStatus.FAIL,
                        summary=f"Package manager has unfinished work ({detail})",
                    )
                )
            else:
                checks.append(
                    DiscoveryCheck(
                        code="pending_packages",
                        status=CheckStatus.PASS,
                        summary=audit_summary,
                    )
                )
        except MysqlPatchSafetyError as exc:
            discovery.transport_error = str(exc)
            checks.append(DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=str(exc)))
        except Exception as exc:
            message = redact_text(str(exc)) or "Remote command failed"
            discovery.transport_error = message
            checks.append(DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=message))

    def _candidate_check(self, checks: list[DiscoveryCheck], discovery: EnvironmentReport) -> None:
        if discovery.v1_patch_eligible:
            checks.append(
                DiscoveryCheck(
                    code="candidate",
                    status=CheckStatus.PASS,
                    summary=(
                        f"{discovery.mysql_upstream_version} can move to "
                        f"{discovery.candidate_upstream_version} inside the same series"
                    ),
                )
            )
            checks.append(
                DiscoveryCheck(
                    code="maintenance_window",
                    status=CheckStatus.WARNING,
                    summary=_maintenance_window(discovery),
                )
            )
            return
        checks.append(
            DiscoveryCheck(
                code="candidate",
                status=CheckStatus.FAIL,
                summary=discovery.stop_reason or "No supported in-series MySQL package update is available",
            )
        )

    def _service_check(self, checks: list[DiscoveryCheck], discovery: EnvironmentReport) -> None:
        if discovery.service_state == "active":
            checks.append(
                DiscoveryCheck(
                    code="mysql_service",
                    status=CheckStatus.PASS,
                    summary=f"{discovery.service_name} service is active",
                )
            )
            return
        state = discovery.service_state or "unknown"
        checks.append(
            DiscoveryCheck(
                code="mysql_service",
                status=CheckStatus.FAIL,
                summary=f"MySQL service is {state}",
            )
        )

    def _backup_check(
        self,
        checks: list[DiscoveryCheck],
        report: PrecheckReport,
        backup_path: str | None,
    ) -> None:
        state, summary = self._backups.assess(backup_path)
        report.backup_state = state
        report.recovery_proven = False
        if state == BackupReadinessState.READABLE_UNVERIFIED:
            status = CheckStatus.WARNING
        else:
            status = CheckStatus.FAIL
        checks.append(DiscoveryCheck(code="backup", status=status, summary=summary))

    def _job_check(self, checks: list[DiscoveryCheck], server_id: str) -> None:
        if not self._jobs_available or self._jobs is None:
            checks.append(
                DiscoveryCheck(
                    code="job_history",
                    status=CheckStatus.WARNING,
                    summary="Patch job history is not stored yet, so earlier failed jobs on this server cannot be seen",
                )
            )
            return
        unsafe = [
            job
            for job in self._jobs.jobs_for(server_id)
            if job.server_id == server_id and job.outcome in _UNSAFE_OUTCOMES
        ]
        if unsafe:
            job = unsafe[0]
            checks.append(
                DiscoveryCheck(
                    code="job_history",
                    status=CheckStatus.FAIL,
                    summary=f"Server has an unsafe patch job {job.job_id} ({job.outcome.value})",
                )
            )
            return
        checks.append(
            DiscoveryCheck(
                code="job_history",
                status=CheckStatus.PASS,
                summary="No unsafe patch job is recorded for this server",
            )
        )

    def _finish(self, report: PrecheckReport, checks: list[DiscoveryCheck]) -> None:
        report.checks = checks
        failed = [item for item in checks if item.status == CheckStatus.FAIL]
        report.precheck_passed = not failed
        report.service_interruption_expected = report.discovery.v1_patch_eligible
        report.ready_for_plan = (
            report.precheck_passed
            and report.discovery.v1_patch_eligible
            and report.backup_state == BackupReadinessState.READABLE_UNVERIFIED
        )
        if failed:
            report.stop_reason = failed[0].summary
        else:
            report.stop_reason = None

    def _skip_remote(self, checks: list[DiscoveryCheck]) -> None:
        for code in _REMOTE_SAFETY:
            checks.append(
                DiscoveryCheck(
                    code=code,
                    status=CheckStatus.SKIPPED,
                    summary="Skipped because the server environment is not supported",
                )
            )

    def _run(self, runner: CommandRunner, report: PrecheckReport, command: str) -> CommandResult:
        lowered = command.lower()
        if command not in allowed_commands() or any(marker in lowered for marker in MUTATING_MARKERS):
            raise MysqlPatchSafetyError("Blocked a command that would change the server")
        report.commands_run.append(command)
        result = runner.run(command, timeout=20)
        if len(result.stdout) >= MAX_OUTPUT_CHARS or len(result.stderr) >= MAX_OUTPUT_CHARS:
            raise MysqlPatchSafetyError("Remote command output exceeded the size limit")
        return result


def _safety_commands(discovery: EnvironmentReport) -> tuple[str, str, str, str, str, str]:
    if discovery.package_manager == PackageManagerKind.DNF:
        return (
            CMD_DF_DNF,
            CMD_DNF_LOCKS,
            CMD_DNF_CHECK,
            "Filesystems for /, /var, and the DNF cache have at least 1 GiB free",
            "DNF and RPM locks are free",
            "DNF reported no unfinished package work",
        )
    return (
        CMD_DF,
        CMD_LOCKS,
        CMD_DPKG_AUDIT,
        "Filesystems for /, /var, and the APT cache have at least 1 GiB free",
        "APT and dpkg locks are free",
        "dpkg audit reported no unfinished package work",
    )


def _maintenance_window(discovery: EnvironmentReport) -> str:
    if discovery.package_manager == PackageManagerKind.DNF:
        return (
            "A MySQL package update on CentOS commonly restarts the database service. "
            "Confirm a maintenance window before any later execution."
        )
    return (
        "A MySQL package update on Ubuntu commonly restarts the database service. "
        "Confirm a maintenance window before any later execution."
    )


def _disk_problem(result: CommandResult) -> str | None:
    if result.exit_code != 0:
        return "Disk space could not be read"
    rows = _parse_df(result.stdout)
    if not rows:
        return "Disk space output could not be parsed"
    short = [mount for mount, free in rows if free < MIN_FREE_BYTES]
    if short:
        return f"Less than 1 GiB free on {', '.join(short)}"
    return None


def _parse_df(text: str) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            available = int(parts[3])
        except ValueError:
            continue
        rows.append((parts[-1], available))
    return rows


def _lock_problem(result: CommandResult) -> str | None:
    if result.exit_code != 0 and not result.stdout.strip():
        return "Package manager locks could not be read"
    saw_free = False
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        state, path = parts
        if state == "held":
            return f"Package manager lock is held: {path}"
        if state == "unknown":
            return "Package manager locks could not be checked because fuser is missing"
        if state == "free":
            saw_free = True
    if not saw_free:
        return "Package manager lock status could not be read"
    return None
