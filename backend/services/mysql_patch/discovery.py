"""Public read-only environment discovery for MySQL package patching."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import UbuntuMysqlAptAdapter, parse_os_release
from backend.services.mysql_patch.adapters.mysql_dnf_centos import CentosMysqlDnfAdapter
from backend.services.mysql_patch.command_runner import CMD_OS_RELEASE, MAX_OUTPUT_CHARS, CommandRunner
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import CheckStatus, DiscoveryCheck, EnvironmentReport
from backend.services.mysql_patch.redaction import redact_text

_RHEL_FAMILY = {"centos", "rhel", "rocky", "almalinux", "fedora"}


class EnvironmentDiscoveryService:
    """Choose the Ubuntu APT or CentOS DNF adapter. Does not install packages."""

    def __init__(
        self,
        adapter: UbuntuMysqlAptAdapter | None = None,
        centos: CentosMysqlDnfAdapter | None = None,
    ) -> None:
        self._ubuntu = adapter or UbuntuMysqlAptAdapter()
        self._centos = centos or CentosMysqlDnfAdapter()

    def discover(self, runner: CommandRunner, *, server_id: str) -> EnvironmentReport:
        """Discover one registered server. `server_id` is a label, not a shell argument."""
        cleaned = server_id.strip()
        if not cleaned:
            raise MysqlPatchSafetyError("Server id is required")
        os_id, probe = _probe_os(runner, cleaned)
        if probe is not None:
            return probe
        if os_id == "ubuntu":
            return self._ubuntu.discover(runner, server_id=cleaned)
        if os_id in _RHEL_FAMILY:
            return self._centos.discover(runner, server_id=cleaned)
        return _unsupported(cleaned)


def _probe_os(runner: CommandRunner, server_id: str) -> tuple[str | None, EnvironmentReport | None]:
    report = EnvironmentReport(server_id=server_id, checked_at=datetime.now(timezone.utc))
    report.commands_run.append(CMD_OS_RELEASE)
    try:
        result = runner.run(CMD_OS_RELEASE, timeout=20)
    except Exception as exc:
        message = redact_text(str(exc)) or "Remote command failed"
        report.transport_error = message
        report.stop_reason = "SSH command failed"
        report.checks.append(DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=message))
        return None, report
    if len(result.stdout) >= MAX_OUTPUT_CHARS or len(result.stderr) >= MAX_OUTPUT_CHARS:
        report.stop_reason = "Remote command output exceeded the size limit"
        report.checks.append(
            DiscoveryCheck(code="output", status=CheckStatus.FAIL, summary=report.stop_reason)
        )
        return None, report
    if result.exit_code != 0 or not result.stdout.strip():
        report.stop_reason = "Operating system release could not be read"
        report.checks.append(DiscoveryCheck(code="os", status=CheckStatus.FAIL, summary=report.stop_reason))
        return None, report
    os_id = (parse_os_release(result.stdout).get("ID") or "").lower()
    return os_id, None


def _unsupported(server_id: str) -> EnvironmentReport:
    report = EnvironmentReport(server_id=server_id, checked_at=datetime.now(timezone.utc))
    report.commands_run.append(CMD_OS_RELEASE)
    report.stop_reason = "Unsupported operating system"
    report.checks.append(
        DiscoveryCheck(
            code="os",
            status=CheckStatus.FAIL,
            summary="Only Ubuntu Server and CentOS Stream 10 are supported",
        )
    )
    return report
