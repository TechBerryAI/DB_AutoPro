"""Read-only Ubuntu APT discovery for MySQL server packages.

V1 accepts only Ubuntu's own archive (archive.ubuntu.com, security.ubuntu.com,
ports.ubuntu.com). Oracle MySQL APT (repo.mysql.com), mixed origins, and any
other host are rejected. No package is installed and no service is restarted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from backend.services.mysql_patch.command_runner import (
    CMD_APT_GET,
    CMD_DPKG,
    CMD_OS_RELEASE,
    CMD_SERVICE_MYSQL,
    CMD_SERVICE_MYSQLD,
    MAX_OUTPUT_CHARS,
    MUTATING_MARKERS,
    CommandResult,
    CommandRunner,
    apt_policy_command,
)
from backend.services.mysql_patch.models import (
    CheckStatus,
    DiscoveryCheck,
    EnvironmentReport,
    InstalledPackage,
    PackageManagerKind,
    PackageOrigin,
    UpgradeKind,
)
from backend.services.mysql_patch.redaction import redact_text

_UPSTREAM = re.compile(r"^(?:(\d+):)?(\d+)\.(\d+)\.(\d+)")
_UBUNTU_HOST_SUFFIXES = (
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "ports.ubuntu.com",
)
_ORACLE_HOST_SUFFIX = "repo.mysql.com"
_SERVER_PACKAGES = ("mysql-server-8.4", "mysql-server-8.0", "mysql-server")
_SERVICE_STATES = {"active", "inactive", "failed", "activating", "deactivating", "unknown"}
_IN_SERIES = {UpgradeKind.PACKAGE_REVISION, UpgradeKind.MAINTENANCE}


class MysqlVersion:
    """MySQL major.minor.patch numbers parsed from a package version."""

    def __init__(self, major: int, minor: int, patch: int) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def parse_mysql_version(package_version: str | None) -> MysqlVersion | None:
    """Parse the leading MySQL version. Epoch prefixes are ignored."""
    if not package_version or package_version in {"(none)", "none"}:
        return None
    match = _UPSTREAM.match(package_version.strip())
    if not match:
        return None
    return MysqlVersion(int(match.group(2)), int(match.group(3)), int(match.group(4)))


def classify_version_change(
    installed: MysqlVersion | None,
    candidate: MysqlVersion | None,
    installed_package_version: str | None,
    candidate_package_version: str | None,
) -> UpgradeKind:
    """Classify a candidate against the installed version.

    Same upstream version with a different Ubuntu package revision is a
    package revision. A higher patch number in the same major.minor series
    is maintenance. A different minor is a release-series change (8.0 to 8.4).
    A different major is a major-version change. Lower numbers are a downgrade.
    """
    if installed is None or candidate is None:
        return UpgradeKind.UNKNOWN
    if candidate.as_tuple() < installed.as_tuple():
        return UpgradeKind.DOWNGRADE
    if candidate.as_tuple() == installed.as_tuple():
        if (installed_package_version or "") == (candidate_package_version or ""):
            return UpgradeKind.NONE
        return UpgradeKind.PACKAGE_REVISION
    if candidate.major != installed.major:
        return UpgradeKind.MAJOR
    if candidate.minor != installed.minor:
        return UpgradeKind.RELEASE_SERIES
    return UpgradeKind.MAINTENANCE


def parse_os_release(text: str) -> dict[str, str]:
    """Parse KEY=value lines from /etc/os-release."""
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def parse_dpkg_query(text: str) -> list[InstalledPackage]:
    """Parse dpkg-query tab output. Only installed rows are returned."""
    packages: list[InstalledPackage] = []
    for line in text.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3:
            continue
        name, version, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not name or status != "installed":
            continue
        packages.append(InstalledPackage(name=name, version=version, status=status))
    return packages


def parse_apt_policy(text: str) -> tuple[str | None, str | None, list[str]]:
    """Return installed version, candidate version, and http(s) URIs."""
    installed: str | None = None
    candidate: str | None = None
    uris: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Installed:"):
            installed = _version_or_none(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("Candidate:"):
            candidate = _version_or_none(stripped.split(":", 1)[1].strip())
        else:
            for token in stripped.split():
                if token.startswith("http://") or token.startswith("https://"):
                    uris.append(token)
    return installed, candidate, uris


def classify_repository(uris: list[str]) -> PackageOrigin:
    """Map policy URIs to one origin. Any non-Ubuntu host fails closed."""
    if not uris:
        return PackageOrigin.UNKNOWN
    kinds: set[str] = set()
    for uri in uris:
        host = (urlparse(uri).hostname or "").lower()
        if host.endswith(_ORACLE_HOST_SUFFIX):
            kinds.add("oracle")
        elif host.endswith(_UBUNTU_HOST_SUFFIXES):
            kinds.add("ubuntu")
        else:
            kinds.add("other")
    if kinds == {"ubuntu"}:
        return PackageOrigin.UBUNTU_ARCHIVE
    if kinds == {"oracle"}:
        return PackageOrigin.ORACLE_MYSQL_APT
    if "ubuntu" in kinds and "oracle" in kinds:
        return PackageOrigin.MIXED
    if "oracle" in kinds:
        return PackageOrigin.ORACLE_MYSQL_APT
    return PackageOrigin.UNKNOWN


class UbuntuMysqlAptAdapter:
    """Discover MySQL on Ubuntu using fixed, read-only commands."""

    def discover(self, runner: CommandRunner, *, server_id: str) -> EnvironmentReport:
        report = EnvironmentReport(
            server_id=server_id,
            checked_at=datetime.now(timezone.utc),
        )
        os_result = self._run(runner, report, CMD_OS_RELEASE)
        if os_result is None:
            return report
        if os_result.exit_code != 0 or not os_result.stdout.strip():
            return self._fail(report, "os", "Operating system release could not be read")

        os_info = parse_os_release(os_result.stdout)
        report.os_id = os_info.get("ID") or None
        report.os_version = os_info.get("VERSION_ID") or None
        report.os_pretty_name = os_info.get("PRETTY_NAME") or None
        if (report.os_id or "").lower() != "ubuntu":
            report.checks.append(
                DiscoveryCheck(
                    code="os",
                    status=CheckStatus.FAIL,
                    summary="Only Ubuntu Server is supported in this version",
                )
            )
            report.stop_reason = "Unsupported operating system"
            return report
        report.checks.append(
            DiscoveryCheck(
                code="os",
                status=CheckStatus.PASS,
                summary=report.os_pretty_name or f"Ubuntu {report.os_version}",
            )
        )

        apt_result = self._run(runner, report, CMD_APT_GET)
        if apt_result is None:
            return report
        if apt_result.exit_code != 0 or not apt_result.stdout.strip():
            return self._fail(report, "package_manager", "APT is not available on this server")
        report.package_manager = PackageManagerKind.APT
        report.checks.append(
            DiscoveryCheck(code="package_manager", status=CheckStatus.PASS, summary="APT is available")
        )

        dpkg_result = self._run(runner, report, CMD_DPKG)
        if dpkg_result is None:
            return report
        if dpkg_result.exit_code not in {0, 1}:
            return self._fail(report, "mysql_packages", "MySQL package query failed")
        packages = parse_dpkg_query(dpkg_result.stdout)
        report.installed_packages = packages
        if any(item.name == "mysql-community-server" for item in packages):
            report.mysql_installed = True
            return self._fail(
                report,
                "mysql_packages",
                "Oracle MySQL packages are installed. This version only supports Ubuntu archive packages",
            )
        server_packages = [item for item in packages if item.name in _SERVER_PACKAGES]
        concrete = [item for item in server_packages if item.name != "mysql-server"]
        if len({item.name for item in concrete}) > 1:
            report.mysql_installed = True
            return self._fail(
                report,
                "mysql_packages",
                "More than one MySQL server series package is installed",
            )
        chosen = concrete[0] if concrete else (server_packages[0] if server_packages else None)
        if chosen is None:
            report.checks.append(
                DiscoveryCheck(
                    code="mysql_packages",
                    status=CheckStatus.FAIL,
                    summary="MySQL server packages are not installed",
                )
            )
            report.repository_origin = PackageOrigin.NOT_INSTALLED
            report.stop_reason = "MySQL server is not installed"
            return report

        report.mysql_installed = True
        report.mysql_server_package = chosen.name
        report.mysql_server_version = chosen.version
        installed_upstream = parse_mysql_version(chosen.version)
        report.mysql_upstream_version = str(installed_upstream) if installed_upstream else None
        report.checks.append(
            DiscoveryCheck(
                code="mysql_packages",
                status=CheckStatus.PASS,
                summary=f"{chosen.name} {chosen.version}",
            )
        )

        policy_command = apt_policy_command(chosen.name)
        policy_result = self._run(runner, report, policy_command)
        if policy_result is None:
            return report
        if policy_result.exit_code != 0:
            return self._fail(report, "repository", "Package repository policy could not be read")
        _policy_installed, candidate, uris = parse_apt_policy(policy_result.stdout)
        report.candidate_version = candidate
        report.repository_uris = uris
        report.repository_origin = classify_repository(uris)
        candidate_upstream = parse_mysql_version(candidate)
        report.candidate_upstream_version = str(candidate_upstream) if candidate_upstream else None
        if report.repository_origin != PackageOrigin.UBUNTU_ARCHIVE:
            return self._fail(
                report,
                "repository",
                "MySQL packages are not coming only from the Ubuntu archive",
            )
        report.checks.append(
            DiscoveryCheck(
                code="repository",
                status=CheckStatus.PASS,
                summary="Ubuntu archive is the only package origin",
            )
        )

        report.upgrade_kind = classify_version_change(
            installed_upstream,
            candidate_upstream,
            chosen.version,
            candidate,
        )
        self._apply_upgrade_kind(report)
        if report.upgrade_kind in {UpgradeKind.UNKNOWN, UpgradeKind.DOWNGRADE}:
            return report
        self._read_service(runner, report)
        return report

    def _read_service(self, runner: CommandRunner, report: EnvironmentReport) -> None:
        for name, command in (("mysql", CMD_SERVICE_MYSQL), ("mysqld", CMD_SERVICE_MYSQLD)):
            result = self._run(runner, report, command)
            if result is None:
                return
            state = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
            if state not in _SERVICE_STATES:
                continue
            report.service_name = name
            report.service_state = state
            status = CheckStatus.PASS if state == "active" else CheckStatus.WARNING
            summary = f"{name} service is {state}"
            report.checks.append(DiscoveryCheck(code="service", status=status, summary=summary))
            return
        report.checks.append(
            DiscoveryCheck(
                code="service",
                status=CheckStatus.WARNING,
                summary="MySQL service state could not be read",
            )
        )

    def _apply_upgrade_kind(self, report: EnvironmentReport) -> None:
        kind = report.upgrade_kind
        if kind == UpgradeKind.UNKNOWN:
            self._fail(report, "version", "Installed or candidate MySQL version could not be parsed")
            return
        if kind == UpgradeKind.DOWNGRADE:
            self._fail(report, "version", "Candidate version is older than the installed version")
            return
        if kind == UpgradeKind.NONE:
            report.environment_supported = True
            report.patch_available = False
            report.v1_patch_eligible = False
            report.checks.append(
                DiscoveryCheck(
                    code="version",
                    status=CheckStatus.PASS,
                    summary="Installed package matches the repository candidate",
                )
            )
            report.stop_reason = "No newer MySQL package is available from the Ubuntu archive"
            return
        if kind in {UpgradeKind.RELEASE_SERIES, UpgradeKind.MAJOR}:
            report.environment_supported = True
            report.patch_available = True
            report.v1_patch_eligible = False
            label = "release-series" if kind == UpgradeKind.RELEASE_SERIES else "major-version"
            report.stop_reason = (
                f"Candidate is a {label} change and is not applied as an ordinary package update"
            )
            report.checks.append(
                DiscoveryCheck(code="version", status=CheckStatus.FAIL, summary=report.stop_reason)
            )
            return
        if kind not in _IN_SERIES:
            self._fail(report, "version", "Version change is not an in-series package update")
            return
        report.environment_supported = True
        report.patch_available = True
        report.v1_patch_eligible = True
        label = (
            "Ubuntu package revision"
            if kind == UpgradeKind.PACKAGE_REVISION
            else "maintenance update inside the same MySQL series"
        )
        report.checks.append(
            DiscoveryCheck(
                code="version",
                status=CheckStatus.PASS,
                summary=f"{report.mysql_upstream_version} to {report.candidate_upstream_version}: {label}",
            )
        )

    def _run(
        self,
        runner: CommandRunner,
        report: EnvironmentReport,
        command: str,
    ) -> CommandResult | None:
        lowered = command.lower()
        if any(marker in lowered for marker in MUTATING_MARKERS):
            self._fail(report, "command", "Blocked a command that would change the server")
            return None
        report.commands_run.append(command)
        try:
            result = runner.run(command, timeout=20)
        except Exception as exc:
            message = redact_text(str(exc)) or "Remote command failed"
            report.transport_error = message
            report.stop_reason = "SSH command failed"
            report.environment_supported = False
            report.v1_patch_eligible = False
            report.checks.append(DiscoveryCheck(code="ssh", status=CheckStatus.FAIL, summary=message))
            return None
        combined = f"{result.stdout}\n{result.stderr}"
        if len(result.stdout) >= MAX_OUTPUT_CHARS or len(result.stderr) >= MAX_OUTPUT_CHARS:
            self._fail(report, "output", "Remote command output exceeded the size limit")
            return None
        report.output_excerpt = redact_text(combined[-4000:])
        return result

    def _fail(self, report: EnvironmentReport, code: str, summary: str) -> EnvironmentReport:
        report.environment_supported = False
        report.v1_patch_eligible = False
        report.patch_available = False
        report.stop_reason = summary
        report.checks.append(DiscoveryCheck(code=code, status=CheckStatus.FAIL, summary=summary))
        return report


def _version_or_none(value: str) -> str | None:
    if not value or value in {"(none)", "none"}:
        return None
    return value
