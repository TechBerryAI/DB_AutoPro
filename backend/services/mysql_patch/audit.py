"""JSON history for MySQL package-patch jobs.

Records live in ``var/data``, separate from SQL script patch history.
Passwords and other secrets are redacted and never given their own fields.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.core.paths import DATA_DIR
from backend.services.mysql_patch.models import (
    ExecutionReport,
    JobOutcome,
    MysqlPatchAuditRecord,
    PatchJobSnapshot,
    PatchPlan,
    VerificationReport,
)
from backend.services.mysql_patch.redaction import redact_text

_UNSAFE = {
    JobOutcome.FAILED.value,
    JobOutcome.PARTIALLY_COMPLETED.value,
    JobOutcome.VERIFICATION_FAILED.value,
    JobOutcome.INTERRUPTED.value,
    JobOutcome.UNKNOWN.value,
}
_SECRET_KEYS = ("password", "passwd", "secret", "token", "private_key", "linux_password")


def default_audit_path() -> Path:
    """Runtime history file. Tests should pass their own path."""
    return DATA_DIR / "mysql_patch_jobs.json"


def final_status(execution: ExecutionReport, verification: VerificationReport | None) -> JobOutcome:
    """A dry-run stays a dry-run. Verification decides only after packages were installed."""
    if execution.dry_run or not execution.packages_installed:
        return execution.outcome
    if verification is not None:
        return verification.outcome
    return execution.outcome


class MysqlPatchAuditStore:
    """Append-only job history used by prechecks and later reports."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_audit_path()
        self._lock = threading.Lock()

    def record(
        self,
        execution: ExecutionReport,
        plan: PatchPlan,
        *,
        initiated_by: str = "unknown",
        verification: VerificationReport | None = None,
    ) -> MysqlPatchAuditRecord:
        """Store one job and return the sanitized record."""
        status = final_status(execution, verification)
        error = execution.stop_reason
        if verification is not None and verification.stop_reason:
            error = verification.stop_reason
        approval = plan.approval
        entry = MysqlPatchAuditRecord(
            job_id=execution.job_id,
            server_id=plan.server_id,
            initiated_by=_clean_actor(initiated_by),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            current_version=plan.current_version,
            target_version=plan.target_version,
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            approved_by=None if approval is None else approval.approved_by,
            approved_at=None if approval is None else approval.approved_at,
            stage=execution.stage.value,
            execution_outcome=execution.outcome.value,
            verification_outcome=None if verification is None else verification.outcome.value,
            final_status=status.value,
            precheck_summaries=[item.summary for item in plan.precheck_findings],
            error_message=redact_text(error),
            packages_installed=execution.packages_installed,
            dry_run=execution.dry_run,
        )
        payload = _sanitize(entry.model_dump(mode="json"))
        stored = MysqlPatchAuditRecord.model_validate(payload)
        with self._lock:
            rows = self._read_unlocked()
            rows.append(stored.model_dump(mode="json"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return stored

    def history(self, server_id: str | None = None) -> list[MysqlPatchAuditRecord]:
        with self._lock:
            rows = self._read_unlocked()
        records = [MysqlPatchAuditRecord.model_validate(row) for row in rows]
        if server_id is None:
            return records
        return [row for row in records if row.server_id == server_id]

    def jobs_for(self, server_id: str) -> list[PatchJobSnapshot]:
        """Expose stored outcomes to the precheck job-history check."""
        return [
            PatchJobSnapshot(job_id=row.job_id, server_id=row.server_id, outcome=JobOutcome(row.final_status))
            for row in self.history(server_id)
        ]

    def _read_unlocked(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(loaded, list):
            return []
        return [row for row in loaded if isinstance(row, dict)]


def _clean_actor(value: str) -> str:
    cleaned = (redact_text(value) or "unknown").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:80] or "unknown"


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict = {}
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in _SECRET_KEYS):
                continue
            cleaned[key] = _sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
