"""Post-patch verification. Reads the server and does not change it.

Success is claimed only when the installed package matches the approved
target, the MySQL service is active, and SELECT 1 succeeded. An apt-get
exit code is ignored. This service does not downgrade, restore, or retry.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import parse_dpkg_query
from backend.services.mysql_patch.adapters.mysql_dnf_centos import parse_rpm_query
from backend.services.mysql_patch.command_runner import (
    CMD_DNF_CHECK,
    CMD_DPKG,
    CMD_DPKG_AUDIT,
    CMD_RPM_SERVER,
    CMD_SERVICE_MYSQL,
    CMD_SERVICE_MYSQLD,
    MAX_OUTPUT_CHARS,
    CommandResult,
    CommandRunner,
    is_allowed_command,
)
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import (
    CheckStatus,
    DiscoveryCheck,
    JobOutcome,
    PackagePreviewReport,
    PatchPlan,
    VerificationReport,
)
from backend.services.mysql_patch.redaction import redact_text

NEXT_STEPS = (
    "Do not downgrade MySQL or restore a backup automatically.",
    "Inspect the MySQL service and dpkg audit output on the server.",
    "Run a new precheck only after the server state is understood.",
)

SqlProbe = Callable[[], bool]
SqlStatementRunner = Callable[[str], bool]

_READONLY_START = {"select", "show", "explain"}
_SQL_FORBIDDEN = (" into outfile", " into dumpfile", " for update", " lock ", " insert ", " update ", " delete ", " drop ", " alter ", " create ", " grant ")


def normalize_readonly_sql(statement: str) -> str:
    """Accept one read-only statement. A trailing semicolon is removed."""
    text = " ".join(statement.strip().split())
    if text.endswith(";"):
        text = text[:-1].strip()
    if not text or ";" in text or "--" in text or "/*" in text:
        raise MysqlPatchSafetyError("SQL check must be one read-only statement")
    first = text.split(" ", 1)[0].lower()
    if first not in _READONLY_START:
        raise MysqlPatchSafetyError("SQL check must start with SELECT, SHOW, or EXPLAIN")
    lowered = f" {text.lower()} "
    if any(token in lowered for token in _SQL_FORBIDDEN):
        raise MysqlPatchSafetyError("SQL check is not read-only")
    return text


def assess_preview(preview: PackagePreviewReport) -> list[DiscoveryCheck]:
    """Confirm a preview was only a simulation. This does not verify the server."""
    checks: list[DiscoveryCheck] = []
    commands = " ".join(preview.commands_run).lower()
    installed = any(marker in commands for marker in ("dnf upgrade -y", "dnf install", "apt-get install", "apt-get upgrade"))
    if preview.simulation_only and preview.accepted and not installed:
        checks.append(
            DiscoveryCheck(
                code="preview_simulation",
                status=CheckStatus.PASS,
                summary="Preview ran a simulation only. The server was not changed and this is not a post-patch verification.",
            )
        )
    else:
        checks.append(
            DiscoveryCheck(
                code="preview_simulation",
                status=CheckStatus.FAIL,
                summary="Preview cannot be treated as a completed patch",
            )
        )
    return checks


class VerificationService:
    """Compare the live server with an approved plan."""

    def verify(
        self,
        runner: CommandRunner,
        plan: PatchPlan,
        *,
        sql_probe: SqlProbe | None = None,
        sql_statements: list[str] | None = None,
        sql_runner: SqlStatementRunner | None = None,
        apt_exit_code: int | None = None,
    ) -> VerificationReport:
        """Verify package, service, and optional SQL. `apt_exit_code` cannot grant success."""
        del apt_exit_code
        report = VerificationReport(
            server_id=plan.server_id,
            plan_id=plan.plan_id,
            checked_at=datetime.now(timezone.utc),
            target_version=plan.target_version,
        )
        try:
            self._read_package(runner, report, plan)
            self._read_service(runner, report, plan)
            self._read_audit(runner, report, plan)
            self._read_sql(report, sql_probe, sql_statements, sql_runner)
        except MysqlPatchSafetyError as exc:
            report.checks.append(DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=str(exc)))
            report.stop_reason = str(exc)
        except Exception as exc:
            message = redact_text(str(exc)) or "Verification command failed"
            report.checks.append(DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=message))
            report.stop_reason = message
        self._decide(report)
        return report

    def _read_package(self, runner: CommandRunner, report: VerificationReport, plan: PatchPlan) -> None:
        if plan.package_manager.value == "dnf":
            self._read_rpm(runner, report, plan)
            return
        result = self._run(runner, report, CMD_DPKG)
        if result.exit_code not in {0, 1}:
            report.checks.append(
                DiscoveryCheck(code="installed_version", status=CheckStatus.FAIL, summary="Package query failed")
            )
            return
        packages = parse_dpkg_query(result.stdout)
        chosen = next((item for item in packages if item.name == plan.server_package), None)
        report.installed_version = chosen.version if chosen else None
        report.target_reached = report.installed_version == plan.target_version
        if report.target_reached:
            summary = f"{plan.server_package} is {plan.target_version}"
            status = CheckStatus.PASS
        else:
            summary = (
                f"Installed version is {report.installed_version or 'missing'}; "
                f"approved target is {plan.target_version}"
            )
            status = CheckStatus.FAIL
        report.checks.append(DiscoveryCheck(code="installed_version", status=status, summary=summary))

    def _read_rpm(self, runner: CommandRunner, report: VerificationReport, plan: PatchPlan) -> None:
        result = self._run(runner, report, CMD_RPM_SERVER)
        rows = parse_rpm_query(result.stdout)
        chosen = next((item for item in rows if item[0] == plan.server_package), None)
        report.installed_version = chosen[2] if chosen else None
        report.target_reached = report.installed_version == plan.target_version
        if report.target_reached:
            summary = f"{plan.server_package} is {plan.target_version}"
            status = CheckStatus.PASS
        else:
            summary = (
                f"Installed version is {report.installed_version or 'missing'}; "
                f"approved target is {plan.target_version}"
            )
            status = CheckStatus.FAIL
        report.checks.append(DiscoveryCheck(code="installed_version", status=status, summary=summary))

    def _read_service(self, runner: CommandRunner, report: VerificationReport, plan: PatchPlan) -> None:
        order = (("mysql", CMD_SERVICE_MYSQL), ("mysqld", CMD_SERVICE_MYSQLD))
        if plan.package_manager.value == "dnf":
            order = (("mysqld", CMD_SERVICE_MYSQLD), ("mysql", CMD_SERVICE_MYSQL))
        for name, command in order:
            result = self._run(runner, report, command)
            state = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
            if state not in {"active", "inactive", "failed", "activating", "deactivating"}:
                continue
            report.service_name = name
            report.service_state = state
            report.service_active = state == "active"
            status = CheckStatus.PASS if report.service_active else CheckStatus.FAIL
            report.checks.append(
                DiscoveryCheck(code="service", status=status, summary=f"{name} service is {state}")
            )
            return
        report.checks.append(
            DiscoveryCheck(
                code="service",
                status=CheckStatus.FAIL,
                summary="MySQL service state could not be read",
            )
        )

    def _read_audit(self, runner: CommandRunner, report: VerificationReport, plan: PatchPlan) -> None:
        command = CMD_DNF_CHECK if plan.package_manager.value == "dnf" else CMD_DPKG_AUDIT
        result = self._run(runner, report, command)
        pending = result.stdout.strip()
        report.package_manager_clean = result.exit_code == 0 and not pending
        label = "DNF" if plan.package_manager.value == "dnf" else "dpkg audit"
        if report.package_manager_clean:
            report.checks.append(
                DiscoveryCheck(
                    code="package_manager",
                    status=CheckStatus.PASS,
                    summary=f"{label} reported no unfinished package work",
                )
            )
            return
        detail = redact_text(pending.splitlines()[0]) if pending else f"{label} failed"
        report.checks.append(
            DiscoveryCheck(
                code="package_manager",
                status=CheckStatus.FAIL,
                summary=f"Package manager reported a problem ({detail})",
            )
        )

    def _read_sql(
        self,
        report: VerificationReport,
        sql_probe: SqlProbe | None,
        sql_statements: list[str] | None,
        sql_runner: SqlStatementRunner | None,
    ) -> None:
        if sql_statements:
            self._read_sql_statements(report, sql_statements, sql_runner)
            return
        if sql_probe is None:
            report.sql_checked = False
            report.sql_ok = False
            report.checks.append(
                DiscoveryCheck(
                    code="sql",
                    status=CheckStatus.SKIPPED,
                    summary="SELECT 1 was not run because database credentials were not supplied",
                )
            )
            return
        report.sql_checked = True
        try:
            report.sql_ok = bool(sql_probe())
        except Exception as exc:
            report.sql_ok = False
            message = redact_text(str(exc)) or "SELECT 1 failed"
            report.checks.append(DiscoveryCheck(code="sql", status=CheckStatus.FAIL, summary=message))
            return
        status = CheckStatus.PASS if report.sql_ok else CheckStatus.FAIL
        summary = "SELECT 1 succeeded" if report.sql_ok else "SELECT 1 did not succeed"
        report.checks.append(DiscoveryCheck(code="sql", status=status, summary=summary))

    def _read_sql_statements(
        self,
        report: VerificationReport,
        statements: list[str],
        sql_runner: SqlStatementRunner | None,
    ) -> None:
        try:
            normalized = [normalize_readonly_sql(item) for item in statements]
        except MysqlPatchSafetyError as exc:
            report.sql_checked = False
            report.sql_ok = False
            report.checks.append(DiscoveryCheck(code="sql", status=CheckStatus.FAIL, summary=str(exc)))
            return
        if sql_runner is None:
            report.sql_checked = False
            report.sql_ok = False
            report.checks.append(
                DiscoveryCheck(
                    code="sql",
                    status=CheckStatus.FAIL,
                    summary="Read-only SQL checks were requested but did not run",
                )
            )
            return
        report.sql_checked = True
        for statement in normalized:
            try:
                ok = bool(sql_runner(statement))
            except Exception as exc:
                report.sql_ok = False
                message = redact_text(str(exc)) or "Read-only SQL check failed"
                report.checks.append(DiscoveryCheck(code="sql", status=CheckStatus.FAIL, summary=message))
                return
            if not ok:
                report.sql_ok = False
                report.checks.append(
                    DiscoveryCheck(code="sql", status=CheckStatus.FAIL, summary=f"Read-only SQL check failed: {statement}")
                )
                return
        report.sql_ok = True
        report.checks.append(
            DiscoveryCheck(
                code="sql",
                status=CheckStatus.PASS,
                summary=f"{len(normalized)} read-only SQL check(s) succeeded",
            )
        )

    def _decide(self, report: VerificationReport) -> None:
        failed = [item for item in report.checks if item.status == CheckStatus.FAIL]
        sql_blocks = report.sql_checked and not report.sql_ok
        if report.target_reached and report.service_active and report.package_manager_clean and not sql_blocks:
            if report.sql_checked and report.sql_ok:
                report.outcome = JobOutcome.SUCCESS
                report.stop_reason = None
                report.recommended_next_steps = []
                return
            report.outcome = JobOutcome.VERIFICATION_FAILED
            report.stop_reason = "Package and service match the plan, but SELECT 1 was not confirmed"
        elif failed:
            report.outcome = JobOutcome.VERIFICATION_FAILED
            report.stop_reason = failed[0].summary
        else:
            report.outcome = JobOutcome.VERIFICATION_FAILED
            report.stop_reason = report.stop_reason or "Verification did not pass"
        report.recommended_next_steps = list(NEXT_STEPS)

    def _run(self, runner: CommandRunner, report: VerificationReport, command: str) -> CommandResult:
        if not is_allowed_command(command):
            raise MysqlPatchSafetyError("Blocked a command that would change the server")
        report.commands_run.append(command)
        result = runner.run(command, timeout=20)
        if len(result.stdout) >= MAX_OUTPUT_CHARS or len(result.stderr) >= MAX_OUTPUT_CHARS:
            raise MysqlPatchSafetyError("Remote command output exceeded the size limit")
        return result
