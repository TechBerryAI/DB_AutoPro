"""Read-only APT simulation of one pinned MySQL package update.

This does not install, download, or upgrade the operating system. A preview is
accepted only when every simulated change stays inside the Ubuntu MySQL
package set and nothing is removed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import parse_mysql_version
from backend.services.mysql_patch.command_runner import (
    MAX_OUTPUT_CHARS,
    CommandResult,
    CommandRunner,
    apt_simulate_command,
    is_allowed_command,
)
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import (
    CheckStatus,
    DiscoveryCheck,
    PackageChange,
    PackagePreviewReport,
)
from backend.services.mysql_patch.precheck import PrecheckService
from backend.services.mysql_patch.redaction import redact_text

# Packages that may appear in an in-series Ubuntu MySQL update.
RELATED_PACKAGES = frozenset(
    {
        "mysql-server",
        "mysql-server-8.0",
        "mysql-server-8.4",
        "mysql-client",
        "mysql-client-8.0",
        "mysql-client-8.4",
        "mysql-client-core-8.0",
        "mysql-client-core-8.4",
        "mysql-server-core-8.0",
        "mysql-server-core-8.4",
        "mysql-common",
        "libmysqlclient21",
        "libmysqlclient24",
    }
)

_INST = re.compile(r"^Inst (?P<name>\S+)(?: \[(?P<old>[^\]]+)\])? \((?P<new>\S+)")
_REMV = re.compile(r"^Remv (?P<name>\S+)(?: \[(?P<old>[^\]]+)\])?")
_SUMMARY = re.compile(r"(?P<up>\d+) upgraded, (?P<new>\d+) newly installed, (?P<rem>\d+) to remove")


class PackagePreviewService:
    """Build a package-change preview after prechecks have passed."""

    def __init__(self, precheck: PrecheckService | None = None) -> None:
        self._precheck = precheck or PrecheckService()

    def preview(
        self,
        runner: CommandRunner,
        *,
        server_id: str,
        backup_path: str | None = None,
    ) -> PackagePreviewReport:
        """Return an accepted preview or a fail-closed stop reason."""
        precheck = self._precheck.precheck(runner, server_id=server_id, backup_path=backup_path)
        report = PackagePreviewReport(
            server_id=precheck.server_id,
            checked_at=datetime.now(timezone.utc),
            precheck=precheck,
            commands_run=list(precheck.commands_run),
        )
        if not precheck.ready_for_plan:
            report.stop_reason = precheck.stop_reason or "Prechecks did not pass"
            report.checks.append(
                DiscoveryCheck(
                    code="package_preview",
                    status=CheckStatus.SKIPPED,
                    summary="Package simulation was not run because prechecks are not ready",
                )
            )
            return report

        package = precheck.discovery.mysql_server_package
        version = precheck.discovery.candidate_version
        if not package or not version:
            return self._fail(report, "MySQL candidate package is missing from discovery")
        try:
            command = apt_simulate_command(package, version)
        except MysqlPatchSafetyError as exc:
            return self._fail(report, str(exc))

        try:
            result = self._run(runner, report, command)
        except MysqlPatchSafetyError as exc:
            return self._fail(report, str(exc))
        except Exception as exc:
            return self._fail(report, redact_text(str(exc)) or "Package simulation failed")

        report.output_excerpt = redact_text(f"{result.stdout}\n{result.stderr}"[-4000:])
        if result.exit_code != 0:
            detail = redact_text(result.stderr.strip().splitlines()[0]) if result.stderr.strip() else "apt-get simulation failed"
            return self._fail(report, f"Package simulation failed ({detail})")

        changes, problem = parse_apt_simulation(result.stdout)
        report.changes = changes
        if problem:
            return self._fail(report, problem)
        problem = assess_changes(
            changes,
            result.stdout,
            target_package=package,
            target_version=version,
        )
        if problem:
            return self._fail(report, problem)
        report.accepted = True
        report.checks.append(
            DiscoveryCheck(
                code="package_preview",
                status=CheckStatus.PASS,
                summary=f"Simulation changes only MySQL-related packages and removes nothing ({len(changes)} change(s))",
            )
        )
        return report

    def _run(self, runner: CommandRunner, report: PackagePreviewReport, command: str) -> CommandResult:
        if not is_allowed_command(command):
            raise MysqlPatchSafetyError("Blocked a command that is not a read-only simulation")
        report.commands_run.append(command)
        result = runner.run(command, timeout=60)
        if len(result.stdout) >= MAX_OUTPUT_CHARS or len(result.stderr) >= MAX_OUTPUT_CHARS:
            raise MysqlPatchSafetyError("Remote command output exceeded the size limit")
        return result

    def _fail(self, report: PackagePreviewReport, summary: str) -> PackagePreviewReport:
        report.accepted = False
        report.stop_reason = summary
        report.checks.append(DiscoveryCheck(code="package_preview", status=CheckStatus.FAIL, summary=summary))
        return report


def parse_apt_simulation(text: str) -> tuple[list[PackageChange], str | None]:
    """Parse `Inst` and `Remv` lines. A missing summary fails closed."""
    summary = _SUMMARY.search(text)
    if summary is None:
        return [], "Package simulation summary could not be read"
    changes: list[PackageChange] = []
    for line in text.splitlines():
        stripped = line.strip()
        installed = _INST.match(stripped)
        if installed:
            old = installed.group("old")
            changes.append(
                PackageChange(
                    name=installed.group("name"),
                    action="upgrade" if old else "install",
                    current_version=old,
                    target_version=installed.group("new"),
                )
            )
            continue
        removed = _REMV.match(stripped)
        if removed:
            changes.append(
                PackageChange(
                    name=removed.group("name"),
                    action="remove",
                    current_version=removed.group("old"),
                    target_version=None,
                )
            )
    expected = int(summary.group("up")) + int(summary.group("new")) + int(summary.group("rem"))
    if expected != len(changes):
        return changes, "Package simulation lines do not match the summary counts"
    if int(summary.group("rem")) > 0:
        return changes, "Package simulation would remove one or more packages"
    return changes, None


def assess_changes(
    changes: list[PackageChange],
    text: str,
    *,
    target_package: str,
    target_version: str,
) -> str | None:
    """Reject unrelated changes, removals, and a different candidate version."""
    if "essential packages will be removed" in text.lower():
        return "Package simulation would remove essential packages"
    if not changes:
        return "Package simulation did not propose a package change"
    expected = parse_mysql_version(target_version)
    target_change = next((item for item in changes if item.name == target_package), None)
    if target_change is None or target_change.action == "remove":
        return f"{target_package} was not included as an install or upgrade"
    if target_change.target_version != target_version:
        return "Simulated target version does not match the discovered candidate"
    for change in changes:
        if change.action == "remove":
            return f"Package simulation would remove {change.name}"
        if change.name not in RELATED_PACKAGES:
            return f"Unrelated package {change.name} would be updated"
        if _series_conflict(change.name, change.target_version, expected):
            return f"{change.name} would move outside the {expected} release series"
    return None


def _series_conflict(name: str, version: str | None, expected: object) -> bool:
    if expected is None or version is None:
        return True
    series = (expected.major, expected.minor)  # type: ignore[attr-defined]
    if "-8.0" in name and series != (8, 0):
        return True
    if "-8.4" in name and series != (8, 4):
        return True
    parsed = parse_mysql_version(version)
    if parsed is None:
        return name != "mysql-common"
    return (parsed.major, parsed.minor) != series
