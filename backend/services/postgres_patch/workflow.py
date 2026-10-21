"""Reviewed PostgreSQL SQL patch execution. Does not install server packages."""

from __future__ import annotations

from dataclasses import dataclass

from backend.connectors.base import get_connector
from backend.core.exceptions import ConnectorError, PatchError
from backend.core.logging import get_audit_logger
from backend.models.schemas import DatabaseType, PatchRequest
from backend.services.patch_service import PatchService
from backend.services.postgres_patch.admin_check import PostgresAdminReport, verify_after_patch
from backend.services.postgres_patch.identity import DatabaseIdentityError, patch_identity_key, read_database_identity
from backend.services.postgres_patch.script_runner import execute_statements
from backend.services.postgres_patch.sql_patch import PatchFileReview

RECOVERY_GUIDANCE = (
    "This page does not roll back SQL automatically. "
    "A completed patch is not undone by a failed verification check. "
    "Recovery needs a reviewed script or a restore that an approved process has already proven."
)

_NO_SEPARATE_APPROVER = "Confirmed by the signed-in operator. No separate approver was recorded."


@dataclass
class PatchRunResult:
    executed: bool
    execution_status: str
    verification_status: str
    summary: str
    patch_id: str = ""


def format_operator_review(report: PostgresAdminReport, review: PatchFileReview) -> str:
    lines = ["Precheck results:"]
    lines.extend(f"[{item.status.value}] {item.code}: {item.summary}" for item in report.checks)
    lines.append("")
    lines.append(f"Patch name: {review.name}")
    lines.append(f"Target database: {review.target_database}")
    lines.append(f"Description: {review.description or '(none)'}")
    lines.append(f"Checksum SHA-256: {review.checksum_sha256}")
    if review.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in review.warnings)
    else:
        lines.append("Warnings: none from the keyword hint scan.")
    lines.append("Transaction: transactional statements run together and commit once.")
    lines.append("If one of those statements fails, the transaction is rolled back and the script is not retried.")
    lines.append("VACUUM, CREATE INDEX CONCURRENTLY, and an explicit COMMIT cannot be rolled back with the rest of the file.")
    lines.append(_NO_SEPARATE_APPROVER)
    lines.append(RECOVERY_GUIDANCE)
    return "\n".join(lines)


def format_history_lines(entries) -> str:
    if not entries:
        return "No PostgreSQL administration patch is recorded for this database."
    lines = []
    for entry in reversed(list(entries)):
        duration = entry.duration_seconds
        duration_text = f"{duration}s" if duration is not None else "unknown"
        outcome = entry.status.value
        if entry.status.value == "completed" and entry.verification_status == "failed":
            outcome = "verification_failed"
        lines.append(
            " ".join(
                [
                    str(entry.executed_at),
                    outcome,
                    entry.patch_name or entry.sql_file,
                    f"database={entry.target_database or 'unknown'}",
                    f"checksum={entry.checksum_sha256 or 'none'}",
                    f"operator={entry.executed_by}",
                    f"confirmed_by={entry.confirmed_by or 'none'}",
                    f"duration={duration_text}",
                    f"execution={entry.apply_result or entry.status.value}",
                    f"verification={entry.verification_status or 'not_recorded'}",
                ]
            )
        )
        if entry.error:
            lines.append(f"error: {entry.error}")
        if entry.verification_summary:
            lines.append(entry.verification_summary)
    return "\n".join(lines)


def run_reviewed_patch(
    *,
    report: PostgresAdminReport | None,
    review: PatchFileReview | None,
    confirmed: bool,
    operator: str,
    service: PatchService,
    target_host: str = "",
    verify_query=None,
    profile: str = "default",
    script_executor=None,
    on_progress=None,
) -> PatchRunResult:
    """Execute one reviewed SQL file. Refuses critical precheck failures and does not retry."""
    if report is None or not report.ready_to_patch:
        return _closed("Execution stays closed until administration checks pass with no critical failure.")
    if review is None or not review.valid:
        return _closed(review.message if review and review.message else "The SQL file is not valid for execution.")
    if review.duplicate_of and not review.allow_repeat:
        return _closed("This patch checksum already completed on this database cluster.")
    if review.interrupted and not review.reconcile_interrupted:
        return _closed("This patch was interrupted. Reconcile it before retrying. It was not run again.")
    if not confirmed:
        return _closed("Confirm the review as the signed-in operator before execution.")
    operator_name = operator.strip()
    if not operator_name:
        return _closed("Operator identity is missing, so the patch was not executed.")
    if verify_query is None:
        return _closed("Post-patch verification is unavailable, so the patch was not executed.")
    identity = patch_identity_key(review.checksum_sha256, review.system_identifier, review.database_oid)
    if identity in service.completed_patch_identities() and not review.allow_repeat:
        return _closed("This patch checksum already completed on this database cluster.")
    if not (target_host or "").strip() or (target_host or "").strip() != review.configured_endpoint:
        return _closed("The configured server no longer matches the reviewed server. The patch was not executed.")
    try:
        live = read_database_identity(verify_query)
    except DatabaseIdentityError as exc:
        return _closed(str(exc))
    mismatch = _identity_mismatch(review, live)
    if mismatch:
        return _closed(mismatch)

    def _executor(sql: str) -> None:
        if script_executor is not None:
            script_executor(sql)
            return
        connector = get_connector(DatabaseType.POSTGRESQL, profile)
        connection = connector.open_connection()
        try:
            execute_statements(
                connection,
                sql,
                allow_non_transactional=review.allow_non_transactional,
                on_progress=on_progress,
            )
        finally:
            connection.close()

    try:
        entry = service.execute_patch(
            PatchRequest(
                profile=profile,
                db_type=DatabaseType.POSTGRESQL,
                sql_file=review.path,
                description=review.description,
            ),
            user=operator_name,
            target_database=review.target_database,
            target_host=target_host,
            checksum_sha256=review.checksum_sha256,
            confirmed_by=operator_name,
            patch_name=review.name,
            system_identifier=review.system_identifier,
            database_oid=review.database_oid,
            redact_literals=True,
            executor=_executor,
            audit_result=False,
        )
    except PatchError as exc:
        if (exc.details or {}).get("history_saved") is False:
            return PatchRunResult(
                executed=True,
                execution_status="failed",
                verification_status="not_run",
                summary=(
                    f"{exc} The SQL may already have run. Do not treat this patch as successful or verified."
                ),
                patch_id=str((exc.details or {}).get("patch_id") or ""),
            )
        return _failed_execution(exc, service, verify_query, review.target_database)

    verification_status, verification_summary = verify_after_patch(verify_query, review.target_database)
    verification_summary = f"{verification_summary} {RECOVERY_GUIDANCE}"
    service.annotate_patch(
        entry.patch_id,
        verification_status=verification_status,
        verification_summary=verification_summary,
        apply_result="committed",
    )
    _audit(operator_name, review.name, entry.patch_id, verification_status)
    if verification_status != "passed":
        summary = (
            f"Verification failed. The SQL was committed, but this patch is not verified. "
            f"Patch {entry.patch_id}. {verification_summary}"
        )
    else:
        summary = (
            f"Execution completed. Verification passed. "
            f"Patch {entry.patch_id}. Duration {entry.duration_seconds}s. {_NO_SEPARATE_APPROVER} "
            f"{verification_summary}"
        )
    return PatchRunResult(
        executed=True,
        execution_status=entry.status.value,
        verification_status=verification_status,
        summary=summary,
        patch_id=entry.patch_id,
    )


def _closed(message: str) -> PatchRunResult:
    return PatchRunResult(
        executed=False,
        execution_status="not_run",
        verification_status="not_run",
        summary=message,
    )


def _identity_mismatch(review: PatchFileReview, live) -> str | None:
    """Return a block message when the live target is not the reviewed target."""
    if live.database_name != review.target_database:
        return (
            f"The connected database is {live.database_name}, not {review.target_database}. "
            "The patch was not executed."
        )
    if live.system_identifier != review.system_identifier:
        return (
            "The connected PostgreSQL cluster does not match the reviewed cluster. "
            "The patch was not executed."
        )
    if live.database_oid != review.database_oid:
        return (
            "The connected database OID does not match the reviewed database. "
            "The patch was not executed."
        )
    return None


def _apply_result(exc: PatchError) -> str:
    cause = exc.__cause__
    named = getattr(cause, "apply_result", "")
    if named:
        return str(named)
    if isinstance(cause, ConnectorError):
        return "rolled_back"
    return "not_committed"


def _failed_execution(exc: PatchError, service: PatchService, verify_query, database: str) -> PatchRunResult:
    patch_id = str((exc.details or {}).get("patch_id") or "")
    if not patch_id:
        return _closed(str(exc))
    apply_result = _apply_result(exc)
    summary = f"Execution failed. {RECOVERY_GUIDANCE}"
    verification_status = "not_run"
    if apply_result == "partial":
        verification_status, verification_detail = verify_after_patch(verify_query, database)
        summary = (
            f"Partial failure. At least one statement was committed and was not rolled back. "
            f"Verification {verification_status}. {verification_detail} {RECOVERY_GUIDANCE}"
        )
    service.annotate_patch(
        patch_id,
        verification_status=verification_status,
        verification_summary=summary,
        apply_result=apply_result,
    )
    return PatchRunResult(
        executed=True,
        execution_status="failed",
        verification_status=verification_status,
        summary=f"{exc}\n{summary}",
        patch_id=patch_id,
    )


def _audit(operator: str, patch_name: str, patch_id: str, verification_status: str) -> None:
    status = "success" if verification_status == "passed" else "failed"
    get_audit_logger().log(
        "patch_execute",
        operator,
        patch_name,
        status,
        {"patch_id": patch_id, "verification": verification_status},
    )
