"""Scheduled backup service using APScheduler."""

from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import BackupMethod, BackupRequest, DatabaseType
from backend.services.backup_service import BackupService

logger = get_logger("services.scheduler")

_scheduler: BackgroundScheduler | None = None


class SchedulerService:
    """Manages scheduled backup jobs and retention cleanup."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.backup_service = BackupService()
        self._jobs: dict[str, str] = {}

    def start(self) -> None:
        global _scheduler
        if _scheduler is not None and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        logger.info("Scheduler started")

        cron = self.settings.backup.schedule_cron
        if cron:
            self.schedule_default_backup(cron)

    def schedule_default_backup(self, cron_expression: str) -> str:
        """Schedule default backup using cron expression."""
        global _scheduler
        if _scheduler is None:
            self.start()

        parts = cron_expression.split()
        if len(parts) != 5:
            logger.warning("Invalid cron expression: %s", cron_expression)
            return ""

        minute, hour, day, month, day_of_week = parts
        job_id = "default_backup"

        def _run_backup() -> None:
            request = BackupRequest(
                db_type=DatabaseType(self.settings.database_type),
                method=BackupMethod.PG_DUMP,
                compress=True,
            )
            try:
                self.backup_service.create_backup(request)
                deleted = self.backup_service.apply_retention_policy()
                logger.info("Scheduled backup completed; retention deleted %d files", deleted)
            except Exception as exc:
                logger.error("Scheduled backup failed: %s", exc)

        _scheduler.add_job(
            _run_backup,
            CronTrigger(
                minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week
            ),
            id=job_id,
            replace_existing=True,
        )
        self._jobs[job_id] = cron_expression
        logger.info("Scheduled backup job: %s", cron_expression)
        return job_id

    def schedule_retention_cleanup(self, interval_hours: int = 24) -> str:
        global _scheduler
        if _scheduler is None:
            self.start()

        job_id = "retention_cleanup"
        _scheduler.add_job(
            self.backup_service.apply_retention_policy,
            "interval",
            hours=interval_hours,
            id=job_id,
            replace_existing=True,
        )
        return job_id

    def list_jobs(self) -> list[dict]:
        global _scheduler
        if _scheduler is None:
            return []
        return [
            {"id": job.id, "next_run": str(job.next_run_time), "trigger": str(job.trigger)}
            for job in _scheduler.get_jobs()
        ]

    def stop(self) -> None:
        global _scheduler
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)
            _scheduler = None
            logger.info("Scheduler stopped")
