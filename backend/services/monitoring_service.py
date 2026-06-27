"""Monitoring, health checks, and dashboard metrics."""

from __future__ import annotations

import json
import os
import platform
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from backend.connectors.base import get_connector, resolve_connection
from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import DashboardStats, DatabaseType, HealthCheckResult, OperationStatus
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

    def _iter_audit_records(self) -> list[dict[str, Any]]:
        audit_path = Path(self.settings.logging.audit_log_path)
        if not audit_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def _backup_audit_stats(
        self, db_type: DatabaseType | None = None
    ) -> tuple[int, int, datetime | None]:
        """Count successful and failed backup operations from the audit log."""
        success = 0
        failed = 0
        last_success: datetime | None = None

        for record in self._iter_audit_records():
            data = record.get("data", {})
            if data.get("action") != "backup":
                continue

            details = data.get("details") or {}
            if db_type:
                rec_db = details.get("db_type")
                if rec_db and rec_db != db_type.value:
                    continue

            status = str(data.get("status", "")).lower()
            ts_raw = record.get("timestamp", "")
            ts: datetime | None = None
            if ts_raw:
                try:
                    ts = datetime.fromisoformat(str(ts_raw))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    ts = None

            if status == "success":
                success += 1
                if ts and (last_success is None or ts > last_success):
                    last_success = ts
            elif status == "failed":
                failed += 1

        return success, failed, last_success

    def count_audit_today(
        self,
        actions: tuple[str, ...] = ("backup", "restore"),
        db_type: DatabaseType | None = None,
    ) -> tuple[int, int]:
        """Count success/failed audit entries for today (local calendar day)."""
        success = 0
        failed = 0
        today = datetime.now().astimezone().date()

        for record in self._iter_audit_records():
            data = record.get("data", {})
            action = str(data.get("action", "")).lower()
            if action not in actions:
                continue

            details = data.get("details") or {}
            if db_type:
                rec_db = details.get("db_type")
                if rec_db and rec_db != db_type.value:
                    continue

            ts_raw = record.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(str(ts_raw))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.astimezone()
            except ValueError:
                continue

            if ts.date() != today:
                continue

            status = str(data.get("status", "")).lower()
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1

        return success, failed

    def count_live_jobs(self) -> tuple[int, int]:
        """Return (running, pending) in-memory backup/restore operations."""
        from backend.services import backup_service, restore_service

        running = 0
        pending = 0
        for module in (backup_service, restore_service):
            for op in getattr(module, "_operations", {}).values():
                if op.status == OperationStatus.RUNNING:
                    running += 1
                elif op.status == OperationStatus.PENDING:
                    pending += 1
        return running, pending

    def _list_backups_for_context(
        self,
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
    ) -> list[dict]:
        if list_remote and db_type == DatabaseType.MONGODB:
            return self.backup_service.list_backups(
                profile,
                local_dir=local_dir,
                remote_dir=remote_dir,
                list_remote=True,
            )
        return self.backup_service.list_local_backups(local_dir)

    def _storage_used_mb(
        self,
        *,
        profile: str = "default",
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
        backups: list[dict] | None = None,
    ) -> float:
        if list_remote:
            try:
                from backend.services.ssh_client import run_ssh_command, ssh_options_from_connection

                conn = resolve_connection(profile)
                ssh = ssh_options_from_connection(conn)
                if ssh:
                    root = remote_dir or ssh.get("remote_backup_dir", "/mnt/backup")
                    cmd = f"du -sb {shlex.quote(root.rstrip('/'))} 2>/dev/null | cut -f1"
                    _code, stdout, _stderr = run_ssh_command(
                        ssh_host=ssh["ssh_host"],
                        fallback_host=ssh["fallback_host"],
                        ssh_port=ssh["ssh_port"],
                        username=ssh["username"],
                        password=ssh["password"],
                        command=cmd,
                    )
                    bytes_used = int(stdout.strip() or "0")
                    if bytes_used > 0:
                        return round(bytes_used / (1024 * 1024), 2)
            except Exception as exc:
                logger.warning("Remote backup storage query failed: %s", exc)

            if backups:
                total = sum(int(b.get("size_bytes") or 0) for b in backups)
                if total > 0:
                    return round(total / (1024 * 1024), 2)
            return 0.0

        scan_dir = self.backup_service._resolve_local_backup_dir(local_dir)
        if not scan_dir.exists():
            return 0.0
        used = sum(f.stat().st_size for f in scan_dir.rglob("*") if f.is_file())
        return round(used / (1024 * 1024), 2)

    def _latest_backup_time(self, backups: list[dict], audit_last: datetime | None) -> datetime | None:
        last_backup: datetime | None = None
        for backup in backups:
            created_raw = backup.get("created_at")
            if not created_raw:
                continue
            try:
                created = datetime.fromisoformat(str(created_raw))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if last_backup is None or created > last_backup:
                    last_backup = created
            except ValueError:
                continue
        if audit_last and (last_backup is None or audit_last > last_backup):
            return audit_last
        return last_backup

    def get_dashboard_stats(
        self,
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
        active_connections: int | None = None,
    ) -> DashboardStats:
        """Aggregate statistics for dashboard display."""
        backups = self._list_backups_for_context(
            profile=profile,
            db_type=db_type,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
        )
        audit_success, audit_failed, audit_last = self._backup_audit_stats(db_type)
        running, pending = self.count_live_jobs()
        storage_used = self._storage_used_mb(
            profile=profile,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
            backups=backups,
        )

        return DashboardStats(
            total_backups=len(backups),
            successful_backups=audit_success,
            failed_backups=audit_failed,
            pending_operations=running + pending,
            last_backup=self._latest_backup_time(backups, audit_last),
            database_connections=(
                active_connections
                if active_connections is not None
                else len(self.settings.connections)
            ),
            patch_count=len(self.patch_service.get_history()),
            storage_used_mb=storage_used,
        )

    def get_backup_report(self) -> dict:
        backups = self.backup_service.list_backups()
        return {
            "total": len(backups),
            "backups": backups[:20],
            "retention_days": self.settings.backup.retention_days,
        }

    def get_audit_logs(self, limit: int = 100) -> list:
        lines = [
            json.dumps(record, ensure_ascii=False)
            for record in self._iter_audit_records()
        ]
        return lines[-limit:]

    def get_recent_activity(self, limit: int = 12) -> list[dict]:
        """Parse audit log into timeline events."""
        events: list[dict] = []
        for record in reversed(self._iter_audit_records()):
            extra = record.get("data", {})
            ts = record.get("timestamp", "")
            if isinstance(ts, str) and "T" in ts:
                ts = ts.split("T")[1][:8]
            events.append({
                "time": ts,
                "action": extra.get("action", record.get("message", "event")),
                "status": extra.get("status", "info"),
                "resource": extra.get("resource", ""),
                "user": extra.get("user", "system"),
            })
            if len(events) >= limit:
                break
        return events

    def get_backup_history_chart(
        self,
        days: int = 7,
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
    ) -> list[dict]:
        """Daily backup counts for the last N days."""
        backups = self._list_backups_for_context(
            profile=profile,
            db_type=db_type,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
        )
        today = datetime.now(timezone.utc).date()
        counts = {today - timedelta(days=i): 0 for i in range(days - 1, -1, -1)}

        for backup in backups:
            try:
                created = datetime.fromisoformat(str(backup["created_at"]))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                day = created.date()
                if day in counts:
                    counts[day] += 1
            except (KeyError, ValueError):
                continue

        # Include successful backup operations without inventory timestamps.
        if not any(counts.values()):
            now = datetime.now(timezone.utc)
            week_start = now - timedelta(days=days)
            for record in self._iter_audit_records():
                data = record.get("data", {})
                if data.get("action") != "backup" or data.get("status") != "success":
                    continue
                details = data.get("details") or {}
                if db_type and details.get("db_type") and details.get("db_type") != db_type.value:
                    continue
                try:
                    created = datetime.fromisoformat(str(record.get("timestamp", "")))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if created < week_start:
                    continue
                day = created.date()
                if day in counts:
                    counts[day] += 1

        return [
            {"label": d.strftime("%a"), "count": counts[d], "date": d.isoformat()}
            for d in sorted(counts.keys())
        ]

    def get_kpi_trends(
        self,
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
        active_connections: int | None = None,
    ) -> dict[str, dict]:
        """Week-over-week trend comparisons for KPI metrics."""
        backups = self._list_backups_for_context(
            profile=profile,
            db_type=db_type,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
        )
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        two_weeks = now - timedelta(days=14)

        def count_in_range(items: list[dict], start: datetime, end: datetime) -> int:
            n = 0
            for item in items:
                try:
                    dt = datetime.fromisoformat(str(item["created_at"]))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if start <= dt < end:
                        n += 1
                except (KeyError, ValueError):
                    pass
            return n

        def audit_count_in_range(start: datetime, end: datetime, status: str) -> int:
            n = 0
            for record in self._iter_audit_records():
                data = record.get("data", {})
                if data.get("action") != "backup" or data.get("status") != status:
                    continue
                details = data.get("details") or {}
                if db_type and details.get("db_type") and details.get("db_type") != db_type.value:
                    continue
                try:
                    dt = datetime.fromisoformat(str(record.get("timestamp", "")))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if start <= dt < end:
                    n += 1
            return n

        this_week = count_in_range(backups, week_ago, now)
        last_week = count_in_range(backups, two_weeks, week_ago)
        if this_week == 0 and last_week == 0:
            this_week = audit_count_in_range(week_ago, now, "success")
            last_week = audit_count_in_range(two_weeks, week_ago, "success")

        def trend(current: int, previous: int) -> dict:
            if previous == 0:
                pct = 100.0 if current > 0 else 0.0
                direction = "up" if current > 0 else "flat"
            else:
                pct = ((current - previous) / previous) * 100
                direction = "up" if pct > 0 else ("down" if pct < 0 else "flat")
            return {"current": current, "previous": previous, "pct": round(pct, 1), "direction": direction}

        success_this = audit_count_in_range(week_ago, now, "success")
        success_last = audit_count_in_range(two_weeks, week_ago, "success")

        patch_history = self.patch_service.get_history()

        def patch_dt(entry):
            dt = entry.executed_at
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

        patches_this = sum(1 for p in patch_history if patch_dt(p) >= week_ago)
        patches_last = sum(1 for p in patch_history if two_weeks <= patch_dt(p) < week_ago)

        storage_mb = self._storage_used_mb(
            profile=profile,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
            backups=backups,
        )
        conn_count = (
            active_connections
            if active_connections is not None
            else len(self.settings.connections)
        )

        return {
            "total_backups": trend(this_week, last_week),
            "successful_backups": trend(success_this, success_last),
            "patch_count": trend(patches_this, patches_last),
            "storage_used_mb": {
                "current": round(storage_mb, 1),
                "previous": round(storage_mb, 1),
                "pct": 0.0,
                "direction": "flat",
            },
            "database_connections": {
                "current": conn_count,
                "previous": conn_count,
                "pct": 0.0,
                "direction": "flat",
            },
            "pending_operations": {"current": 0, "previous": 0, "pct": 0.0, "direction": "flat"},
        }

    def get_storage_usage_percent(
        self,
        max_mb: float = 10240.0,
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
    ) -> float:
        """Backup storage as percentage of configured capacity (default 10 GB)."""
        _ = db_type
        used = self._storage_used_mb(
            profile=profile,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
        )
        return min(100.0, (used / max_mb) * 100) if max_mb > 0 else 0.0
