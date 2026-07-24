"""Monitoring, health checks, and dashboard metrics."""

from __future__ import annotations

import json
import os
import platform
import shlex
import socket
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import psutil

from backend.connectors.base import get_connector, resolve_connection
from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import DashboardStats, DatabaseType, HealthCheckResult, OperationStatus
from backend.services.backup_service import BackupService
from backend.services.patch_service import PatchService

logger = get_logger("services.monitoring")

ReportPeriod = Literal["today", "monthly", "yearly", "custom"]

_NETWORK_BAR_CAP_BPS = 10 * 1024 * 1024  # 10 MB/s reference for utilization bar


@dataclass(frozen=True)
class DashboardConnectionScope:
    """Active database session used to scope dashboard audit aggregates."""

    resource: str
    host: str
    db_type: DatabaseType
    profile: str = "default"
    local_dir: str | None = None
    database: str | None = None


def audit_record_matches_scope(data: dict[str, Any], scope: DashboardConnectionScope | None) -> bool:
    """Return True when an audit record belongs to the active connected database."""
    if scope is None:
        return False

    action = str(data.get("action", "")).lower()
    details = data.get("details") or {}
    resource = str(data.get("resource", ""))
    resource_l = resource.lower()
    scope_resource = scope.resource.lower()
    scope_host = scope.host.lower()
    rec_db = details.get("db_type")

    if rec_db and rec_db != scope.db_type.value:
        return False

    if action == "connect":
        if resource_l == scope_resource:
            return True
        rec_host = str(details.get("host", "")).lower()
        return rec_host == scope_host

    if action in ("backup", "restore"):
        if rec_db == scope.db_type.value:
            return True
        if scope.local_dir:
            norm = scope.local_dir.lower().replace("\\", "/")
            if norm and norm in resource_l.replace("\\", "/"):
                return True
        if resource_l == scope.profile.lower():
            return True
        return False

    return False


def empty_security_dashboard(days: int = 7) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    day_keys = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    return {
        "posture": {"failed_logins": 0, "security_alerts": 0, "compliance": "Compliant"},
        "overview": {"critical": 0, "warning": 0, "info": 0, "total": 0},
        "trend": [
            {"label": d.strftime("%d %b"), "critical": 0, "warning": 0, "info": 0}
            for d in day_keys
        ],
        "recent_alerts": [],
        "top_risks": [],
    }


def empty_backup_analytics(days: int = 7) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    day_keys = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    return {
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "total": 0,
        "success_rate": 0.0,
        "storage_mb": 0.0,
        "storage_gb": 0.0,
        "trend": [
            {"label": d.strftime("%d %b"), "successful": 0, "failed": 0, "skipped": 0}
            for d in day_keys
        ],
    }


def _disk_usage_path() -> str:
    if platform.system() == "Windows":
        return os.environ.get("SystemDrive", "C:") + os.sep
    return "/"


def _format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_bytes_per_sec(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 * 1024):.1f} MB/s"


class MonitoringService:
    """Provides health checks, metrics, and dashboard statistics."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.backup_service = BackupService()
        self.patch_service = PatchService()
        self._audit_cache: list[dict[str, Any]] | None = None
        self._audit_cache_mtime: float = -1.0
        self._net_sample: tuple[float, int, int] | None = None
        self._cpu_ready = False

    def health_check(self, *, check_databases: bool = True) -> list[HealthCheckResult]:
        """Run health checks on application and optionally configured databases."""
        results: list[HealthCheckResult] = []

        results.append(HealthCheckResult(
            service="application",
            status="healthy",
            details={"version": "1.0.0", "environment": self.settings.environment},
        ))

        if check_databases:
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
        """Collect live system resource metrics from the local host."""
        disk_path = _disk_usage_path()
        disk = psutil.disk_usage(disk_path)
        memory = psutil.virtual_memory()
        now = time.time()

        if not self._cpu_ready:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            self._cpu_ready = True
        else:
            cpu_percent = psutil.cpu_percent(interval=None)

        freq = psutil.cpu_freq()
        cpu_freq_mhz = round(freq.current, 0) if freq and freq.current else None

        counters = psutil.net_io_counters()
        sent_bps = 0.0
        recv_bps = 0.0
        if counters:
            if self._net_sample is not None:
                elapsed = max(now - self._net_sample[0], 0.001)
                sent_bps = max(0.0, (counters.bytes_sent - self._net_sample[1]) / elapsed)
                recv_bps = max(0.0, (counters.bytes_recv - self._net_sample[2]) / elapsed)
            self._net_sample = (now, counters.bytes_sent, counters.bytes_recv)

        total_bps = sent_bps + recv_bps
        throughput_pct = min(100.0, (total_bps / _NETWORK_BAR_CAP_BPS) * 100) if total_bps else 0.0

        boot_ts = psutil.boot_time()
        uptime_seconds = max(0.0, now - boot_ts)
        hostname = socket.gethostname()
        os_label = f"{platform.system()} {platform.release()}"

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": cpu_percent,
            "cpu_count": psutil.cpu_count(logical=True) or 0,
            "cpu_freq_mhz": cpu_freq_mhz,
            "memory": {
                "total_mb": round(memory.total / (1024 * 1024), 2),
                "used_mb": round(memory.used / (1024 * 1024), 2),
                "available_mb": round(memory.available / (1024 * 1024), 2),
                "percent": memory.percent,
            },
            "disk": {
                "path": disk_path,
                "total_gb": round(disk.total / (1024 ** 3), 2),
                "used_gb": round(disk.used / (1024 ** 3), 2),
                "free_gb": round(disk.free / (1024 ** 3), 2),
                "percent": disk.percent,
            },
            "network": {
                "bytes_sent": counters.bytes_sent if counters else 0,
                "bytes_recv": counters.bytes_recv if counters else 0,
                "sent_bps": round(sent_bps, 1),
                "recv_bps": round(recv_bps, 1),
                "total_bps": round(total_bps, 1),
                "throughput_pct": round(throughput_pct, 1),
                "sent_label": _format_bytes_per_sec(sent_bps),
                "recv_label": _format_bytes_per_sec(recv_bps),
                "total_label": _format_bytes_per_sec(total_bps),
            },
            "host": {
                "hostname": hostname,
                "os": os_label,
                "uptime_seconds": round(uptime_seconds),
                "uptime_label": _format_uptime(uptime_seconds),
                "boot_time": datetime.fromtimestamp(boot_ts, tz=timezone.utc).isoformat(),
                "process_count": len(psutil.pids()),
            },
            "platform": platform.platform(),
        }

    def _iter_audit_records(self) -> list[dict[str, Any]]:
        audit_path = Path(self.settings.logging.audit_log_path)
        if not audit_path.exists():
            self._audit_cache = []
            self._audit_cache_mtime = -1.0
            return []

        mtime = audit_path.stat().st_mtime
        if self._audit_cache is not None and mtime == self._audit_cache_mtime:
            return self._audit_cache

        records: list[dict[str, Any]] = []
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        self._audit_cache = records
        self._audit_cache_mtime = mtime
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

    def _timestamp_in_period(
        self,
        ts: datetime,
        period: ReportPeriod,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> bool:
        local = ts.astimezone()
        if period == "custom":
            if not date_from or not date_to:
                return False
            day = local.date()
            return date_from <= day <= date_to
        now = datetime.now().astimezone()
        if period == "today":
            return local.date() == now.date()
        if period == "monthly":
            return local.year == now.year and local.month == now.month
        return local.year == now.year

    def _period_label(
        self,
        period: ReportPeriod,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> str:
        now = datetime.now().astimezone()
        if period == "custom" and date_from and date_to:
            return (
                f"Custom Report ({date_from.strftime('%d %b %Y')} – "
                f"{date_to.strftime('%d %b %Y')})"
            )
        if period == "today":
            return f"Today's Report ({now.strftime('%d %B %Y')})"
        if period == "monthly":
            return f"Monthly Report ({now.strftime('%B %Y')})"
        return f"Yearly Report ({now.year})"

    def count_audit_for_period(
        self,
        period: ReportPeriod = "today",
        actions: tuple[str, ...] = ("backup", "restore"),
        db_type: DatabaseType | None = None,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        user: str | None = None,
        scope: DashboardConnectionScope | None = None,
    ) -> tuple[int, int]:
        """Count success/failed audit entries for the selected time period."""
        success = 0
        failed = 0
        user_key = user.strip().lower() if user else None

        for record in self._iter_audit_records():
            data = record.get("data", {})
            action = str(data.get("action", "")).lower()
            if action not in actions:
                continue

            if user_key:
                rec_user = str(data.get("user", "")).strip().lower()
                if rec_user != user_key:
                    continue

            if scope is not None and not audit_record_matches_scope(data, scope):
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
            except ValueError:
                continue

            if not self._timestamp_in_period(
                ts, period, date_from=date_from, date_to=date_to
            ):
                continue

            status = str(data.get("status", "")).lower()
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1

        return success, failed

    def count_audit_today(
        self,
        actions: tuple[str, ...] = ("backup", "restore"),
        db_type: DatabaseType | None = None,
        *,
        user: str | None = None,
        scope: DashboardConnectionScope | None = None,
    ) -> tuple[int, int]:
        """Count success/failed audit entries for today (local calendar day)."""
        return self.count_audit_for_period(
            "today", actions=actions, db_type=db_type, user=user, scope=scope
        )

    def get_dashboard_report(
        self,
        period: ReportPeriod = "today",
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
        registered_servers: int = 0,
        database_count: int = 0,
        connection_name: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """Build a dashboard-style metrics report for the selected time period."""
        range_kw = {"date_from": date_from, "date_to": date_to}
        success, failed = self.count_audit_for_period(period, db_type=db_type, **range_kw)
        backup_ok, backup_fail = self.count_audit_for_period(
            period, actions=("backup",), db_type=db_type, **range_kw
        )
        restore_ok, restore_fail = self.count_audit_for_period(
            period, actions=("restore",), db_type=db_type, **range_kw
        )
        running, pending = self.count_live_jobs()

        backups = self._list_backups_for_context(
            profile=profile,
            db_type=db_type,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
        )
        storage_used = self._storage_used_mb(
            profile=profile,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
            backups=backups,
        )

        return {
            "period": period,
            "period_label": self._period_label(period, date_from=date_from, date_to=date_to),
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "generated_at": datetime.now().astimezone().strftime("%A, %d %B %Y · %H:%M:%S"),
            "linux_servers": registered_servers,
            "databases": database_count,
            "active_jobs": running,
            "pending_jobs": pending,
            "success_operations": success,
            "failed_operations": failed,
            "backup_success": backup_ok,
            "backup_failed": backup_fail,
            "restore_success": restore_ok,
            "restore_failed": restore_fail,
            "total_backups": len(backups),
            "storage_used_mb": storage_used,
            "connection_name": connection_name or "Not connected",
        }

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

    @staticmethod
    def _security_severity(action: str, status: str) -> str:
        action_l = action.lower()
        status_l = status.lower()
        if status_l == "failed":
            if action_l == "connect":
                return "critical"
            if action_l in ("backup", "restore"):
                return "warning"
            return "critical"
        return "info"

    @staticmethod
    def _security_alert_title(action: str, status: str) -> str:
        action_l = action.lower()
        status_l = status.lower()
        titles = {
            ("connect", "failed"): "Connection attempt failed",
            ("connect", "success"): "User connected successfully",
            ("backup", "failed"): "Backup operation failed",
            ("backup", "success"): "Backup completed successfully",
            ("restore", "failed"): "Restore operation failed",
            ("restore", "success"): "Restore completed successfully",
        }
        return titles.get((action_l, status_l), f"{action_l.replace('_', ' ').title()} {status_l}")

    def get_security_dashboard_data(
        self,
        *,
        user: str | None = None,
        days: int = 7,
        scope: DashboardConnectionScope | None = None,
    ) -> dict[str, Any]:
        """Aggregate audit log into security dashboard metrics and charts."""
        if scope is None:
            return empty_security_dashboard(days=days)

        today = datetime.now().astimezone().date()
        user_key = user.strip().lower() if user else None
        day_keys = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        trend: dict = {d: {"critical": 0, "warning": 0, "info": 0} for d in day_keys}

        critical_today = warning_today = info_today = 0
        failed_logins_today = 0
        recent_candidates: list[dict[str, Any]] = []
        risk_counts: dict[str, int] = {
            "Failed DB Connections": 0,
            "Failed Backups": 0,
            "Failed Restores": 0,
        }

        for record in self._iter_audit_records():
            data = record.get("data", {})
            action = str(data.get("action", "")).lower()
            status = str(data.get("status", "")).lower()
            if not action:
                continue

            rec_user = str(data.get("user", "")).strip().lower()
            if user_key and rec_user != user_key:
                continue

            if not audit_record_matches_scope(data, scope):
                continue

            try:
                ts = datetime.fromisoformat(str(record.get("timestamp", "")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.astimezone()
            except ValueError:
                continue

            severity = self._security_severity(action, status)
            day = ts.date()
            if day in trend:
                trend[day][severity] += 1

            if day == today:
                if severity == "critical":
                    critical_today += 1
                elif severity == "warning":
                    warning_today += 1
                else:
                    info_today += 1

                if action == "connect" and status == "failed":
                    failed_logins_today += 1
                    risk_counts["Failed DB Connections"] += 1
                elif action == "backup" and status == "failed":
                    risk_counts["Failed Backups"] += 1
                elif action == "restore" and status == "failed":
                    risk_counts["Failed Restores"] += 1

            if status == "failed":
                resource = str(data.get("resource", "Unknown server"))
                details = data.get("details") or {}
                host = details.get("host", "")
                detail = f"Server: {resource}"
                if host:
                    detail = f"{detail} ({host})"
                if status == "failed" and details.get("error"):
                    detail = f"{detail} — {str(details['error'])[:80]}"
                recent_candidates.append({
                    "severity": severity,
                    "title": self._security_alert_title(action, status),
                    "detail": detail,
                    "time": ts.strftime("%I:%M %p").lstrip("0"),
                    "timestamp": ts,
                    "user": data.get("user", "system"),
                })

        recent_candidates.sort(key=lambda x: x["timestamp"], reverse=True)
        recent_alerts = [
            {k: v for k, v in item.items() if k != "timestamp"}
            for item in recent_candidates[:5]
        ]

        total_today = critical_today + warning_today + info_today
        overview = {
            "critical": critical_today,
            "warning": warning_today,
            "info": info_today,
            "total": total_today,
        }

        trend_series = [
            {
                "label": d.strftime("%d %b"),
                "critical": trend[d]["critical"],
                "warning": trend[d]["warning"],
                "info": trend[d]["info"],
            }
            for d in day_keys
        ]

        severity_map = {
            "Failed DB Connections": ("Critical", "critical"),
            "Failed Backups": ("High", "warning"),
            "Failed Restores": ("High", "warning"),
        }
        top_risks: list[dict[str, Any]] = []
        for name, count in risk_counts.items():
            if count <= 0:
                continue
            label, sev = severity_map.get(name, ("Medium", "warning"))
            top_risks.append({"risk": name, "severity": label, "severity_key": sev, "count": count})
        top_risks.sort(key=lambda r: r["count"], reverse=True)

        security_alerts = critical_today + warning_today
        if critical_today > 0:
            compliance = "Review Required"
        elif warning_today > 0:
            compliance = "Needs Attention"
        else:
            compliance = "Compliant"

        return {
            "posture": {
                "failed_logins": failed_logins_today,
                "security_alerts": security_alerts,
                "compliance": compliance,
            },
            "overview": overview,
            "trend": trend_series,
            "recent_alerts": recent_alerts,
            "top_risks": top_risks[:5],
        }

    def get_backup_analytics_data(
        self,
        *,
        user: str | None = None,
        days: int = 7,
        storage_mb: float = 0.0,
        scope: DashboardConnectionScope | None = None,
    ) -> dict[str, Any]:
        """Backup success/failure breakdown for dashboard charts."""
        if scope is None:
            return empty_backup_analytics(days=days)

        today = datetime.now().astimezone().date()
        user_key = user.strip().lower() if user else None
        day_keys = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        counts = {d: {"successful": 0, "failed": 0, "skipped": 0} for d in day_keys}
        successful = failed = skipped = 0

        for record in self._iter_audit_records():
            data = record.get("data", {})
            action = str(data.get("action", "")).lower()
            if action != "backup":
                continue
            rec_user = str(data.get("user", "")).strip().lower()
            if user_key and rec_user != user_key:
                continue
            if not audit_record_matches_scope(data, scope):
                continue
            try:
                ts = datetime.fromisoformat(str(record.get("timestamp", "")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.astimezone()
            except ValueError:
                continue
            day = ts.date()
            if day not in counts:
                continue
            status = str(data.get("status", "")).lower()
            if status == "success":
                counts[day]["successful"] += 1
                successful += 1
            elif status == "failed":
                counts[day]["failed"] += 1
                failed += 1
            elif status in ("skipped", "cancelled"):
                counts[day]["skipped"] += 1
                skipped += 1

        total = successful + failed + skipped
        success_rate = round(successful / total * 100, 1) if total else 0.0
        trend = [
            {
                "label": d.strftime("%d %b"),
                "successful": counts[d]["successful"],
                "failed": counts[d]["failed"],
                "skipped": counts[d]["skipped"],
            }
            for d in day_keys
        ]
        return {
            "successful": successful,
            "failed": failed,
            "skipped": skipped,
            "total": total,
            "success_rate": success_rate,
            "storage_mb": storage_mb,
            "storage_gb": round(storage_mb / 1024, 1),
            "trend": trend,
        }

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
