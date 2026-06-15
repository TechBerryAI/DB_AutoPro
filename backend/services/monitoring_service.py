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

    def get_recent_activity(self, limit: int = 12) -> list[dict]:
        """Parse audit log into timeline events."""
        import json

        events: list[dict] = []
        for line in reversed(self.get_audit_logs(limit * 2)):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                extra = data.get("data", {})
                ts = data.get("timestamp", "")
                if isinstance(ts, str) and "T" in ts:
                    ts = ts.split("T")[1][:8]
                events.append({
                    "time": ts,
                    "action": extra.get("action", data.get("message", "event")),
                    "status": extra.get("status", "info"),
                    "resource": extra.get("resource", ""),
                    "user": extra.get("user", "system"),
                })
            except json.JSONDecodeError:
                events.append({
                    "time": "",
                    "action": line[:80],
                    "status": "info",
                    "resource": "",
                    "user": "system",
                })
            if len(events) >= limit:
                break
        return events

    def get_backup_history_chart(self, days: int = 7) -> list[dict]:
        """Daily backup counts for the last N days."""
        from datetime import timedelta

        backups = self.backup_service.list_backups()
        today = datetime.now(timezone.utc).date()
        counts = {today - timedelta(days=i): 0 for i in range(days - 1, -1, -1)}

        for backup in backups:
            try:
                created = datetime.fromisoformat(backup["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                day = created.date()
                if day in counts:
                    counts[day] += 1
            except (KeyError, ValueError):
                continue

        return [
            {"label": d.strftime("%a"), "count": counts[d], "date": d.isoformat()}
            for d in sorted(counts.keys())
        ]

    def get_kpi_trends(self) -> dict[str, dict]:
        """Week-over-week trend comparisons for KPI metrics."""
        from datetime import timedelta

        backups = self.backup_service.list_backups()
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        two_weeks = now - timedelta(days=14)

        def count_in_range(items, start, end, date_key="created_at"):
            n = 0
            for item in items:
                try:
                    dt = datetime.fromisoformat(item[date_key])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if start <= dt < end:
                        n += 1
                except (KeyError, ValueError):
                    pass
            return n

        this_week = count_in_range(backups, week_ago, now)
        last_week = count_in_range(backups, two_weeks, week_ago)

        def trend(current: int, previous: int) -> dict:
            if previous == 0:
                pct = 100.0 if current > 0 else 0.0
                direction = "up" if current > 0 else "flat"
            else:
                pct = ((current - previous) / previous) * 100
                direction = "up" if pct > 0 else ("down" if pct < 0 else "flat")
            return {"current": current, "previous": previous, "pct": round(pct, 1), "direction": direction}

        patch_history = self.patch_service.get_history()
        def patch_dt(entry):
            dt = entry.executed_at
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

        patches_this = sum(1 for p in patch_history if patch_dt(p) >= week_ago)
        patches_last = sum(1 for p in patch_history if two_weeks <= patch_dt(p) < week_ago)

        backup_dir = Path(self.settings.backup.output_dir)
        storage_mb = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file()) / (1024 * 1024) if backup_dir.exists() else 0.0

        return {
            "total_backups": trend(this_week, last_week),
            "successful_backups": trend(this_week, last_week),
            "patch_count": trend(patches_this, patches_last),
            "storage_used_mb": {
                "current": round(storage_mb, 1),
                "previous": round(storage_mb, 1),
                "pct": 0.0,
                "direction": "flat",
            },
            "database_connections": {"current": len(self.settings.connections), "previous": len(self.settings.connections), "pct": 0.0, "direction": "flat"},
            "pending_operations": {"current": 0, "previous": 0, "pct": 0.0, "direction": "flat"},
        }

    def get_storage_usage_percent(self, max_mb: float = 10240.0) -> float:
        """Backup storage as percentage of configured capacity (default 10 GB)."""
        backup_dir = Path(self.settings.backup.output_dir)
        if not backup_dir.exists():
            return 0.0
        used = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file()) / (1024 * 1024)
        return min(100.0, (used / max_mb) * 100) if max_mb > 0 else 0.0
