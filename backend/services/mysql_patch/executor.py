"""Guarded MySQL patch executor.

Phase 6 runs a dry-run only. It revalidates the approved plan, records the
stage it reached, and does not send an install, upgrade, reboot, or retry.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from backend.services.mysql_patch.command_runner import CommandRunner
from backend.services.mysql_patch.models import (
    ExecutionReport,
    ExecutionStage,
    JobOutcome,
    PatchPlan,
)
from backend.services.mysql_patch.plan_service import PlanService
from backend.services.mysql_patch.redaction import redact_text

StageCallback = Callable[[ExecutionStage, str], None]


class ServerJobGuard:
    """Stops two patch jobs for the same server from running together."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, server_id: str) -> bool:
        with self._lock:
            if server_id in self._active:
                return False
            self._active.add(server_id)
            return True

    def release(self, server_id: str) -> None:
        with self._lock:
            self._active.discard(server_id)


class PatchExecutor:
    """Revalidate an approved plan, then stop before any package install."""

    def __init__(
        self,
        plans: PlanService | None = None,
        guard: ServerJobGuard | None = None,
    ) -> None:
        self._plans = plans or PlanService()
        self._guard = guard or ServerJobGuard()

    def execute(
        self,
        runner: CommandRunner,
        plan: PatchPlan,
        *,
        backup_path: str | None = None,
        mode: str = "dry_run",
        on_stage: StageCallback | None = None,
    ) -> ExecutionReport:
        """Dry-run the approved plan. `mode="execute"` is refused."""
        started = datetime.now(timezone.utc)
        if mode != "dry_run":
            return self._report(
                plan,
                started=started,
                stage=ExecutionStage.FAILED,
                outcome=JobOutcome.FAILED,
                stop_reason="Only dry-run execution is enabled. Package installation was not started.",
            )
        if not self._guard.acquire(plan.server_id):
            return self._report(
                plan,
                started=started,
                stage=ExecutionStage.FAILED,
                outcome=JobOutcome.FAILED,
                stop_reason=f"A patch job is already running for {plan.server_id}",
            )
        try:
            return self._dry_run(runner, plan, backup_path=backup_path, on_stage=on_stage, started=started)
        finally:
            self._guard.release(plan.server_id)

    def _dry_run(
        self,
        runner: CommandRunner,
        plan: PatchPlan,
        *,
        backup_path: str | None,
        on_stage: StageCallback | None,
        started: datetime,
    ) -> ExecutionReport:
        stage = ExecutionStage.REVALIDATED
        self._notify(on_stage, stage, "Revalidating the approved plan")
        try:
            checked = self._plans.revalidate(runner, plan, backup_path=backup_path)
        except Exception as exc:
            message = redact_text(str(exc)) or "Dry-run failed"
            return self._report(
                plan,
                started=started,
                stage=stage,
                outcome=outcome_after_failure(error_text=message, install_was_sent=False),
                stop_reason=_unfinished(message),
                log_excerpt=message,
            )

        if not checked.execution_allowed:
            message = redact_text(checked.stop_reason) or "Approved plan is not executable"
            return self._report(
                plan,
                started=started,
                stage=stage,
                outcome=outcome_after_failure(error_text=message, install_was_sent=False),
                stop_reason=_unfinished(message),
                commands=checked.commands_run,
                log_excerpt=message,
            )

        if _install_was_sent(checked.commands_run):
            return self._report(
                plan,
                started=started,
                stage=ExecutionStage.EXECUTING,
                outcome=JobOutcome.PARTIALLY_COMPLETED,
                stop_reason=(
                    "An install command was observed during revalidation. "
                    "No automatic retry will be attempted."
                ),
                commands=checked.commands_run,
            )

        stage = ExecutionStage.EXECUTING
        self._notify(on_stage, stage, "Dry-run confirmed. No install command will be sent.")
        stage = ExecutionStage.COMPLETED
        self._notify(on_stage, stage, "Dry-run matched the approved plan")
        return self._report(
            plan,
            started=started,
            stage=stage,
            outcome=JobOutcome.DRY_RUN_PASSED,
            stop_reason=None,
            commands=checked.commands_run,
            log_excerpt="Dry-run matched the approved plan. No packages were installed.",
        )

    def _notify(self, on_stage: StageCallback | None, stage: ExecutionStage, message: str) -> None:
        if on_stage is not None:
            on_stage(stage, message)

    def _report(
        self,
        plan: PatchPlan,
        *,
        started: datetime,
        stage: ExecutionStage,
        outcome: JobOutcome,
        stop_reason: str | None,
        commands: list[str] | None = None,
        log_excerpt: str | None = None,
    ) -> ExecutionReport:
        return ExecutionReport(
            job_id=str(uuid.uuid4()),
            plan_id=plan.plan_id,
            server_id=plan.server_id,
            dry_run=True,
            packages_installed=False,
            retry_attempted=False,
            stage=stage,
            outcome=outcome,
            stop_reason=stop_reason,
            log_excerpt=redact_text(log_excerpt),
            commands_run=list(commands or []),
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )


def outcome_after_failure(*, error_text: str, install_was_sent: bool) -> JobOutcome:
    """Map a failed dry-run to an outcome. A sent install is never retried here."""
    if install_was_sent:
        return JobOutcome.PARTIALLY_COMPLETED
    lowered = error_text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return JobOutcome.INTERRUPTED
    if any(
        token in lowered
        for token in ("disconnect", "connection reset", "broken pipe", "connection aborted", "not connected")
    ):
        return JobOutcome.UNKNOWN
    return JobOutcome.FAILED


def _unfinished(message: str) -> str:
    return f"Dry-run did not finish. No package install was sent. {message}"


def _install_was_sent(commands: list[str]) -> bool:
    for command in commands:
        lowered = command.lower()
        if "apt-get install" in lowered or "apt-get upgrade" in lowered or "apt install" in lowered:
            return True
        if lowered.startswith("apt-get ") and " -s " not in f" {lowered} " and "install" in lowered:
            return True
    return False
