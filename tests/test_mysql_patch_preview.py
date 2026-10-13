"""Package-preview tests. The APT simulation is mocked and nothing is installed."""

from __future__ import annotations

import pytest

from backend.services.mysql_patch.command_runner import (
    MUTATING_MARKERS,
    SshCommandRunner,
    apt_simulate_command,
)
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.preview import PackagePreviewService
from tests.test_mysql_patch_discovery import CANDIDATE, FakeRunner
from tests.test_mysql_patch_precheck import _df, _ready_responses, _service
from backend.services.mysql_patch.command_runner import CMD_DF

OLD = "8.0.36-0ubuntu0.24.04.1"


def _simulation(lines: list[str], *, upgraded: int, new: int = 0, removed: int = 0) -> str:
    body = "\n".join(lines)
    return (
        "NOTE: This is only a simulation!\n"
        f"{upgraded} upgraded, {new} newly installed, {removed} to remove and 12 not upgraded.\n"
        f"{body}\n"
    )


def _inst(name: str, old: str | None, new: str) -> str:
    if old:
        return f"Inst {name} [{old}] ({new} Ubuntu:24.04/noble-updates [amd64])"
    return f"Inst {name} ({new} Ubuntu:24.04/noble-updates [amd64])"


def _mysql_lines() -> list[str]:
    return [
        _inst("mysql-server-8.0", OLD, CANDIDATE),
        _inst("mysql-server-core-8.0", OLD, CANDIDATE),
        _inst("mysql-common", "5.8+1.0.5ubuntu2", "5.8+1.0.8ubuntu1"),
    ]


def _preview(stdout: str, responses: dict | None = None, service=None):
    command = apt_simulate_command("mysql-server-8.0", CANDIDATE)
    data = dict(responses or _ready_responses())
    data[command] = (0, stdout, "")
    runner = FakeRunner(data)
    report = PackagePreviewService(precheck=service or _service()).preview(
        runner,
        server_id="db-01",
        backup_path="C:/backup.sql",
    )
    return runner, report


def test_related_mysql_packages_are_accepted():
    text = _simulation(_mysql_lines(), upgraded=3)
    runner, report = _preview(text)

    assert report.accepted is True
    assert report.stop_reason is None
    assert [item.name for item in report.changes] == [
        "mysql-server-8.0",
        "mysql-server-core-8.0",
        "mysql-common",
    ]
    assert report.changes[0].action == "upgrade"
    assert report.changes[0].target_version == CANDIDATE
    assert any(marker in command for command in runner.calls for marker in MUTATING_MARKERS) is False
    assert any(command.startswith("apt-get -s ") for command in runner.calls)


def test_unrelated_package_upgrade_is_rejected():
    lines = _mysql_lines() + [_inst("openssl", "3.0.2-0ubuntu1.1", "3.0.13-0ubuntu3.1")]
    _, report = _preview(_simulation(lines, upgraded=4))

    assert report.accepted is False
    assert "openssl" in (report.stop_reason or "")
    assert "Unrelated" in (report.stop_reason or "")


def test_package_removal_is_rejected():
    lines = _mysql_lines() + ["Remv legacy-helper [1.2.3]"]
    _, report = _preview(_simulation(lines, upgraded=3, removed=1))

    assert report.accepted is False
    assert "remove" in (report.stop_reason or "").lower()


def test_removal_count_without_a_parsed_line_is_rejected():
    _, report = _preview(_simulation(_mysql_lines(), upgraded=3, removed=1))

    assert report.accepted is False
    assert "do not match" in (report.stop_reason or "") or "remove" in (report.stop_reason or "").lower()


def test_candidate_version_mismatch_is_rejected():
    other = "8.0.41-0ubuntu0.24.04.1"
    lines = [_inst("mysql-server-8.0", OLD, other)]
    _, report = _preview(_simulation(lines, upgraded=1))

    assert report.accepted is False
    assert "candidate" in (report.stop_reason or "").lower()


def test_missing_target_package_is_rejected():
    lines = [_inst("mysql-common", "5.8+1.0.5ubuntu2", "5.8+1.0.8ubuntu1")]
    _, report = _preview(_simulation(lines, upgraded=1))

    assert report.accepted is False
    assert "mysql-server-8.0" in (report.stop_reason or "")


def test_other_series_dependency_is_rejected():
    lines = [
        _inst("mysql-server-8.0", OLD, CANDIDATE),
        _inst("libmysqlclient21", OLD, "8.4.3-0ubuntu0.24.04.1"),
    ]
    _, report = _preview(_simulation(lines, upgraded=2))

    assert report.accepted is False
    assert "release series" in (report.stop_reason or "")


def test_new_unrelated_install_is_rejected():
    lines = [
        _inst("mysql-server-8.0", OLD, CANDIDATE),
        _inst("nginx", None, "1.24.0-2ubuntu1"),
    ]
    _, report = _preview(_simulation(lines, upgraded=1, new=1))

    assert report.accepted is False
    assert "nginx" in (report.stop_reason or "")


def test_failed_precheck_does_not_run_simulation():
    responses = _ready_responses(**{CMD_DF: (0, _df(1024), "")})
    runner = FakeRunner(responses)
    report = PackagePreviewService(precheck=_service()).preview(
        runner,
        server_id="db-01",
        backup_path="C:/backup.sql",
    )

    assert report.accepted is False
    assert report.precheck.ready_for_plan is False
    assert all(not command.startswith("apt-get") for command in runner.calls)
    assert any(item.status.value == "skipped" for item in report.checks)


def test_apt_failure_is_fail_closed_and_redacts_secrets():
    command = apt_simulate_command("mysql-server-8.0", CANDIDATE)
    responses = _ready_responses()
    responses[command] = (100, "", "E: password=hunter2\n")
    runner = FakeRunner(responses)
    report = PackagePreviewService(precheck=_service()).preview(
        runner,
        server_id="db-01",
        backup_path="C:/backup.sql",
    )

    assert report.accepted is False
    assert "hunter2" not in (report.stop_reason or "")
    assert "[REDACTED]" in (report.stop_reason or "")


def test_empty_transaction_is_rejected():
    _, report = _preview(_simulation([], upgraded=0))

    assert report.accepted is False
    assert "did not propose" in (report.stop_reason or "")


def test_real_install_and_broad_upgrade_are_not_allowlisted(monkeypatch):
    def fail_if_called(**_kwargs):
        raise AssertionError("SSH should not be opened")

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fail_if_called)
    runner = SshCommandRunner(host="10.0.0.5", port=22, username="ubuntu", password="secret")
    with pytest.raises(MysqlPatchSafetyError):
        runner.run("apt-get install mysql-server-8.0")
    with pytest.raises(MysqlPatchSafetyError):
        runner.run("apt-get -s upgrade")
    with pytest.raises(MysqlPatchSafetyError):
        apt_simulate_command("openssl", "3.0.13-0ubuntu3.1")
    with pytest.raises(MysqlPatchSafetyError):
        apt_simulate_command("mysql-server-8.0", "8.0.40;reboot")


def test_simulation_command_is_allowlisted(monkeypatch):
    seen: list[str] = []

    def fake_ssh(**kwargs):
        seen.append(kwargs["command"])
        return 0, "simulated", ""

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fake_ssh)
    command = apt_simulate_command("mysql-server-8.0", CANDIDATE)
    result = SshCommandRunner(
        host="10.0.0.5",
        port=22,
        username="ubuntu",
        password="secret",
    ).run(command, timeout=60)

    assert result.stdout == "simulated"
    assert seen == [command]
    assert "secret" not in command
