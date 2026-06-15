"""Monitoring, health checks, and dashboard metrics."""

from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from pathlib import Path

import psutil

from backend.connectors.base import get_connector
from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import DashboardStats, DatabaseType, HealthCheckResult
from backend.services.backup_service import BackupService
from backend.services.patch_service import PatchService

logger = get_logger("services.monitoring")


class MonitoringService:
    """Provides health checks, metrics, and dashboard statistics."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.backup_service = BackupService()
        self.patch_service = PatchService()

    def health_check(self) -> list[HealthCheckResult]:
        """Run health checks on application and configured databases."""
        results: list[HealthCheckResult] = []

        results.append(HealthCheckResult(
            service="application",
            status="healthy",
            details={"version": "1.0.0", "environment": self.settings.environment},
        ))

        for profile in self.settings.connections:
            try:
                db_type = DatabaseType(self.settings.database_type)
                connector = get_connector(db_type, profile)
                health = connector.health_check()
                results.append(HealthCheckResult(
                    service=f"database:{profile}",
                    status=health.get("status", "unknown"),
                    latency_ms=health.get("latency_ms"),
                    details=health,
                ))
            except Exception as exc:
                results.append(HealthCheckResult(
                    service=f"database:{profile}",
                    status="unhealthy",
                    details={"error": str(exc)},
                ))

        backup_dir = Path(self.settings.backup.output_dir)
        results.append(HealthCheckResult(
            service="backup_storage",
            status="healthy" if backup_dir.exists() else "degraded",
            details={
                "path": str(backup_dir),
                "writable": os.access(backup_dir, os.W_OK) if backup_dir.exists() else False,
            },
        ))

        return results

    def get_system_metrics(self) -> dict:
        """Collect system resource metrics."""
        disk = psutil.disk_usage("/")
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory": {
                "total_mb": round(psutil.virtual_memory().total / (1024 * 1024), 2),
                "used_mb": round(psutil.virtual_memory().used / (1024 * 1024), 2),
                "percent": psutil.virtual_memory().percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024 ** 3), 2),
                "used_gb": round(disk.used / (1024 ** 3), 2),
                "percent": disk.percent,
            },
            "platform": platform.platform(),
        }

    def get_dashboard_stats(self) -> DashboardStats:
        """Aggregate statistics for dashboard display."""
        backups = self.backup_service.list_backups()
        backup_dir = Path(self.settings.backup.output_dir)
        storage_used = sum(
            f.stat().st_size for f in backup_dir.rglob("*") if f.is_file()
        ) / (1024 * 1024) if backup_dir.exists() else 0.0

        last_backup = None
        if backups:
            last_backup = datetime.fromisoformat(backups[0]["created_at"])

        return DashboardStats(
            total_backups=len(backups),
            successful_backups=len(backups),
            failed_backups=0,
            pending_operations=0,
            last_backup=last_backup,
            database_connections=len(self.settings.connections),
            patch_count=len(self.patch_service.get_history()),
            storage_used_mb=round(storage_used, 2),
        )

    def get_backup_report(self) -> dict:
        backups = self.backup_service.list_backups()
        return {
            "total": len(backups),
            "backups": backups[:20],
            "retention_days": self.settings.backup.retention_days,
        }

    def get_audit_logs(self, limit: int = 100) -> list:
        audit_path = Path(self.settings.logging.audit_log_path)
        if not audit_path.exists():
            return []
        lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
        return lines[-limit:]
