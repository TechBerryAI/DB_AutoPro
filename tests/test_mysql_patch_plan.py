"""Patch plan and approval tests. No package is installed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.services.mysql_patch.command_runner import apt_policy_command, apt_simulate_command
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import UpgradeKind
from backend.services.mysql_patch.plan_service import PlanService
from backend.services.mysql_patch.preview import PackagePreviewService
from tests.test_mysql_patch_discovery import CANDIDATE, INSTALLED, FakeRunner, _policy
from tests.test_mysql_patch_precheck import _ready_responses, _service
from tests.test_mysql_patch_preview import _inst, _simulation

NEW_CANDIDATE = "8.0.41-0ubuntu0.24.04.1"
WHEN = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


class _Boom:
    def run(self, command: str, *, timeout: int = 20):
        raise AssertionError(f"remote command was not expected: {command}")


def _clock(moment: datetime):
    current = {"at": moment}

    def read() -> datetime:
        return current["at"]

    def advance(delta: timedelta) -> None:
        current["at"] = current["at"] + delta

    return read, advance


def _accepted(candidate: str = CANDIDATE, uris: list[str] | None = None):
    data = _ready_responses()
    if candidate != CANDIDATE or uris is not None:
        data[apt_policy_command("mysql-server-8.0")] = (
            0,
            _policy(INSTALLED, candidate, uris or ["http://archive.ubuntu.com/ubuntu"]),
            "",
        )
    lines = [
        _inst("mysql-server-8.0", INSTALLED, candidate),
        _inst("mysql-server-core-8.0", INSTALLED, candidate),
        _inst("mysql-common", "5.8+1.0.5ubuntu2", "5.8+1.0.8ubuntu1"),
    ]
    data[apt_simulate_command("mysql-server-8.0", candidate)] = (0, _simulation(lines, upgraded=3), "")
    runner = FakeRunner(data)
    preview = PackagePreviewService(precheck=_service()).preview(
        runner,
        server_id="db-01",
        backup_path="C:/backup.sql",
    )
    return runner, preview


def _plan_service(clock=None) -> PlanService:
    preview = PackagePreviewService(precheck=_service())
    if clock is None:
        return PlanService(preview=preview)
    return PlanService(preview=preview, clock=clock)


def _approved_plan():
    _runner, preview = _accepted()
    read, _advance = _clock(WHEN)
    service = _plan_service(read)
    plan = service.create_from_preview(preview)
    approved = service.approve(plan, approved_by="alex", high_risk_confirmed=True)
    return service, plan, approved


def test_accepted_preview_becomes_an_unapproved_plan():
    _runner, preview = _accepted()
    plan = PlanService().create_from_preview(preview)

    assert plan.approval is None
    assert plan.server_id == "db-01"
    assert plan.current_version == INSTALLED
    assert plan.target_version == CANDIDATE
    assert plan.server_package == "mysql-server-8.0"
    assert [item.name for item in plan.packages] == [
        "mysql-common",
        "mysql-server-8.0",
        "mysql-server-core-8.0",
    ]
    assert plan.recovery_proven is False
    assert plan.high_risk is True
    assert any("not been proven" in reason for reason in plan.high_risk_reasons)
    assert any("restart" in reason for reason in plan.high_risk_reasons)
    assert "SELECT 1" in " ".join(plan.planned_verification)
    assert plan.required_privileges == "Remote user is root"
    assert len(plan.plan_digest) == 64


def test_two_plans_from_the_same_preview_share_a_digest():
    _runner, preview = _accepted()
    service = PlanService()
    first = service.create_from_preview(preview)
    second = service.create_from_preview(preview)

    assert first.plan_digest == second.plan_digest
    assert first.plan_id != second.plan_id


def test_rejected_preview_cannot_become_a_plan():
    _runner, preview = _accepted()
    preview.accepted = False
    preview.stop_reason = "simulation was rejected"

    with pytest.raises(MysqlPatchSafetyError):
        PlanService().create_from_preview(preview)


def test_no_newer_package_cannot_become_a_plan_or_approval():
    _runner, preview = _accepted()
    discovery = preview.precheck.discovery
    original_kind = discovery.upgrade_kind
    discovery.upgrade_kind = UpgradeKind.NONE
    discovery.patch_available = False

    with pytest.raises(MysqlPatchSafetyError, match="No newer MySQL package is available"):
        PlanService().create_from_preview(preview)

    discovery.upgrade_kind = original_kind
    discovery.patch_available = True
    plan = PlanService().create_from_preview(preview)
    plan.upgrade_kind = UpgradeKind.NONE
    with pytest.raises(MysqlPatchSafetyError, match="No newer MySQL package is available"):
        PlanService().approve(plan, approved_by="alex", high_risk_confirmed=True)


def test_approval_requires_a_person_and_high_risk_confirmation():
    service, plan, _approved = _approved_plan()

    with pytest.raises(MysqlPatchSafetyError):
        service.approve(plan, approved_by="  ", high_risk_confirmed=True)
    with pytest.raises(MysqlPatchSafetyError):
        service.approve(plan, approved_by="alex", high_risk_confirmed=False)


def test_approve_returns_a_copy_and_does_not_execute():
    service, plan, approved = _approved_plan()

    assert plan.approval is None
    assert approved.approval is not None
    assert approved.approval.approved_by == "alex"
    assert approved.approval.plan_digest == plan.plan_digest
    assert approved.approval.high_risk_confirmed is True
    with pytest.raises(MysqlPatchSafetyError):
        service.approve(approved, approved_by="alex", high_risk_confirmed=True)


def test_unapproved_plan_is_not_revalidated_against_the_server():
    service, plan, _approved = _approved_plan()
    result = service.revalidate(_Boom(), plan, backup_path="C:/backup.sql")

    assert result.valid is False
    assert result.execution_allowed is False
    assert result.stop_reason == "Plan is not approved"
    assert result.commands_run == []


def test_matching_server_allows_execution_later_without_installing():
    service, _plan, approved = _approved_plan()
    runner, _preview = _accepted()
    result = service.revalidate(runner, approved, backup_path="C:/backup.sql")

    assert result.valid is True
    assert result.execution_allowed is True
    assert result.stop_reason is None
    assert any(command.startswith("apt-get -s ") for command in result.commands_run)
    assert all("apt-get install" not in command for command in result.commands_run)
    assert all("apt-get upgrade" not in command for command in result.commands_run)


def test_candidate_change_after_approval_is_rejected():
    service, _plan, approved = _approved_plan()
    runner, preview = _accepted(NEW_CANDIDATE)
    assert preview.accepted is True

    result = service.revalidate(runner, approved, backup_path="C:/backup.sql")

    assert result.valid is False
    assert result.execution_allowed is False
    assert "no longer matches" in (result.stop_reason or "")


def test_repository_change_after_approval_is_rejected():
    service, _plan, approved = _approved_plan()
    runner, preview = _accepted(uris=["http://security.ubuntu.com/ubuntu"])
    assert preview.accepted is True

    result = service.revalidate(runner, approved, backup_path="C:/backup.sql")

    assert result.valid is False
    assert "no longer matches" in (result.stop_reason or "")


def test_modified_plan_invalidates_approval_without_a_new_connection():
    _service_plan, _plan, approved = _approved_plan()
    approved.packages[0].target_version = "9.9.9-changed"
    result = PlanService().revalidate(_Boom(), approved, backup_path="C:/backup.sql")

    assert result.valid is False
    assert result.stop_reason == "Plan was modified after it was created"


def test_expired_approval_is_rejected():
    read, advance = _clock(WHEN)
    _runner, preview = _accepted()
    service = PlanService(clock=read)
    approved = service.approve(
        service.create_from_preview(preview),
        approved_by="alex",
        high_risk_confirmed=True,
    )
    advance(timedelta(minutes=31))

    result = service.revalidate(_Boom(), approved, backup_path="C:/backup.sql")

    assert result.valid is False
    assert result.stop_reason == "Approval has expired"
    assert result.commands_run == []


def test_changed_package_before_approval_is_rejected():
    _runner, preview = _accepted()
    service = PlanService()
    plan = service.create_from_preview(preview)
    plan.packages[0].action = "remove"

    with pytest.raises(MysqlPatchSafetyError, match="modified before approval"):
        service.approve(plan, approved_by="alex", high_risk_confirmed=True)
