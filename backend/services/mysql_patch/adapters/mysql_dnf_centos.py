"""Read-only CentOS Stream 10 DNF discovery for MySQL server packages.

This adapter does not install packages, refresh caches, or restart services.
Only CentOS Stream 10 is accepted. Other RHEL-family systems stop here and
are not sent to the Ubuntu APT adapter.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import (
    classify_version_change,
    parse_mysql_version,
    parse_os_release,
)
from backend.services.mysql_patch.command_runner import (
    CMD_DNF,
    CMD_DNF_HISTORY_LIST,
    CMD_DNF_REPOINFO,
    CMD_DNF_REPOQUERY,
    CMD_MACHINE_ID,
    CMD_OS_RELEASE,
    CMD_RPM_SERVER,
    CMD_SERVICE_MYSQL,
    CMD_SERVICE_MYSQLD,
    MAX_OUTPUT_CHARS,
    MUTATING_MARKERS,
    CommandResult,
    CommandRunner,
    dnf_history_info_command,
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

_IN_SERIES = {UpgradeKind.PACKAGE_REVISION, UpgradeKind.MAINTENANCE}
_SERVICE_STATES = {"active", "inactive", "failed", "activating", "deactivating", "unknown"}
_RHEL_FAMILY = {"centos", "rhel", "rocky", "almalinux", "fedora"}
_CENTOS_HOST_SUFFIXES = ("centos.org",)
_FORBIDDEN_HOST_SUFFIXES = ("repo.mysql.com", "mysql.com")
_CENTOS_REPO_IDS = {"appstream", "baseos", "extras-common"}
_SUPPLYING_ACTIONS = {"install", "upgrade", "reinstall"}
_PREVIOUS_ACTIONS = {"upgraded", "removed", "obsoleted", "obsolete", "downgraded"}
_MAX_HISTORY_TRANSACTIONS = 10
_HISTORY_ACTIONS = _SUPPLYING_ACTIONS | _PREVIOUS_ACTIONS | {"downgrade"}
_NOT_INSTALLED = re.compile(r"package (\S+) is not installed", re.IGNORECASE)
_SERVER_PACKAGE = "mysql8.4-server"
_RPM_PACKAGES = ("mysql8.4", "mysql8.4-common", "mysql8.4-errmsg", "mysql8.4-server")


class CentosMysqlDnfAdapter:
    """Discover MySQL 8.4 on CentOS Stream 10 from RPM and DNF metadata."""

    def discover(self, runner: CommandRunner, *, server_id: str) -> EnvironmentReport:
        report = EnvironmentReport(server_id=server_id, checked_at=datetime.now(timezone.utc))
        os_result = self._run(runner, report, CMD_OS_RELEASE)
        if os_result is None:
            return report
        if os_result.exit_code != 0 or not os_result.stdout.strip():
            return self._fail(report, "os", "Operating system release could not be read")

        os_info = parse_os_release(os_result.stdout)
        report.os_id = os_info.get("ID") or None
        report.os_version = os_info.get("VERSION_ID") or None
        report.os_pretty_name = os_info.get("PRETTY_NAME") or None
        if not _stream_10(os_info):
            summary = "Only CentOS Stream 10 is supported by the DNF adapter"
            if (report.os_id or "").lower() not in _RHEL_FAMILY:
                summary = "Only Ubuntu Server and CentOS Stream 10 are supported"
            report.checks.append(DiscoveryCheck(code="os", status=CheckStatus.FAIL, summary=summary))
            report.stop_reason = "Unsupported operating system"
            return report
        report.checks.append(
            DiscoveryCheck(
                code="os",
                status=CheckStatus.PASS,
                summary=report.os_pretty_name or "CentOS Stream 10",
            )
        )

        dnf_result = self._run(runner, report, CMD_DNF)
        if dnf_result is None:
            return report
        if dnf_result.exit_code != 0 or not dnf_result.stdout.strip():
            return self._fail(report, "package_manager", "DNF is not available on this server")
        report.package_manager = PackageManagerKind.DNF
        report.checks.append(
            DiscoveryCheck(code="package_manager", status=CheckStatus.PASS, summary="DNF is available")
        )

        rpm_result = self._run(runner, report, CMD_RPM_SERVER)
        if rpm_result is None:
            return report
        rows, problem = interpret_rpm_query(rpm_result.stdout, rpm_result.stderr, rpm_result.exit_code)
        if problem == "absent":
            report.mysql_installed = False
            report.checks.append(
                DiscoveryCheck(
                    code="mysql_packages",
                    status=CheckStatus.FAIL,
                    summary="MySQL server packages are not installed",
                )
            )
            report.stop_reason = "MySQL server is not installed"
            return report
        if problem:
            return self._fail(report, "mysql_packages", problem)
        server_rows = [row for row in rows if row[0] == _SERVER_PACKAGE]
        related_rows = [row for row in rows if row[0] != _SERVER_PACKAGE]
        packages = _installed(server_rows + related_rows)
        report.installed_packages = packages
        report.package_nevras = [row[1] for row in server_rows + related_rows]
        chosen_name, chosen_nevra, chosen_evr = server_rows[0]
        installed_upstream = parse_mysql_version(chosen_evr)
        if installed_upstream is None or (installed_upstream.major, installed_upstream.minor) != (8, 4):
            report.mysql_installed = True
            report.mysql_server_package = chosen_name
            report.mysql_server_version = chosen_evr
            return self._fail(
                report,
                "mysql_packages",
                "CentOS Stream 10 support in this phase is limited to MySQL 8.4 packages",
            )
        report.mysql_installed = True
        report.mysql_server_package = chosen_name
        report.mysql_server_version = chosen_evr
        report.mysql_upstream_version = str(installed_upstream)
        report.checks.append(
            DiscoveryCheck(
                code="mysql_packages",
                status=CheckStatus.PASS,
                summary=f"{chosen_name} {chosen_nevra}",
            )
        )

        query = self._run(runner, report, CMD_DNF_REPOQUERY)
        if query is None:
            return report
        if query.exit_code != 0:
            report.repository_origin = PackageOrigin.UNKNOWN
            return self._fail(
                report,
                "repository",
                "DNF repository metadata is unavailable. The installed MySQL package was still detected.",
            )
        candidate = parse_repoquery(query.stdout)
        if _repoquery_ambiguous(query.stdout):
            report.repository_origin = PackageOrigin.UNKNOWN
            return self._fail(report, "repository", "DNF candidate version output was ambiguous")
        report.candidate_version = candidate
        candidate_upstream = parse_mysql_version(candidate)
        report.candidate_upstream_version = str(candidate_upstream) if candidate_upstream else None

        repo = self._run(runner, report, CMD_DNF_REPOINFO)
        if repo is None:
            return report
        if repo.exit_code != 0:
            report.repository_origin = PackageOrigin.UNKNOWN
            return self._fail(
                report,
                "repository",
                "DNF repository status could not be read. The installed MySQL package was still detected.",
            )
        origin, uris = classify_centos_repositories(repo.stdout)
        report.repository_uris = uris
        history_problem = self._read_provenance(runner, report, [row[1] for row in server_rows + related_rows], repo.stdout)
        if history_problem == "abort":
            return report
        if history_problem:
            report.repository_origin = PackageOrigin.UNKNOWN
            return self._fail(report, "repository", history_problem)
        if origin != PackageOrigin.CENTOS_REPOSITORIES:
            report.repository_origin = PackageOrigin.UNKNOWN
            return self._fail(
                report,
                "repository",
                "Enabled CentOS Stream repository metadata does not identify appstream",
            )
        report.repository_origin = origin
        report.checks.append(
            DiscoveryCheck(
                code="repository",
                status=CheckStatus.PASS,
                summary="Enabled repositories are CentOS repositories",
            )
        )

        report.upgrade_kind = classify_version_change(
            installed_upstream,
            candidate_upstream,
            chosen_evr,
            candidate,
        )
        self._apply_upgrade_kind(report)
        if report.upgrade_kind in {UpgradeKind.UNKNOWN, UpgradeKind.DOWNGRADE}:
            return report
        identity = self._run(runner, report, CMD_MACHINE_ID)
        if identity is None:
            return report
        machine_id = identity.stdout.strip()
        if identity.exit_code != 0 or not re.fullmatch(r"[A-Za-z0-9-]{8,128}", machine_id):
            return self._fail(report, "host_identity", "Server host identity could not be read")
        report.host_identity = machine_id
        self._read_service(runner, report)
        return report

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
            report.stop_reason = "No newer MySQL package is available from the CentOS repositories"
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
            "package revision"
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

    def _read_service(self, runner: CommandRunner, report: EnvironmentReport) -> None:
        for name, command in (("mysqld", CMD_SERVICE_MYSQLD), ("mysql", CMD_SERVICE_MYSQL)):
            result = self._run(runner, report, command)
            if result is None:
                return
            state = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
            if state not in _SERVICE_STATES:
                continue
            report.service_name = name
            report.service_state = state
            status = CheckStatus.PASS if state == "active" else CheckStatus.WARNING
            report.checks.append(DiscoveryCheck(code="service", status=status, summary=f"{name} service is {state}"))
            return
        report.checks.append(
            DiscoveryCheck(
                code="service",
                status=CheckStatus.WARNING,
                summary="MySQL service state could not be read",
            )
        )

    def _read_provenance(
        self,
        runner: CommandRunner,
        report: EnvironmentReport,
        nevras: list[str],
        repoinfo: str,
    ) -> str | None:
        listed = self._run(runner, report, CMD_DNF_HISTORY_LIST)
        if listed is None:
            return "abort"
        if listed.exit_code != 0:
            return _history_read_failure("list", listed)
        transaction_ids = parse_history_ids(listed.stdout)[:_MAX_HISTORY_TRANSACTIONS]
        details: list[str] = []
        for transaction_id in transaction_ids:
            command = dnf_history_info_command(transaction_id)
            info = self._run(runner, report, command)
            if info is None:
                return "abort"
            if info.exit_code != 0:
                return _history_read_failure(f"info {transaction_id}", info)
            details.append(info.stdout)
        return provenance_problem(nevras, repoinfo, "\n".join(details))

    def _run(self, runner: CommandRunner, report: EnvironmentReport, command: str) -> CommandResult | None:
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
        if len(result.stdout) >= MAX_OUTPUT_CHARS or len(result.stderr) >= MAX_OUTPUT_CHARS:
            if " history " in f" {command} ":
                self._fail(
                    report,
                    "repository",
                    "DNF history output was truncated before provenance could be read",
                )
            else:
                self._fail(report, "output", "Remote command output exceeded the size limit")
            return None
        report.output_excerpt = redact_text(f"{result.stdout}\n{result.stderr}"[-4000:])
        return result

    def _fail(self, report: EnvironmentReport, code: str, summary: str) -> EnvironmentReport:
        report.environment_supported = False
        report.v1_patch_eligible = False
        report.patch_available = False
        report.stop_reason = summary
        report.checks.append(DiscoveryCheck(code=code, status=CheckStatus.FAIL, summary=summary))
        return report


def interpret_rpm_query(stdout: str, stderr: str, exit_code: int) -> tuple[list[tuple[str, str, str]], str | None]:
    """Return parsed rows, or a problem. 'absent' means every queried package is missing."""
    combined = f"{stdout}\n{stderr}"
    if _unparsed_rpm_lines(stdout):
        return [], "MySQL package detection output was ambiguous"
    rows = parse_rpm_query(stdout)
    names = [name for name, _nevra, _evr in rows]
    if len(names) != len(set(names)) or any(name not in _RPM_PACKAGES for name in names):
        return [], "MySQL package detection output was ambiguous"
    if _SERVER_PACKAGE in names:
        if exit_code not in {0, 1}:
            return [], "MySQL package detection failed"
        return rows, None
    if rows:
        return [], "MySQL package detection output was ambiguous"
    if exit_code == 1 and all(_package_absent(combined, name) for name in _RPM_PACKAGES):
        return [], "absent"
    return [], "MySQL package detection failed"


def _package_absent(text: str, name: str) -> bool:
    return any(match.group(1) == name for match in _NOT_INSTALLED.finditer(text))


def _unparsed_rpm_lines(stdout: str) -> bool:
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or _NOT_INSTALLED.search(stripped):
            continue
        parts = stripped.split("\t")
        if len(parts) < 3 or not all(part.strip() for part in parts[:3]):
            return True
    return False


def parse_rpm_query(text: str) -> list[tuple[str, str, str]]:
    """Return name, NEVRA, and EVR rows. Missing-package lines are ignored."""
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if _NOT_INSTALLED.search(line):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3:
            continue
        name, nevra, evr = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if name and nevra and evr:
            rows.append((name, nevra, evr))
    return rows


def parse_repoquery(text: str) -> str | None:
    """Return the EVR for mysql8.4-server. Versions are not assumed."""
    found: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == _SERVER_PACKAGE:
            found.append(parts[1])
    if len(found) != 1:
        return None
    return found[0]


def _repoquery_ambiguous(text: str) -> bool:
    found = [
        line.split()[1]
        for line in text.splitlines()
        if len(line.split()) >= 2 and line.split()[0] == _SERVER_PACKAGE
    ]
    return len(set(found)) > 1


def classify_centos_repositories(text: str) -> tuple[PackageOrigin, list[str]]:
    """Identify enabled CentOS Stream repositories. Mirror base URLs are not origins."""
    repos = parse_repoinfo(text)
    centos = [repo for repo in repos if repo["enabled"] and _is_centos_stream_repo(repo)]
    urls = [url for repo in centos for url in repo["metalinks"] + repo["baseurls"]]
    if not any(repo["repo_id"] == "appstream" for repo in centos):
        return PackageOrigin.UNKNOWN, urls
    return PackageOrigin.CENTOS_REPOSITORIES, urls


def parse_repoinfo(text: str) -> list[dict[str, object]]:
    """Parse DNF4 and DNF5 repoinfo blocks."""
    repos: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        label, value = raw.split(":", 1)
        key = re.sub(r"[\s_-]+", "", label.strip().lower())
        cleaned = re.sub(r"\s+\(\d+\s+more\)$", "", value.strip())
        if key == "repoid":
            current = {"repo_id": cleaned, "name": "", "enabled": True, "metalinks": [], "baseurls": []}
            repos.append(current)
            continue
        if current is None:
            continue
        if key in {"reponame", "name"}:
            current["name"] = cleaned
        elif key in {"repostatus", "status"}:
            current["enabled"] = "enabled" in cleaned.lower()
        elif key in {"repometalink", "metalink"}:
            _append_url(current["metalinks"], cleaned)  # type: ignore[arg-type]
        elif key in {"repobaseurl", "baseurl", "repomirrors", "mirrors"}:
            _append_url(current["baseurls"], cleaned)  # type: ignore[arg-type]
    return repos


def parse_history_ids(text: str) -> list[int]:
    """Return transaction ids from DNF4 pipe tables and DNF5 space-separated lists."""
    ids: list[int] = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s*(?:\||\s+\S)", line)
        if match:
            ids.append(int(match.group(1)))
    return ids


def _history_fields(line: str) -> tuple[str, str, str] | None:
    """Read one package row. The repository is the last column, not a fixed field."""
    parts = line.split()
    if len(parts) < 3 or parts[0].lower() not in _HISTORY_ACTIONS:
        return None
    return parts[0], parts[1], parts[-1]


def _history_read_failure(stage: str, result: CommandResult) -> str:
    """Name the history stage and exit code. The detail is redacted."""
    detail = _history_detail(result.stderr, result.stdout)
    message = f"DNF history {stage} failed with exit code {result.exit_code}"
    if detail:
        return f"{message}. {detail}"
    return message


def _history_detail(stderr: str, stdout: str) -> str:
    text = redact_text(f"{stderr}\n{stdout}") or ""
    text = re.sub(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        "[REDACTED]",
        text,
        flags=re.DOTALL,
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:180]
    return ""


def provenance_problem(nevras: list[str], repoinfo: str, history: str) -> str | None:
    """Fail closed unless every installed NEVRA was installed or upgraded from a CentOS Stream repo."""
    repos = parse_repoinfo(repoinfo)
    centos_ids = {str(repo["repo_id"]) for repo in repos if repo["enabled"] and _is_centos_stream_repo(repo)}
    sources: dict[str, set[str]] = {}
    for line in history.splitlines():
        fields = _history_fields(line.strip())
        if fields is None:
            continue
        action, package, repo = fields
        action = action.lower()
        package = package.replace("-0:", "-")
        repo_id = repo.lstrip("@")
        if action in _PREVIOUS_ACTIONS:
            continue
        matched = next((nevra for nevra in nevras if package == nevra), None)
        if matched is None:
            continue
        if action not in _SUPPLYING_ACTIONS:
            return f"DNF history action for {matched} is {action}, which is not an install or upgrade"
        if repo_id.lower() == "system":
            return f"{matched} was recorded from the local system (@System), not from a CentOS repository"
        sources.setdefault(matched, set()).add(repo_id)
    for nevra in nevras:
        found = sources.get(nevra, set())
        if not found:
            return f"DNF history does not identify the repository that supplied {nevra}"
        if len(found) > 1:
            return f"DNF history conflicts for {nevra}: {', '.join(sorted(found))}"
        repo_id = next(iter(found))
        if repo_id not in centos_ids:
            return (
                f"{nevra} was installed from {repo_id}, "
                "which is not an enabled CentOS Stream repository"
            )
    return None


def _is_centos_stream_repo(repo: dict[str, object]) -> bool:
    repo_id = str(repo["repo_id"])
    name = str(repo["name"]).lower()
    metalinks = list(repo["metalinks"])  # type: ignore[arg-type]
    baseurls = list(repo["baseurls"])  # type: ignore[arg-type]
    if repo_id not in _CENTOS_REPO_IDS or "centos stream" not in name:
        return False
    if any(_forbidden_host(url) for url in metalinks + baseurls):
        return False
    if any(_centos_host(url) for url in metalinks):
        return True
    return bool(baseurls) and all(_centos_host(url) for url in baseurls)


def _append_url(urls: list[str], value: str) -> None:
    for match in re.findall(r"https?://[^\s,]+", value):
        urls.append(match.rstrip(").,"))


def _centos_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _CENTOS_HOST_SUFFIXES)


def _forbidden_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "mysql.com" or host.endswith(".mysql.com") or host.endswith("repo.mysql.com")


def _stream_10(os_info: dict[str, str]) -> bool:
    if (os_info.get("ID") or "").lower() != "centos":
        return False
    if (os_info.get("VERSION_ID") or "") != "10":
        return False
    pretty = f"{os_info.get('VERSION', '')} {os_info.get('PRETTY_NAME', '')}".lower()
    return "stream" in pretty


def _installed(rows: list[tuple[str, str, str]]) -> list[InstalledPackage]:
    seen: set[str] = set()
    packages: list[InstalledPackage] = []
    for name, _nevra, evr in rows:
        if name in seen:
            continue
        seen.add(name)
        packages.append(InstalledPackage(name=name, version=evr, status="installed"))
    return packages
