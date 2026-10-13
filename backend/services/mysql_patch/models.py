"""Typed records for MySQL package patching.

Version kinds follow MySQL's published numbering: major.minor.patch.
The release series is major.minor (8.0 and 8.4 are different series).
A series change is not classified as an ordinary in-series update.
This module does not decide that a series change is a supported upgrade.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class PackageManagerKind(str, Enum):
    APT = "apt"
    UNKNOWN = "unknown"


class PackageOrigin(str, Enum):
    UBUNTU_ARCHIVE = "ubuntu_archive"
    ORACLE_MYSQL_APT = "oracle_mysql_apt"
    MIXED = "mixed"
    UNKNOWN = "unknown"
    NOT_INSTALLED = "not_installed"


class UpgradeKind(str, Enum):
    """How the candidate version differs from the installed version."""

    NONE = "none"
    PACKAGE_REVISION = "package_revision"
    MAINTENANCE = "maintenance"
    RELEASE_SERIES = "release_series"
    MAJOR = "major"
    DOWNGRADE = "downgrade"
    UNKNOWN = "unknown"


class JobOutcome(str, Enum):
    NOT_STARTED = "not_started"
    DRY_RUN_PASSED = "dry_run_passed"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"
    VERIFICATION_FAILED = "verification_failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ExecutionStage(str, Enum):
    CREATED = "created"
    DISCOVERED = "discovered"
    PRECHECK = "precheck"
    PLAN_READY = "plan_ready"
    APPROVED = "approved"
    REVALIDATED = "revalidated"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class BackupReadinessState(str, Enum):
    """What the existing backup check was able to prove."""

    MISSING = "missing"
    UNREADABLE = "unreadable"
    READABLE_UNVERIFIED = "readable_unverified"


class PatchJobSnapshot(BaseModel):
    """Minimal job record used to block a new precheck. Persistence is later."""

    job_id: str
    server_id: str
    outcome: JobOutcome


class DiscoveryCheck(BaseModel):
    """One read-only discovery result."""

    code: str
    status: CheckStatus
    summary: str


class InstalledPackage(BaseModel):
    name: str
    version: str
    status: str


class PackageChange(BaseModel):
    """A single package action inside a later patch plan."""

    name: str
    action: str
    current_version: str | None = None
    target_version: str | None = None


class ApprovalRecord(BaseModel):
    """Human approval bound to one plan digest."""

    approved_by: str
    approved_at: datetime
    plan_digest: str
    high_risk_confirmed: bool = False


class PatchPlan(BaseModel):
    """Immutable snapshot. Approval is attached as a copy; execution is later."""

    plan_id: str
    server_id: str
    created_at: datetime
    current_version: str
    target_version: str
    current_upstream_version: str
    target_upstream_version: str
    server_package: str
    upgrade_kind: UpgradeKind
    repository_origin: PackageOrigin
    repository_uris: list[str] = Field(default_factory=list)
    packages: list[PackageChange] = Field(default_factory=list)
    precheck_findings: list[DiscoveryCheck] = Field(default_factory=list)
    backup_state: BackupReadinessState = BackupReadinessState.MISSING
    recovery_proven: bool = False
    service_interruption_expected: bool = False
    required_privileges: str
    planned_verification: list[str] = Field(default_factory=list)
    high_risk: bool = False
    high_risk_reasons: list[str] = Field(default_factory=list)
    plan_digest: str
    approval: ApprovalRecord | None = None


class PlanRevalidation(BaseModel):
    """Result of comparing an approved plan with a fresh simulation."""

    plan_id: str
    valid: bool = False
    execution_allowed: bool = False
    stop_reason: str | None = None
    commands_run: list[str] = Field(default_factory=list)


class ExecutionReport(BaseModel):
    """One dry-run or later install attempt. Phase 6 does not install packages."""

    job_id: str
    plan_id: str
    server_id: str
    dry_run: bool = True
    packages_installed: bool = False
    retry_attempted: bool = False
    stage: ExecutionStage
    outcome: JobOutcome
    stop_reason: str | None = None
    log_excerpt: str | None = None
    commands_run: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None


class EnvironmentReport(BaseModel):
    """Read-only picture of one server. No secrets."""

    server_id: str
    checked_at: datetime
    os_id: str | None = None
    os_version: str | None = None
    os_pretty_name: str | None = None
    package_manager: PackageManagerKind = PackageManagerKind.UNKNOWN
    mysql_installed: bool = False
    installed_packages: list[InstalledPackage] = Field(default_factory=list)
    mysql_server_package: str | None = None
    mysql_server_version: str | None = None
    mysql_upstream_version: str | None = None
    candidate_version: str | None = None
    candidate_upstream_version: str | None = None
    repository_origin: PackageOrigin = PackageOrigin.UNKNOWN
    repository_uris: list[str] = Field(default_factory=list)
    service_name: str | None = None
    service_state: str | None = None
    upgrade_kind: UpgradeKind = UpgradeKind.UNKNOWN
    environment_supported: bool = False
    patch_available: bool = False
    v1_patch_eligible: bool = False
    stop_reason: str | None = None
    checks: list[DiscoveryCheck] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    transport_error: str | None = None
    output_excerpt: str | None = None


class PrecheckReport(BaseModel):
    """Combined discovery and safety checks. No secrets."""

    server_id: str
    checked_at: datetime
    discovery: EnvironmentReport
    checks: list[DiscoveryCheck] = Field(default_factory=list)
    precheck_passed: bool = False
    ready_for_plan: bool = False
    host_key_verified: bool = False
    recovery_proven: bool = False
    backup_state: BackupReadinessState = BackupReadinessState.MISSING
    service_interruption_expected: bool = False
    stop_reason: str | None = None
    commands_run: list[str] = Field(default_factory=list)


class PackagePreviewReport(BaseModel):
    """Simulated package transaction. Nothing has been installed."""

    server_id: str
    checked_at: datetime
    precheck: PrecheckReport
    changes: list[PackageChange] = Field(default_factory=list)
    accepted: bool = False
    stop_reason: str | None = None
    checks: list[DiscoveryCheck] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    output_excerpt: str | None = None


class VerificationReport(BaseModel):
    """Independent read of the server after a patch attempt. No secrets."""

    server_id: str
    plan_id: str
    checked_at: datetime
    installed_version: str | None = None
    target_version: str
    target_reached: bool = False
    service_name: str | None = None
    service_state: str | None = None
    service_active: bool = False
    sql_checked: bool = False
    sql_ok: bool = False
    package_manager_clean: bool = False
    outcome: JobOutcome = JobOutcome.VERIFICATION_FAILED
    stop_reason: str | None = None
    checks: list[DiscoveryCheck] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)


class MysqlPatchAuditRecord(BaseModel):
    """One stored patch job. Strings are redacted before they are written."""

    job_id: str
    server_id: str
    initiated_by: str = "unknown"
    started_at: datetime
    finished_at: datetime | None = None
    current_version: str
    target_version: str
    plan_id: str
    plan_digest: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    stage: str
    execution_outcome: str
    verification_outcome: str | None = None
    final_status: str
    precheck_summaries: list[str] = Field(default_factory=list)
    error_message: str | None = None
    packages_installed: bool = False
    dry_run: bool = True
