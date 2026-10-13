"""Read-only MySQL discovery tests. No SSH connections and no package installs."""

from __future__ import annotations

import pytest

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import (
    classify_repository,
    classify_version_change,
    parse_mysql_version,
)
from backend.services.mysql_patch.command_runner import (
    CMD_APT_GET,
    CMD_DPKG,
    CMD_OS_RELEASE,
    CMD_SERVICE_MYSQL,
    MUTATING_MARKERS,
    SshCommandRunner,
    apt_policy_command,
)
from backend.services.mysql_patch.discovery import EnvironmentDiscoveryService
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import (
    EnvironmentReport,
    PackageOrigin,
    UpgradeKind,
)
from backend.services.mysql_patch.command_runner import CommandResult
from backend.services.mysql_patch.redaction import redact_text

INSTALLED = "8.0.36-0ubuntu0.24.04.1"
CANDIDATE = "8.0.40-0ubuntu0.24.04.1"
REVISION = "8.0.36-0ubuntu0.24.04.2"


class FakeRunner:
    def __init__(self, responses: dict[str, tuple[int, str, str]], fail: Exception | None = None) -> None:
        self.responses = responses
        self.fail = fail
        self.calls: list[str] = []

    def run(self, command: str, *, timeout: int = 20) -> CommandResult:
        self.calls.append(command)
        if self.fail is not None:
            raise self.fail
        if command not in self.responses:
            raise AssertionError(f"unexpected command: {command}")
        code, stdout, stderr = self.responses[command]
        return CommandResult(exit_code=code, stdout=stdout, stderr=stderr)


def _os_release(os_id: str = "ubuntu", version: str = "24.04") -> str:
    return f'ID={os_id}\nVERSION_ID="{version}"\nPRETTY_NAME="Ubuntu {version}"\n'


def _dpkg(*rows: tuple[str, str]) -> str:
    return "".join(f"{name}\t{version}\tinstalled\n" for name, version in rows)


def _policy(installed: str, candidate: str, uris: list[str]) -> str:
    lines = [
        "mysql-server:",
        f"  Installed: {installed}",
        f"  Candidate: {candidate}",
        "  Version table:",
    ]
    for uri in uris:
        lines.append(f"     {candidate} 500")
        lines.append(f"        500 {uri} noble-updates/universe amd64 Packages")
    return "\n".join(lines) + "\n"


def _responses(
    *,
    os_text: str | None = None,
    packages: str = "",
    policy: str = "",
    policy_package: str = "mysql-server-8.0",
    service: str = "active",
    os_id_missing_apt: bool = False,
) -> dict[str, tuple[int, str, str]]:
    data = {
        CMD_OS_RELEASE: (0, os_text if os_text is not None else _os_release(), ""),
        CMD_APT_GET: (0, "/usr/bin/apt-get\n", ""),
        CMD_DPKG: (0, packages, ""),
        apt_policy_command(policy_package): (0, policy, ""),
        CMD_SERVICE_MYSQL: (0, f"{service}\n", ""),
    }
    if os_id_missing_apt:
        data[CMD_APT_GET] = (1, "", "not found")
    return data


def _discover(responses: dict[str, tuple[int, str, str]], fail: Exception | None = None):
    runner = FakeRunner(responses, fail=fail)
    report = EnvironmentDiscoveryService().discover(runner, server_id="db-01")
    return runner, report


def test_maintenance_update_inside_same_series_is_eligible():
    policy = _policy(INSTALLED, CANDIDATE, ["http://archive.ubuntu.com/ubuntu"])
    runner, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    )

    assert report.environment_supported is True
    assert report.v1_patch_eligible is True
    assert report.patch_available is True
    assert report.upgrade_kind == UpgradeKind.MAINTENANCE
    assert report.mysql_upstream_version == "8.0.36"
    assert report.candidate_upstream_version == "8.0.40"
    assert report.repository_origin == PackageOrigin.UBUNTU_ARCHIVE
    assert report.service_name == "mysql"
    assert report.service_state == "active"
    assert report.stop_reason is None
    assert not any(marker in command for command in runner.calls for marker in MUTATING_MARKERS)


def test_ubuntu_package_revision_is_eligible():
    policy = _policy(INSTALLED, REVISION, ["http://security.ubuntu.com/ubuntu"])
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    )

    assert report.upgrade_kind == UpgradeKind.PACKAGE_REVISION
    assert report.v1_patch_eligible is True
    assert report.mysql_upstream_version == report.candidate_upstream_version == "8.0.36"


def test_same_package_version_has_no_patch():
    policy = _policy(INSTALLED, INSTALLED, ["http://archive.ubuntu.com/ubuntu"])
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    )

    assert report.environment_supported is True
    assert report.patch_available is False
    assert report.v1_patch_eligible is False
    assert report.upgrade_kind == UpgradeKind.NONE
    assert report.stop_reason == "No newer MySQL package is available from the Ubuntu archive"


def test_release_series_change_is_not_an_ordinary_patch():
    installed = "8.0.36-0ubuntu0.24.04.1"
    candidate = "8.4.3-0ubuntu0.24.04.1"
    policy = _policy(installed, candidate, ["http://archive.ubuntu.com/ubuntu"])
    _, report = _discover(
        _responses(
            packages=_dpkg(("mysql-server-8.0", installed)),
            policy=policy,
        )
    )

    assert report.upgrade_kind == UpgradeKind.RELEASE_SERIES
    assert report.environment_supported is True
    assert report.v1_patch_eligible is False
    assert "release-series" in (report.stop_reason or "")


def test_major_version_change_is_not_an_ordinary_patch():
    installed = parse_mysql_version("8.0.36-0ubuntu0.24.04.1")
    candidate = parse_mysql_version("9.0.1-1")
    assert classify_version_change(installed, candidate, "8.0.36-0ubuntu0.24.04.1", "9.0.1-1") == (
        UpgradeKind.MAJOR
    )


def test_downgrade_is_rejected():
    policy = _policy(CANDIDATE, INSTALLED, ["http://archive.ubuntu.com/ubuntu"])
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", CANDIDATE)), policy=policy)
    )

    assert report.upgrade_kind == UpgradeKind.DOWNGRADE
    assert report.v1_patch_eligible is False
    assert report.environment_supported is False


def test_non_ubuntu_os_stops_before_package_commands():
    runner, report = _discover(_responses(os_text=_os_release("debian", "12")))

    assert report.environment_supported is False
    assert report.stop_reason == "Unsupported operating system"
    assert runner.calls == [CMD_OS_RELEASE]


def test_missing_apt_is_rejected():
    responses = _responses(os_id_missing_apt=True)
    _, report = _discover(responses)

    assert report.package_manager.value == "unknown"
    assert report.stop_reason == "APT is not available on this server"
    assert CMD_DPKG not in report.commands_run
    assert all("apt-cache" not in command for command in report.commands_run)


def test_missing_mysql_installation():
    _, report = _discover(_responses(packages=""))

    assert report.mysql_installed is False
    assert report.repository_origin == PackageOrigin.NOT_INSTALLED
    assert report.stop_reason == "MySQL server is not installed"


def test_oracle_repository_is_rejected():
    policy = _policy(INSTALLED, CANDIDATE, ["http://repo.mysql.com/apt/ubuntu"])
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    )

    assert report.repository_origin == PackageOrigin.ORACLE_MYSQL_APT
    assert report.environment_supported is False
    assert report.v1_patch_eligible is False


def test_mixed_repository_is_rejected():
    policy = _policy(
        INSTALLED,
        CANDIDATE,
        ["http://archive.ubuntu.com/ubuntu", "https://repo.mysql.com/apt/ubuntu"],
    )
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    )

    assert report.repository_origin == PackageOrigin.MIXED
    assert report.v1_patch_eligible is False


def test_unknown_mirror_is_rejected():
    policy = _policy(INSTALLED, CANDIDATE, ["http://mirror.example.com/ubuntu"])
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-server-8.0", INSTALLED)), policy=policy)
    )

    assert report.repository_origin == PackageOrigin.UNKNOWN
    assert report.environment_supported is False


def test_oracle_server_package_is_rejected():
    _, report = _discover(
        _responses(packages=_dpkg(("mysql-community-server", "8.0.40-1ubuntu24.04")))
    )

    assert report.mysql_installed is True
    assert report.environment_supported is False
    assert "Oracle MySQL packages" in (report.stop_reason or "")
    assert all("apt-cache" not in command for command in report.commands_run)


def test_ssh_failure_is_fail_closed():
    _, report = _discover({}, fail=TimeoutError("timed out talking to 10.0.0.8"))

    assert report.environment_supported is False
    assert report.v1_patch_eligible is False
    assert report.stop_reason == "SSH command failed"
    assert report.transport_error == "timed out talking to 10.0.0.8"


def test_secret_in_remote_output_is_redacted():
    responses = _responses(
        packages=_dpkg(("mysql-server-8.0", INSTALLED)),
        policy=_policy(INSTALLED, CANDIDATE, ["http://archive.ubuntu.com/ubuntu"]),
    )
    responses[CMD_SERVICE_MYSQL] = (0, "active\n", "password=hunter2 token=abc")
    _, report = _discover(responses)

    assert report.v1_patch_eligible is True
    assert report.output_excerpt is not None
    assert "hunter2" not in report.output_excerpt
    assert "[REDACTED]" in report.output_excerpt
    assert "password" not in EnvironmentReport.model_fields


def test_secret_in_ssh_exception_is_redacted():
    _, report = _discover({}, fail=RuntimeError("login failed password=s3cret"))

    assert report.transport_error is not None
    assert "s3cret" not in report.transport_error
    assert "[REDACTED]" in report.transport_error


def test_oversized_output_is_rejected():
    huge = "x" * 64_000
    responses = _responses()
    responses[CMD_OS_RELEASE] = (0, huge, "")
    _, report = _discover(responses)

    assert report.environment_supported is False
    assert report.stop_reason == "Remote command output exceeded the size limit"


def test_empty_server_id_is_rejected():
    with pytest.raises(MysqlPatchSafetyError):
        EnvironmentDiscoveryService().discover(FakeRunner({}), server_id="  ")


def test_ssh_runner_refuses_commands_outside_the_allowlist(monkeypatch):
    def fail_if_called(**_kwargs):
        raise AssertionError("SSH should not be opened")

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fail_if_called)
    runner = SshCommandRunner(host="10.0.0.5", port=22, username="ubuntu", password="secret")

    with pytest.raises(MysqlPatchSafetyError):
        runner.run("apt-get upgrade -y")

    assert "secret" not in repr(runner)
    assert "password" not in repr(runner).lower()


def test_repository_classifier_accepts_only_ubuntu_archive_hosts():
    assert classify_repository(["http://us.archive.ubuntu.com/ubuntu"]) == PackageOrigin.UBUNTU_ARCHIVE
    assert classify_repository(["http://ports.ubuntu.com/ubuntu-ports"]) == PackageOrigin.UBUNTU_ARCHIVE
    assert classify_repository([]) == PackageOrigin.UNKNOWN


def test_redact_helper_masks_assignments():
    assert redact_text("MYSQL_PWD=top") == "MYSQL_PWD=[REDACTED]"
    assert redact_text(None) is None
