"""CentOS Stream 10 DNF patch tests. No live SSH and no package installs."""

from __future__ import annotations

import pytest

from backend.services.mysql_patch.command_runner import (
    CMD_APT_GET,
    CMD_DF_DNF,
    CMD_DNF,
    CMD_DNF_CHECK,
    CMD_DNF_HISTORY_LIST,
    CMD_DNF_LOCKS,
    CMD_DNF_REPOINFO,
    CMD_DNF_REPOQUERY,
    CMD_DNF_UPGRADE_ASSUMENO,
    CMD_DPKG,
    CMD_ID,
    CMD_MACHINE_ID,
    CMD_OS_RELEASE,
    CMD_RPM_SERVER,
    CMD_SERVICE_MYSQLD,
    CMD_SUDO_CHECK,
    dnf_history_info_command,
    dnf_upgrade_assumeno_command,
    is_allowed_command,
)
from backend.services.mysql_patch.discovery import EnvironmentDiscoveryService
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.executor import PatchExecutor
from backend.services.mysql_patch.models import JobOutcome, PackageManagerKind, PackageOrigin, UpgradeKind
from backend.services.mysql_patch.plan_service import PlanService
from backend.services.mysql_patch.precheck import MIN_FREE_BYTES, PrecheckService
from backend.services.mysql_patch.preview import PackagePreviewService
from backend.services.mysql_patch.verification import VerificationService, assess_preview, normalize_readonly_sql
from tests.test_mysql_patch_discovery import FakeRunner
from tests.test_mysql_patch_precheck import _Backups, _Jobs
from backend.services.mysql_patch.models import BackupReadinessState

INSTALLED = "8.4.2-1.el10"
CANDIDATE = "8.4.3-1.el10"
MACHINE = "abc123def4567890"


def _os_release(version: str = "10", stream: bool = True, os_id: str = "centos") -> str:
    pretty = f"{os_id} Stream {version}" if stream else f"{os_id} {version}"
    version_name = f'{version} (Stream)' if stream else version
    return (
        f"ID={os_id}\nVERSION_ID=\"{version}\"\nVERSION=\"{version_name}\"\n"
        f"PRETTY_NAME=\"{pretty}\"\n"
    )


def _rpm(name: str, evr: str) -> str:
    return f"{name}\t{name}-{evr}.x86_64\t{evr}\n"


def _repoinfo() -> str:
    """Enabled CentOS Stream repos. Base URLs are mirrors; metalinks identify CentOS."""
    blocks = (
        ("appstream", "CentOS Stream 10 - AppStream", "centos-appstream-10-stream", "AppStream"),
        ("baseos", "CentOS Stream 10 - BaseOS", "centos-baseos-10-stream", "BaseOS"),
        ("extras-common", "CentOS Stream 10 - Extras packages", "centos-extras-sig-extras-common-10-stream", "extras-common"),
    )
    lines: list[str] = []
    for repo_id, name, metalink, path in blocks:
        lines.extend(
            [
                f"Repo-id            : {repo_id}",
                f"Repo-name          : {name}",
                "Repo-status        : enabled",
                f"Repo-metalink      : https://mirrors.centos.org/metalink?repo={metalink}&arch=x86_64",
                f"Repo-baseurl       : https://mirror.example.net/centos/10-stream/{path}/x86_64/os/",
            ]
        )
    return "\n".join(lines) + "\n"


def _history(installed: str) -> tuple[str, str]:
    listing = (
        "ID | Command line | Date and time | Action(s) | Altered\n"
        "5 | upgrade | 2026-10-14 10:00 | Upgrade | 4\n"
        "4 | install mysql8.4-server | 2026-10-01 09:00 | Install | 4\n"
    )
    lines = ["Transaction ID : 5", "Packages altered:"]
    for name, arch in (
        ("mysql8.4", "x86_64"),
        ("mysql8.4-common", "x86_64"),
        ("mysql8.4-errmsg", "noarch"),
        ("mysql8.4-server", "x86_64"),
    ):
        lines.append(f"  Upgrade  {name}-{installed}.{arch} @appstream")
        lines.append(f"  Upgraded {name}-8.4.1-1.el10.{arch} @@System")
    return listing, "\n".join(lines) + "\n"


def _responses(installed: str = INSTALLED, candidate: str = CANDIDATE, *, os_text: str | None = None) -> dict:
    listing, info = _history(installed)
    return {
        CMD_OS_RELEASE: (0, os_text if os_text is not None else _os_release(), ""),
        CMD_DNF: (0, "/usr/bin/dnf\n", ""),
        CMD_RPM_SERVER: (0, _rpm("mysql8.4-server", installed) + _rpm("mysql8.4", installed) + _rpm("mysql8.4-common", installed), ""),
        CMD_DNF_REPOQUERY: (0, f"mysql8.4-server {candidate}\n", ""),
        CMD_DNF_REPOINFO: (0, _repoinfo(), ""),
        CMD_DNF_HISTORY_LIST: (0, listing, ""),
        dnf_history_info_command(5): (0, info, ""),
        dnf_history_info_command(4): (0, "Transaction ID : 4\nPackages altered:\n", ""),
        CMD_SERVICE_MYSQLD: (0, "active\n", ""),
        CMD_MACHINE_ID: (0, f"{MACHINE}\n", ""),
        CMD_ID: (0, "0\n", ""),
        CMD_DF_DNF: (
            0,
            "Filesystem 1B-blocks Used Available Capacity Mounted on\n"
            + "".join(
                f"/dev/root 200 100 {MIN_FREE_BYTES} 50% {mount}\n" for mount in ("/", "/var", "/var/cache/dnf")
            ),
            "",
        ),
        CMD_DNF_LOCKS: (
            0,
            "free /var/run/dnf.pid\nfree /var/lib/rpm/.rpm.lock\nfree /var/cache/dnf\n",
            "",
        ),
        CMD_DNF_CHECK: (0, "", ""),
    }


def _simulation(candidate: str = CANDIDATE, extra: str = "") -> str:
    return (
        "Dependencies resolved.\n"
        "================================================================================\n"
        " Package           Architecture  Version           Repository      Size\n"
        "================================================================================\n"
        "Upgrading:\n"
        f" mysql8.4-server    x86_64        {candidate}       appstream       17 M\n"
        f" mysql8.4           x86_64        {candidate}       appstream       1.5 M\n"
        f"{extra}"
        "Transaction Summary\n"
        "================================================================================\n"
        "Upgrade  2 Packages\n"
        "\n"
        "Operation aborted.\n"
    )


def _discover(responses: dict):
    runner = FakeRunner(responses)
    report = EnvironmentDiscoveryService().discover(runner, server_id="centos-01")
    return runner, report


def test_centos_stream_10_uses_dnf_and_not_the_ubuntu_adapter():
    runner, report = _discover(_responses())

    assert report.package_manager == PackageManagerKind.DNF
    assert report.environment_supported is True
    assert report.v1_patch_eligible is True
    assert report.upgrade_kind == UpgradeKind.MAINTENANCE
    assert report.mysql_upstream_version == "8.4.2"
    assert report.candidate_upstream_version == "8.4.3"
    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.host_identity == MACHINE
    assert report.mysql_server_version == INSTALLED
    assert any(item.endswith(INSTALLED + ".x86_64") for item in report.package_nevras)
    assert CMD_APT_GET not in runner.calls
    assert CMD_DPKG not in runner.calls
    assert "8.4.11" not in " ".join(runner.calls)


def test_no_available_update_is_not_eligible():
    _, report = _discover(_responses(candidate=INSTALLED))

    assert report.environment_supported is True
    assert report.patch_available is False
    assert report.v1_patch_eligible is False
    assert report.upgrade_kind == UpgradeKind.NONE
    assert "No newer MySQL package" in (report.stop_reason or "")


def test_rhel_and_older_centos_are_rejected_without_apt():
    rhel, rhel_report = _discover(_responses(os_text=_os_release(os_id="rhel", stream=False)))
    older, older_report = _discover(_responses(os_text=_os_release(version="9")))

    assert rhel_report.stop_reason == "Unsupported operating system"
    assert older_report.stop_reason == "Unsupported operating system"
    assert CMD_APT_GET not in rhel.calls
    assert CMD_APT_GET not in older.calls
    assert CMD_DNF not in rhel.calls
    assert dnf_upgrade_assumeno_command() not in older.calls


def test_dnf_command_failure_stops_before_a_simulation():
    responses = _responses()
    responses[CMD_DNF_REPOQUERY] = (2, "", "Error: cache is missing")
    runner, report = _discover(responses)

    assert report.mysql_installed is True
    assert report.repository_origin != PackageOrigin.NOT_INSTALLED
    assert report.environment_supported is False
    assert "unavailable" in (report.stop_reason or "")
    assert "not installed" not in (report.stop_reason or "").lower()
    assert CMD_DNF_UPGRADE_ASSUMENO not in runner.calls


def test_unrestricted_dnf_upgrade_is_not_allowlisted():
    assert is_allowed_command("dnf upgrade -y") is False
    assert is_allowed_command("dnf upgrade --assumeno") is False
    assert is_allowed_command("dnf upgrade --assumeno mysql-server") is False
    assert is_allowed_command(dnf_upgrade_assumeno_command()) is True


def test_preview_reports_an_available_update_without_installing():
    responses = _responses()
    responses[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation(), "")
    preview = PackagePreviewService(
        precheck=PrecheckService(backups=_Backups(BackupReadinessState.READABLE_UNVERIFIED, "readable, not a restore"), jobs=_Jobs([]))
    ).preview(FakeRunner(responses), server_id="centos-01", backup_path="/home/user/db_autopro_backups/a.sql")

    assert preview.accepted is True
    assert preview.simulation_only is True
    assert preview.transaction_summary is not None
    assert "Upgrade" in preview.transaction_summary
    assert {item.name for item in preview.changes} == {"mysql8.4", "mysql8.4-server"}
    assert all(item.action == "upgrade" for item in preview.changes)
    assert "assumeno" in preview.commands_run[-1]
    assert "-y" not in preview.commands_run[-1]
    assert assess_preview(preview)[0].status.value == "pass"
    assert "not a post-patch" in assess_preview(preview)[0].summary


def test_preview_reports_dependencies_and_removals_and_rejects_them():
    removal = (
        "Removing:\n"
        " mysql-old          x86_64        8.4.2-1.el10      @appstream      1.0 M\n"
    )
    responses = _responses()
    responses[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation(extra=removal).replace("Upgrade  2 Packages", "Upgrade  2 Packages\nRemove  1 Package"), "")
    preview = PackagePreviewService(
        precheck=PrecheckService(backups=_Backups(BackupReadinessState.READABLE_UNVERIFIED, "readable"), jobs=_Jobs([]))
    ).preview(FakeRunner(responses), server_id="centos-01", backup_path="C:/backup.sql")

    assert preview.accepted is False
    assert any(item.name == "mysql-old" and item.action == "remove" for item in preview.changes)
    assert "remove" in (preview.stop_reason or "").lower()

    dependency = (
        "Installing dependencies:\n"
        " openssl           x86_64        3.2.2-1.el10      baseos          2.0 M\n"
    )
    responses[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation(extra=dependency), "")
    blocked = PackagePreviewService(
        precheck=PrecheckService(backups=_Backups(BackupReadinessState.READABLE_UNVERIFIED, "readable"), jobs=_Jobs([]))
    ).preview(FakeRunner(responses), server_id="centos-01", backup_path="C:/backup.sql")
    assert any(item.name == "openssl" for item in blocked.changes)
    assert blocked.accepted is False
    assert "Unrelated package openssl" in (blocked.stop_reason or "")


def test_backup_remains_unverified_and_plan_changes_invalidate_approval():
    responses = _responses()
    responses[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation(), "")
    service = PlanService(
        preview=PackagePreviewService(
            precheck=PrecheckService(
                backups=_Backups(
                    BackupReadinessState.READABLE_UNVERIFIED,
                    "Backup file passed a limited read. This does not prove the backup can be restored.",
                ),
                jobs=_Jobs([]),
            )
        )
    )
    preview = service._preview.preview(FakeRunner(responses), server_id="centos-01", backup_path="C:/backup.sql")
    assert any("prove" in item.summary or "not prove" in item.summary or "unverified" in item.summary.lower() or "restore" in item.summary.lower() for item in preview.precheck.checks if item.code == "backup")
    plan = service.create_from_preview(preview)
    assert plan.host_identity == MACHINE
    assert plan.package_nevras
    assert plan.approval is None
    try:
        service.approve(plan, approved_by="operator", high_risk_confirmed=False)
    except MysqlPatchSafetyError as exc:
        assert "High-risk" in str(exc)
    else:
        raise AssertionError("approval without high-risk confirmation was accepted")
    approved = service.approve(plan, approved_by="operator", high_risk_confirmed=True)

    changed = _responses(candidate="8.4.4-1.el10")
    changed[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation("8.4.4-1.el10"), "")
    checked = service.revalidate(FakeRunner(changed), approved, backup_path="C:/backup.sql")
    assert checked.valid is False
    assert checked.execution_allowed is False
    assert "no longer matches" in (checked.stop_reason or "")


def test_apply_stays_dry_run_and_does_not_call_the_installer():
    responses = _responses()
    responses[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation(), "")
    plans = PlanService(
        preview=PackagePreviewService(
            precheck=PrecheckService(
                backups=_Backups(BackupReadinessState.READABLE_UNVERIFIED, "readable, restore not proven"),
                jobs=_Jobs([]),
            )
        )
    )
    preview = plans._preview.preview(FakeRunner(responses), server_id="centos-01", backup_path="C:/backup.sql")
    approved = plans.approve(
        plans.create_from_preview(preview),
        approved_by="operator",
        high_risk_confirmed=True,
    )
    called = {"count": 0}

    class _Spy:
        def install_approved(self, runner, plan):
            del runner, plan
            called["count"] += 1
            raise AssertionError("installer was called")

    dry = PatchExecutor(plans=plans, installer=_Spy()).execute(
        FakeRunner(responses),
        approved,
        backup_path="C:/backup.sql",
    )
    refused = PatchExecutor(plans=plans, installer=_Spy()).execute(
        FakeRunner(responses),
        approved,
        backup_path="C:/backup.sql",
        mode="execute",
    )

    assert dry.outcome == JobOutcome.DRY_RUN_PASSED
    assert dry.packages_installed is False
    assert dry.retry_attempted is False
    assert any("--assumeno" in command for command in dry.commands_run)
    assert all("dnf upgrade -y" not in command for command in dry.commands_run)
    assert refused.outcome == JobOutcome.FAILED
    assert refused.commands_run == []
    assert refused.packages_installed is False
    assert called["count"] == 0


def test_verification_succeeds_only_when_rpm_service_and_sql_checks_ran():
    plan_responses = _responses()
    plan_responses[CMD_DNF_UPGRADE_ASSUMENO] = (1, _simulation(), "")
    plans = PlanService(
        preview=PackagePreviewService(
            precheck=PrecheckService(
                backups=_Backups(BackupReadinessState.READABLE_UNVERIFIED, "readable"),
                jobs=_Jobs([]),
            )
        )
    )
    preview = plans._preview.preview(FakeRunner(plan_responses), server_id="centos-01", backup_path="C:/backup.sql")
    approved = plans.approve(plans.create_from_preview(preview), approved_by="operator", high_risk_confirmed=True)
    server = FakeRunner(
        {
            CMD_RPM_SERVER: (0, _rpm("mysql8.4-server", CANDIDATE), ""),
            CMD_SERVICE_MYSQLD: (0, "active\n", ""),
            CMD_DNF_CHECK: (0, "", ""),
        }
    )
    skipped = VerificationService().verify(server, approved, sql_statements=["SELECT 1"])
    assert skipped.outcome != JobOutcome.SUCCESS
    assert skipped.sql_checked is False

    ran = {"statements": []}

    def _run_sql(statement: str) -> bool:
        ran["statements"].append(statement)
        return True

    verified = VerificationService().verify(
        FakeRunner(
            {
                CMD_RPM_SERVER: (0, _rpm("mysql8.4-server", CANDIDATE), ""),
                CMD_SERVICE_MYSQLD: (0, "active\n", ""),
                CMD_DNF_CHECK: (0, "", ""),
            }
        ),
        approved,
        sql_statements=["SELECT 1"],
        sql_runner=_run_sql,
    )
    assert verified.outcome == JobOutcome.SUCCESS
    assert verified.target_reached is True
    assert verified.sql_checked is True
    assert ran["statements"] == ["SELECT 1"]
    with __import__("pytest").raises(MysqlPatchSafetyError):
        normalize_readonly_sql("DROP TABLE employees")


def _stream_rpm(evr: str) -> str:
    """Query-format rows for the CentOS Stream MySQL 8.4 package names."""
    rows = (
        ("mysql8.4", "x86_64"),
        ("mysql8.4-common", "x86_64"),
        ("mysql8.4-errmsg", "noarch"),
        ("mysql8.4-server", "x86_64"),
    )
    return "".join(f"{name}\t{name}-{evr}.{arch}\t{evr}\n" for name, arch in rows)


def _absent_rpm() -> str:
    return "".join(f"package {name} is not installed\n" for name in (
        "mysql8.4",
        "mysql8.4-common",
        "mysql8.4-errmsg",
        "mysql8.4-server",
    ))


def test_centos_stream_rpm_names_detect_installed_mysql_8_4_11():
    evr = "8.4.11-1.el10"
    responses = _responses(installed=evr, candidate=evr)
    responses[CMD_RPM_SERVER] = (0, _stream_rpm(evr), "")
    _runner, report = _discover(responses)

    assert "mysql8.4-server" in CMD_RPM_SERVER
    assert " mysql-server" not in f" {CMD_RPM_SERVER}"
    assert report.mysql_installed is True
    assert report.mysql_server_package == "mysql8.4-server"
    assert report.mysql_server_version == evr
    assert report.mysql_upstream_version == "8.4.11"
    assert {item.name for item in report.installed_packages} >= {
        "mysql8.4",
        "mysql8.4-common",
        "mysql8.4-errmsg",
        "mysql8.4-server",
    }
    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.repository_origin != PackageOrigin.NOT_INSTALLED
    assert report.stop_reason != "MySQL server is not installed"


def test_absent_centos_packages_are_not_installed_without_marking_the_repository():
    responses = _responses()
    responses[CMD_RPM_SERVER] = (1, _absent_rpm(), "")
    runner, report = _discover(responses)

    assert report.mysql_installed is False
    assert report.stop_reason == "MySQL server is not installed"
    assert report.repository_origin == PackageOrigin.UNKNOWN
    assert CMD_DNF_REPOQUERY not in runner.calls


def test_rpm_command_failure_is_a_detection_error():
    responses = _responses()
    responses[CMD_RPM_SERVER] = (127, "", "rpm: command not found")
    _runner, report = _discover(responses)

    assert report.mysql_installed is False
    assert report.stop_reason == "MySQL package detection failed"
    assert report.repository_origin != PackageOrigin.NOT_INSTALLED


def test_ambiguous_rpm_output_is_not_reported_as_missing():
    responses = _responses()
    responses[CMD_RPM_SERVER] = (0, "mysql8.4-server latest\n", "")
    _runner, report = _discover(responses)

    assert report.mysql_installed is False
    assert "ambiguous" in (report.stop_reason or "")
    assert report.stop_reason != "MySQL server is not installed"


def test_unavailable_repository_does_not_hide_installed_mysql():
    evr = "8.4.11-1.el10"
    responses = _responses(installed=evr, candidate=evr)
    responses[CMD_RPM_SERVER] = (0, _stream_rpm(evr), "")
    responses[CMD_DNF_REPOQUERY] = (
        1,
        "",
        "Error: Cache-only enabled but no cache for repo 'appstream'",
    )
    runner = FakeRunner(responses)
    report = PrecheckService(
        backups=_Backups(BackupReadinessState.READABLE_UNVERIFIED, "readable, restore not proven"),
        jobs=_Jobs([]),
    ).precheck(runner, server_id="centos-01", backup_path="C:/backup.sql")

    assert report.discovery.mysql_installed is True
    assert report.discovery.mysql_server_package == "mysql8.4-server"
    assert report.discovery.mysql_upstream_version == "8.4.11"
    assert report.discovery.repository_origin == PackageOrigin.UNKNOWN
    assert "not installed" not in (report.stop_reason or "").lower()
    assert "unavailable" in (report.stop_reason or "")
    assert CMD_DNF_UPGRADE_ASSUMENO not in runner.calls


def test_appstream_history_accepts_mirrors_and_ignores_previous_system_packages():
    evr = "8.4.11-1.el10"
    responses = _responses(installed=evr, candidate=evr)
    responses[CMD_RPM_SERVER] = (0, _stream_rpm(evr), "")
    _runner, report = _discover(responses)

    assert report.mysql_installed is True
    assert report.mysql_upstream_version == "8.4.11"
    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.stop_reason == "No newer MySQL package is available from the CentOS repositories"
    assert "not coming only from CentOS" not in (report.stop_reason or "")
    assert "@@System" not in (report.stop_reason or "")


def test_external_repository_history_is_rejected():
    responses = _responses()
    _listing, info = _history(INSTALLED)
    responses[dnf_history_info_command(5)] = (0, info.replace("@appstream", "@mysql80-community"), "")
    _runner, report = _discover(responses)

    assert report.mysql_installed is True
    assert report.repository_origin == PackageOrigin.UNKNOWN
    assert "mysql80-community" in (report.stop_reason or "")
    assert "not an enabled CentOS Stream repository" in (report.stop_reason or "")


def test_missing_history_fails_closed():
    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (0, "No transaction which manipulates package 'mysql8.4-server'\n", "")
    _runner, report = _discover(responses)

    assert report.mysql_installed is True
    assert "does not identify the repository" in (report.stop_reason or "")
    assert report.repository_origin == PackageOrigin.UNKNOWN


def test_conflicting_history_fails_closed():
    responses = _responses()
    _listing, info = _history(INSTALLED)
    conflict = info.replace(
        f"Upgrade  mysql8.4-server-{INSTALLED}.x86_64 @appstream",
        f"Upgrade  mysql8.4-server-{INSTALLED}.x86_64 @appstream\n"
        f"  Upgrade  mysql8.4-server-{INSTALLED}.x86_64 @epel",
    )
    responses[dnf_history_info_command(5)] = (0, conflict, "")
    _runner, report = _discover(responses)

    assert "conflicts" in (report.stop_reason or "")
    assert "appstream" in (report.stop_reason or "")
    assert "epel" in (report.stop_reason or "")


def test_same_candidate_is_not_a_provenance_failure():
    responses = _responses(candidate=INSTALLED)
    _runner, report = _discover(responses)

    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.patch_available is False
    assert report.stop_reason == "No newer MySQL package is available from the CentOS repositories"


def test_history_list_failure_names_the_stage_and_hides_secrets():
    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (
        1,
        "",
        "Cache-only enabled but no cache password=secret\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key\n-----END OPENSSH PRIVATE KEY-----\n",
    )
    _runner, report = _discover(responses)

    assert report.mysql_installed is True
    assert report.repository_origin != PackageOrigin.CENTOS_REPOSITORIES
    assert "DNF history list failed with exit code 1" in (report.stop_reason or "")
    assert "secret" not in (report.stop_reason or "")
    assert "PRIVATE KEY" not in (report.stop_reason or "")
    assert "[REDACTED]" in (report.stop_reason or "")


def test_history_output_truncation_does_not_establish_provenance():
    from backend.services.mysql_patch.command_runner import MAX_OUTPUT_CHARS

    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (0, "x" * MAX_OUTPUT_CHARS, "")
    _runner, report = _discover(responses)

    assert "truncated" in (report.stop_reason or "")
    assert report.v1_patch_eligible is False
    assert report.environment_supported is True
    assert report.transport_error is None
    assert report.repository_origin == PackageOrigin.UNKNOWN


def test_dnf5_history_table_reads_appstream_and_ignores_system():
    evr = "8.4.11-1.el10"
    responses = _responses(installed=evr, candidate=evr)
    responses[CMD_RPM_SERVER] = (0, _stream_rpm(evr), "")
    responses[CMD_DNF_HISTORY_LIST] = (
        0,
        "ID Command line Date and time Action(s) Altered\n"
        "5 dnf upgrade mysql8.4-server 2026-10-14 10:00:00 Upgrade 4\n"
        "4 dnf install mysql8.4-server 2026-10-01 09:00:00 Install 4\n",
        "",
    )
    lines = ["Transaction ID : 5", "Packages altered:", "  Action   Package   Reason   Repository"]
    for name, arch in (
        ("mysql8.4", "x86_64"),
        ("mysql8.4-common", "x86_64"),
        ("mysql8.4-errmsg", "noarch"),
        ("mysql8.4-server", "x86_64"),
    ):
        lines.append(f"  Upgrade  {name}-0:{evr}.{arch}  User  appstream")
        lines.append(f"  Upgraded {name}-0:8.4.9-1.el10.{arch}  User  @@System")
    responses[dnf_history_info_command(5)] = (0, "\n".join(lines) + "\n", "")
    _runner, report = _discover(responses)

    assert is_allowed_command(CMD_DNF_HISTORY_LIST)
    assert "sudo" not in CMD_DNF_HISTORY_LIST
    assert "sudo" not in dnf_history_info_command(5)
    assert is_allowed_command(dnf_history_info_command(5))
    assert is_allowed_command("sudo -n dnf history info 5; reboot") is False
    assert is_allowed_command("sudo -n dnf --assumeno history info 5") is False
    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.mysql_upstream_version == "8.4.11"
    assert report.stop_reason == "No newer MySQL package is available from the CentOS repositories"


def test_unprivileged_history_read_does_not_use_sudo():
    runner, report = _discover(_responses())
    history_commands = [command for command in runner.calls if " history " in f" {command} "]

    assert history_commands
    assert CMD_DNF_HISTORY_LIST == "dnf history list"
    assert dnf_history_info_command(5) == "dnf history info 5"
    assert "--contains-pkgs" not in CMD_DNF_HISTORY_LIST
    assert "--assumeno" not in CMD_DNF_HISTORY_LIST
    assert all(is_allowed_command(command) for command in history_commands)
    assert all("sudo" not in command for command in history_commands)
    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert "password is required" not in (report.stop_reason or "")


def test_server_history_table_identifies_appstream_without_package_filters():
    evr = "8.4.11-1.el10"
    responses = _responses(installed=evr, candidate=evr)
    responses[CMD_RPM_SERVER] = (
        0,
        "".join(
            f"{name}\t{name}-{evr}.{arch}\t{evr}\n"
            for name, arch in (
                ("mysql8.4-server", "x86_64"),
                ("mysql8.4", "x86_64"),
                ("mysql8.4-common", "x86_64"),
                ("mysql8.4-errmsg", "noarch"),
            )
        ),
        "",
    )
    responses[CMD_DNF_HISTORY_LIST] = (
        0,
        "ID | Command line | Date and time | Action(s) | Altered\n"
        "5 | upgrade mysql8.4* | 2026-10-14 12:16 | Upgrade | 4\n"
        "4 | install mysql8.4-server -y | 2026-07-30 15:20 | Install | 8 EE\n"
        "3 | install git -y | 2026-07-30 15:15 | Install | 5\n",
        "",
    )
    lines = ["Transaction ID : 5", "Command Line : upgrade mysql8.4*", "Packages Altered:"]
    for name, arch in (
        ("mysql8.4", "x86_64"),
        ("mysql8.4-common", "x86_64"),
        ("mysql8.4-errmsg", "noarch"),
        ("mysql8.4-server", "x86_64"),
    ):
        lines.append(f"  Upgrade  {name}-{evr}.{arch}  @appstream")
        lines.append(f"  Upgraded {name}-8.4.9-1.el10.{arch}  @@System")
    responses[dnf_history_info_command(5)] = (0, "\n".join(lines) + "\n", "")
    responses[dnf_history_info_command(4)] = (0, "Transaction ID : 4\nPackages Altered:\n", "")
    responses[dnf_history_info_command(3)] = (0, "Transaction ID : 3\nPackages Altered:\n  Install  git-2.45.x86_64  @appstream\n", "")
    _runner, report = _discover(responses)

    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.environment_supported is True
    assert "git" not in (report.stop_reason or "")


def test_history_usage_error_keeps_centos_checks_available():
    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (
        2,
        "",
        "usage: dnf history [-c [config file]] [-q] [-v] [--version]\n",
    )
    runner = FakeRunner(responses)
    discovery = EnvironmentDiscoveryService().discover(runner, server_id="centos-01")
    report = PrecheckService(
        backups=_Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        ),
        jobs=_Jobs([]),
    ).precheck(runner, server_id="centos-01", backup_path="C:/backup.sql")

    assert CMD_DNF_HISTORY_LIST in runner.calls
    assert "--contains-pkgs" not in " ".join(runner.calls)
    assert discovery.environment_supported is True
    assert discovery.repository_origin == PackageOrigin.UNKNOWN
    assert "exit code 2" in (discovery.stop_reason or "")
    assert "usage: dnf history" in (discovery.stop_reason or "")
    assert report.ready_for_plan is False
    assert not any(
        item.status.value == "skipped" and "not supported" in item.summary
        for item in report.checks
        if item.code in {"sudo", "disk", "locks", "pending_packages"}
    )
    assert any(item.code == "disk" and item.status.value != "skipped" for item in report.checks)


def test_large_unrelated_history_transaction_is_not_downloaded():
    listing, _info = _history(INSTALLED)
    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (
        0,
        listing + "1 | install -y gcc make perl | 2026-07-30 14:25 | Install | 1259 >E\n",
        "",
    )
    runner, report = _discover(responses)

    assert dnf_history_info_command(1) not in runner.calls
    assert dnf_history_info_command(5) in runner.calls
    assert report.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.environment_supported is True


def test_history_stdout_limit_keeps_centos_supported():
    from backend.services.mysql_patch.command_runner import MAX_OUTPUT_CHARS

    responses = _responses()
    responses[dnf_history_info_command(5)] = (0, "P" * MAX_OUTPUT_CHARS, "warning only\n")
    _runner, report = _discover(responses)

    assert report.transport_error is None
    assert report.environment_supported is True
    assert report.repository_origin == PackageOrigin.UNKNOWN
    assert report.v1_patch_eligible is False
    assert "truncated" in (report.stop_reason or "")


def test_history_stderr_limit_is_not_treated_as_provenance():
    from backend.services.mysql_patch.command_runner import MAX_OUTPUT_CHARS

    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (0, "ID | Command line | Date and time | Action(s) | Altered\n", "E" * MAX_OUTPUT_CHARS)
    _runner, report = _discover(responses)

    assert report.transport_error is None
    assert report.environment_supported is True
    assert report.repository_origin != PackageOrigin.CENTOS_REPOSITORIES
    assert "truncated" in (report.stop_reason or "")


def test_transaction_cap_does_not_invent_provenance():
    lines = [
        "ID | Command line | Date and time | Action(s) | Altered",
    ]
    lines.extend(
        f"{tx_id} | upgrade | 2026-10-14 10:00 | Upgrade | 4" for tx_id in range(11, 0, -1)
    )
    responses = _responses()
    responses[CMD_DNF_HISTORY_LIST] = (0, "\n".join(lines) + "\n", "")
    for tx_id in range(11, 1, -1):
        responses[dnf_history_info_command(tx_id)] = (0, f"Transaction ID : {tx_id}\nPackages altered:\n", "")
    supplied, info = _history(INSTALLED)
    del supplied
    responses[dnf_history_info_command(1)] = (0, info, "")
    runner, report = _discover(responses)

    assert dnf_history_info_command(1) not in runner.calls
    assert dnf_history_info_command(11) in runner.calls
    assert report.repository_origin == PackageOrigin.UNKNOWN
    assert report.environment_supported is True
    assert "did not examine every transaction" in (report.stop_reason or "")
    assert "does not identify the repository" in (report.stop_reason or "")


def test_ssh_runner_reports_which_history_stream_exceeded_the_limit(monkeypatch):
    from backend.services.mysql_patch.command_runner import MAX_OUTPUT_CHARS, SshCommandRunner
    from backend.services.mysql_patch.errors import OutputLimitError

    def fake_ssh(**kwargs: object) -> tuple[int, str, str]:
        command = str(kwargs["command"])
        if command == "dnf history info 1":
            return 0, "x" * (MAX_OUTPUT_CHARS + 1), ""
        if command == "dnf history list":
            return 0, "", "e" * (MAX_OUTPUT_CHARS + 1)
        raise AssertionError(command)

    monkeypatch.setattr("backend.services.ssh_client.run_ssh_command", fake_ssh)
    runner = SshCommandRunner(host="10.0.0.8", port=22, username="user", password="secret-value")

    with pytest.raises(OutputLimitError) as stdout_limit:
        runner.run("dnf history info 1")
    with pytest.raises(OutputLimitError) as stderr_limit:
        runner.run(CMD_DNF_HISTORY_LIST)

    assert stdout_limit.value.category == "dnf history"
    assert stdout_limit.value.stream == "stdout"
    assert stderr_limit.value.category == "dnf history"
    assert stderr_limit.value.stream == "stderr"
    assert "secret-value" not in str(stdout_limit.value)
    assert "x" * 20 not in str(stdout_limit.value)


def test_current_centos_package_is_not_a_precheck_failure():
    responses = _responses(installed=INSTALLED, candidate=INSTALLED)
    responses[CMD_ID] = (0, "1000\n", "")
    responses[CMD_SUDO_CHECK] = (1, "", "sudo: a password is required")
    runner = FakeRunner(responses)
    report = PrecheckService(
        backups=_Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        ),
        jobs=_Jobs([]),
    ).precheck(runner, server_id="centos-01", backup_path="C:/backup.sql")

    candidate = next(item for item in report.checks if item.code == "candidate")
    assert candidate.status.value == "warning"
    assert candidate.summary == "No newer MySQL package is available from the CentOS repositories"
    assert report.discovery.repository_origin == PackageOrigin.CENTOS_REPOSITORIES
    assert report.precheck_passed is True
    assert report.ready_for_plan is False
    assert next(item for item in report.checks if item.code == "sudo").status.value == "warning"


def test_uncertain_repository_still_fails_the_candidate_check():
    responses = _responses(installed=INSTALLED, candidate=INSTALLED)
    responses[CMD_DNF_REPOQUERY] = (1, "", "Cache-only enabled but no cache\n")
    runner = FakeRunner(responses)
    report = PrecheckService(
        backups=_Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        ),
        jobs=_Jobs([]),
    ).precheck(runner, server_id="centos-01", backup_path="C:/backup.sql")

    assert report.discovery.repository_origin == PackageOrigin.UNKNOWN
    assert report.precheck_passed is False
    assert any(item.status.value == "fail" for item in report.checks)
    assert report.ready_for_plan is False


def test_passwordless_sudo_is_explicit_for_a_read_only_precheck():
    responses = _responses()
    responses[CMD_ID] = (0, "1000\n", "")
    responses[CMD_SUDO_CHECK] = (1, "", "sudo: a password is required")
    runner = FakeRunner(responses)
    report = PrecheckService(
        backups=_Backups(
            BackupReadinessState.READABLE_UNVERIFIED,
            "Backup file passed the existing readability check. This does not prove the backup can be restored.",
        ),
        jobs=_Jobs([]),
    ).precheck(runner, server_id="centos-01", backup_path="/home/user/db_autopro_backups/employees_pre_patch.sql")

    history_commands = [command for command in runner.calls if " history " in f" {command} "]
    sudo = next(item for item in report.checks if item.code == "sudo")
    assert history_commands
    assert all("sudo" not in command for command in history_commands)
    assert CMD_SUDO_CHECK in runner.calls
    assert sudo.status.value == "warning"
    assert "Passwordless sudo is not available" in sudo.summary
    assert "password is required" in sudo.summary
    assert CMD_DF_DNF in runner.calls
    assert "DNF history" not in (report.stop_reason or "")
