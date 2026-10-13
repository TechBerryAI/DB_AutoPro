"""Create, approve, and revalidate a MySQL patch plan.

Approving a plan does not install packages. A later phase may execute only
after revalidate reports that the fresh simulation still matches this digest.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from backend.services.mysql_patch.command_runner import CommandRunner
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import (
    ApprovalRecord,
    BackupReadinessState,
    CheckStatus,
    PackageChange,
    PackagePreviewReport,
    PatchPlan,
    PlanRevalidation,
)
from backend.services.mysql_patch.preview import PackagePreviewService

APPROVAL_TTL = timedelta(minutes=30)
_FUTURE_SKEW = timedelta(minutes=5)

PLANNED_VERIFICATION = (
    "MySQL service is active after the package transaction",
    "Installed MySQL server package version equals the approved target version",
    "Run SELECT 1 when database credentials and a client are available",
    "Do not treat an apt-get exit code of zero as success by itself",
)


class PlanService:
    """Build an immutable plan from an accepted preview and check it again later."""

    def __init__(
        self,
        preview: PackagePreviewService | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = APPROVAL_TTL,
    ) -> None:
        self._preview = preview or PackagePreviewService()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ttl = ttl

    def create_from_preview(self, preview: PackagePreviewReport) -> PatchPlan:
        """Snapshot one accepted preview. The plan is not approved."""
        if not preview.accepted or not preview.precheck.ready_for_plan:
            raise MysqlPatchSafetyError(preview.stop_reason or "Preview was not accepted")
        discovery = preview.precheck.discovery
        if not discovery.mysql_server_version or not discovery.candidate_version:
            raise MysqlPatchSafetyError("Preview is missing MySQL versions")
        if not discovery.mysql_upstream_version or not discovery.candidate_upstream_version:
            raise MysqlPatchSafetyError("Preview is missing MySQL upstream versions")
        if not discovery.mysql_server_package:
            raise MysqlPatchSafetyError("Preview is missing the MySQL server package name")
        if preview.precheck.backup_state != BackupReadinessState.READABLE_UNVERIFIED:
            raise MysqlPatchSafetyError("Backup readiness is not in a state that can be planned")
        if preview.precheck.recovery_proven:
            raise MysqlPatchSafetyError("Recovery was marked proven, which this version cannot establish")

        packages = _sorted_packages(preview.changes)
        reasons = _risk_reasons(preview)
        material = _material(
            server_id=preview.server_id,
            current_version=discovery.mysql_server_version,
            target_version=discovery.candidate_version,
            current_upstream_version=discovery.mysql_upstream_version,
            target_upstream_version=discovery.candidate_upstream_version,
            server_package=discovery.mysql_server_package,
            upgrade_kind=discovery.upgrade_kind.value,
            repository_origin=discovery.repository_origin.value,
            repository_uris=list(discovery.repository_uris),
            packages=packages,
            backup_state=preview.precheck.backup_state.value,
        )
        return PatchPlan(
            plan_id=str(uuid.uuid4()),
            server_id=preview.server_id,
            created_at=self._now(),
            current_version=discovery.mysql_server_version,
            target_version=discovery.candidate_version,
            current_upstream_version=discovery.mysql_upstream_version,
            target_upstream_version=discovery.candidate_upstream_version,
            server_package=discovery.mysql_server_package,
            upgrade_kind=discovery.upgrade_kind,
            repository_origin=discovery.repository_origin,
            repository_uris=list(discovery.repository_uris),
            packages=packages,
            precheck_findings=list(preview.precheck.checks),
            backup_state=preview.precheck.backup_state,
            recovery_proven=False,
            service_interruption_expected=preview.precheck.service_interruption_expected,
            required_privileges=_privileges(preview),
            planned_verification=list(PLANNED_VERIFICATION),
            high_risk=bool(reasons),
            high_risk_reasons=reasons,
            plan_digest=_digest(material),
            approval=None,
        )

    def approve(
        self,
        plan: PatchPlan,
        *,
        approved_by: str,
        high_risk_confirmed: bool = False,
    ) -> PatchPlan:
        """Return a new approved copy. The previous object is left unchanged."""
        approver = approved_by.strip()
        if not approver or any(char in approver for char in "\r\n"):
            raise MysqlPatchSafetyError("Approver name is required")
        if len(approver) > 80:
            raise MysqlPatchSafetyError("Approver name is too long")
        if plan.approval is not None:
            raise MysqlPatchSafetyError("Plan is already approved. Create a new plan to review again")
        if _digest(_material_from_plan(plan)) != plan.plan_digest:
            raise MysqlPatchSafetyError("Plan was modified before approval")
        if plan.high_risk and not high_risk_confirmed:
            raise MysqlPatchSafetyError("High-risk confirmation is required before approval")
        approved = plan.model_copy(deep=True)
        approved.approval = ApprovalRecord(
            approved_by=approver,
            approved_at=self._now(),
            plan_digest=plan.plan_digest,
            high_risk_confirmed=high_risk_confirmed,
        )
        return approved

    def revalidate(
        self,
        runner: CommandRunner,
        plan: PatchPlan,
        *,
        backup_path: str | None = None,
    ) -> PlanRevalidation:
        """Compare the plan with a fresh simulation. Does not install packages."""
        result = PlanRevalidation(plan_id=plan.plan_id)
        problem = self._approval_problem(plan)
        if problem:
            result.stop_reason = problem
            return result

        fresh = self._preview.preview(runner, server_id=plan.server_id, backup_path=backup_path)
        result.commands_run = list(fresh.commands_run)
        if not fresh.accepted:
            result.stop_reason = fresh.stop_reason or "Fresh package preview was not accepted"
            return result
        fresh_digest = _digest(_material_from_plan(self.create_from_preview(fresh)))
        if fresh_digest != plan.plan_digest:
            result.stop_reason = (
                "Approved plan no longer matches the server: candidate version, "
                "repository, or package set changed"
            )
            return result
        result.valid = True
        result.execution_allowed = True
        return result

    def _approval_problem(self, plan: PatchPlan) -> str | None:
        if plan.approval is None:
            return "Plan is not approved"
        if _digest(_material_from_plan(plan)) != plan.plan_digest:
            return "Plan was modified after it was created"
        if plan.approval.plan_digest != plan.plan_digest:
            return "Approval does not match this plan"
        if plan.high_risk and not plan.approval.high_risk_confirmed:
            return "High-risk confirmation is missing"
        now = self._now()
        approved_at = plan.approval.approved_at
        if approved_at.tzinfo is None:
            return "Approval timestamp is not valid"
        if approved_at - now > _FUTURE_SKEW:
            return "Approval timestamp is not valid"
        if now - approved_at > self._ttl:
            return "Approval has expired"
        return None

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            raise MysqlPatchSafetyError("Clock must return a timezone-aware time")
        return current


def _privileges(preview: PackagePreviewReport) -> str:
    for item in preview.precheck.checks:
        if item.code == "sudo" and item.status == CheckStatus.PASS:
            if "root" in item.summary.lower():
                return "Remote user is root"
            return "Remote user must have passwordless sudo"
    return "Passwordless sudo or root is required"


def _risk_reasons(preview: PackagePreviewReport) -> list[str]:
    reasons: list[str] = []
    if not preview.precheck.recovery_proven:
        reasons.append("Backup file is readable, but a restore has not been proven")
    if preview.precheck.service_interruption_expected:
        reasons.append("The MySQL service is expected to restart and interrupt connections")
    if not preview.precheck.host_key_verified:
        reasons.append("SSH host key was not pinned")
    return reasons


def _sorted_packages(changes: list[PackageChange]) -> list[PackageChange]:
    ordered = sorted(changes, key=lambda item: item.name)
    return [item.model_copy(deep=True) for item in ordered]


def _material_from_plan(plan: PatchPlan) -> dict:
    return _material(
        server_id=plan.server_id,
        current_version=plan.current_version,
        target_version=plan.target_version,
        current_upstream_version=plan.current_upstream_version,
        target_upstream_version=plan.target_upstream_version,
        server_package=plan.server_package,
        upgrade_kind=plan.upgrade_kind.value,
        repository_origin=plan.repository_origin.value,
        repository_uris=list(plan.repository_uris),
        packages=plan.packages,
        backup_state=plan.backup_state.value,
    )


def _material(
    *,
    server_id: str,
    current_version: str,
    target_version: str,
    current_upstream_version: str,
    target_upstream_version: str,
    server_package: str,
    upgrade_kind: str,
    repository_origin: str,
    repository_uris: list[str],
    packages: list[PackageChange],
    backup_state: str,
) -> dict:
    return {
        "backup_state": backup_state,
        "current_upstream_version": current_upstream_version,
        "current_version": current_version,
        "packages": [
            {
                "action": item.action,
                "current_version": item.current_version,
                "name": item.name,
                "target_version": item.target_version,
            }
            for item in sorted(packages, key=lambda item: item.name)
        ],
        "repository_origin": repository_origin,
        "repository_uris": sorted(repository_uris),
        "server_id": server_id,
        "server_package": server_package,
        "target_upstream_version": target_upstream_version,
        "target_version": target_version,
        "upgrade_kind": upgrade_kind,
    }


def _digest(material: dict) -> str:
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
