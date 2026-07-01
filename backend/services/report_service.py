"""Enterprise report data aggregation (model layer)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService, ReportPeriod


@dataclass
class ReportFilters:
    period: ReportPeriod = "today"
    date_from: date | None = None
    date_to: date | None = None
    server: str = "All"
    database: str = "All"
    operation_type: str = "All"
    status: str = "All"


class ReportService:
    """Builds rich enterprise dashboard reports from monitoring data."""

    OPERATIONS = ("backup", "restore", "migration", "patch")

    def __init__(self) -> None:
        self.monitoring = MonitoringService()

    def build_report(
        self,
        filters: ReportFilters,
        *,
        profile: str = "default",
        db_type: DatabaseType | None = None,
        list_remote: bool = False,
        remote_dir: str | None = None,
        local_dir: str | None = None,
        registered_servers: int = 0,
        database_count: int = 0,
        connection_name: str | None = None,
        server_names: list[str] | None = None,
        database_names: list[str] | None = None,
    ) -> dict[str, Any]:
        period = filters.period
        range_kw = {"date_from": filters.date_from, "date_to": filters.date_to}
        base = self.monitoring.get_dashboard_report(
            period,
            profile=profile,
            db_type=db_type,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
            registered_servers=registered_servers,
            database_count=database_count,
            connection_name=connection_name,
            **range_kw,
        )

        backups = self.monitoring._list_backups_for_context(
            profile=profile,
            db_type=db_type,
            list_remote=list_remote,
            remote_dir=remote_dir,
            local_dir=local_dir,
        )
        if period == "custom" and filters.date_from and filters.date_to:
            backups = self._filter_backups_by_range(backups, filters.date_from, filters.date_to)
            base["total_backups"] = len(backups)
            total_bytes = sum(int(b.get("size_bytes") or 0) for b in backups)
            base["storage_used_mb"] = round(total_bytes / (1024 * 1024), 2)

        prev_success, prev_failed = self._previous_period_totals(filters, db_type)
        activity = self._filtered_activity(filters, limit=25)
        ops = self._operations_overview(filters, db_type)
        connected = connection_name is not None
        health = self._health_summary(
            connected=connected,
            registered_servers=registered_servers,
        )
        analytics = self._backup_analytics(backups)

        success = base["success_operations"]
        failed = base["failed_operations"]
        success_delta = self._delta_text(success, prev_success, "operations")
        failed_delta = self._delta_text(failed, prev_failed, "failures", invert=True)
        return {
            **base,
            "connection_status": "Connected" if connected else "Disconnected",
            "connection_badge_color": "success" if connected else "muted",
            "last_generated": base["generated_at"],
            "filters": {
                "period": period,
                "date_from": filters.date_from.isoformat() if filters.date_from else None,
                "date_to": filters.date_to.isoformat() if filters.date_to else None,
                "server": filters.server,
                "database": filters.database,
                "operation_type": filters.operation_type,
                "status": filters.status,
            },
            "filter_options": {
                "servers": ["All"] + (server_names or []),
                "databases": ["All"] + (database_names or []),
                "operations": ["All", "Backup", "Restore", "Migration", "Patch"],
                "statuses": ["All", "Success", "Failed"],
                "periods": [
                    ("today", "Today"),
                    ("monthly", "Monthly"),
                    ("yearly", "Yearly"),
                    ("custom", "Custom"),
                ],
            },
            "kpi": {
                "success_operations": success,
                "failed_operations": failed,
                "total_backups": base["total_backups"],
                "storage_used_mb": base["storage_used_mb"],
                "success_comparison": success_delta,
                "failed_comparison": failed_delta,
                "backups_comparison": f"{base['total_backups']} files indexed",
                "storage_comparison": f"{analytics['avg_size_mb']:.2f} MB avg size",
            },
            "summary": {
                "registered_servers": registered_servers,
                "connected_databases": database_count,
                "active_jobs": base["active_jobs"],
                "pending_jobs": base["pending_jobs"],
                "failed_jobs": failed,
            },
            "operations_overview": ops,
            "backup_analytics": analytics,
            "health": health,
            "recent_activity": activity,
        }

    def _filter_backups_by_range(
        self, backups: list[dict], date_from: date, date_to: date
    ) -> list[dict]:
        filtered: list[dict] = []
        for backup in backups:
            ts = self._parse_backup_ts(backup.get("created_at"))
            if ts and date_from <= ts.date() <= date_to:
                filtered.append(backup)
        return filtered

    def _parse_backup_ts(self, raw) -> datetime | None:
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(str(raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone()
        except ValueError:
            return None

    def _previous_period_totals(
        self, filters: ReportFilters, db_type: DatabaseType | None
    ) -> tuple[int, int]:
        period = filters.period
        if period == "custom" and filters.date_from and filters.date_to:
            span = (filters.date_to - filters.date_from).days + 1
            prev_end = filters.date_from - timedelta(days=1)
            prev_start = prev_end - timedelta(days=span - 1)
            return self._count_audit_in_range(prev_start, prev_end, db_type)
        if period == "today":
            return self._count_audit_day_offset(1, db_type)
        if period == "monthly":
            return self._count_audit_month_offset(1, db_type)
        return self._count_audit_year_offset(1, db_type)

    def _count_audit_in_range(
        self, date_from: date, date_to: date, db_type: DatabaseType | None
    ) -> tuple[int, int]:
        success = failed = 0
        for record in self.monitoring._iter_audit_records():
            if not self._record_matches_db(record, db_type):
                continue
            ts = self._parse_ts(record.get("timestamp", ""))
            if not ts:
                continue
            day = ts.date()
            if day < date_from or day > date_to:
                continue
            status = str(record.get("data", {}).get("status", "")).lower()
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1
        return success, failed

    def _count_audit_day_offset(self, days_ago: int, db_type: DatabaseType | None) -> tuple[int, int]:
        target = datetime.now().astimezone().date() - timedelta(days=days_ago)
        return self._count_audit_on_date(target, db_type)

    def _count_audit_month_offset(self, months_ago: int, db_type: DatabaseType | None) -> tuple[int, int]:
        now = datetime.now().astimezone()
        month = now.month - months_ago
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        success = failed = 0
        for record in self.monitoring._iter_audit_records():
            if not self._record_matches_db(record, db_type):
                continue
            ts = self._parse_ts(record.get("timestamp", ""))
            if not ts or ts.year != year or ts.month != month:
                continue
            status = str(record.get("data", {}).get("status", "")).lower()
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1
        return success, failed

    def _count_audit_year_offset(self, years_ago: int, db_type: DatabaseType | None) -> tuple[int, int]:
        target_year = datetime.now().astimezone().year - years_ago
        success = failed = 0
        for record in self.monitoring._iter_audit_records():
            if not self._record_matches_db(record, db_type):
                continue
            ts = self._parse_ts(record.get("timestamp", ""))
            if not ts or ts.year != target_year:
                continue
            status = str(record.get("data", {}).get("status", "")).lower()
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1
        return success, failed

    def _count_audit_on_date(self, day, db_type: DatabaseType | None) -> tuple[int, int]:
        success = failed = 0
        for record in self.monitoring._iter_audit_records():
            if not self._record_matches_db(record, db_type):
                continue
            ts = self._parse_ts(record.get("timestamp", ""))
            if not ts or ts.date() != day:
                continue
            status = str(record.get("data", {}).get("status", "")).lower()
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1
        return success, failed

    def _record_matches_db(self, record: dict, db_type: DatabaseType | None) -> bool:
        if not db_type:
            return True
        details = record.get("data", {}).get("details") or {}
        rec_db = details.get("db_type")
        return not rec_db or rec_db == db_type.value

    def _parse_ts(self, ts_raw: str) -> datetime | None:
        try:
            ts = datetime.fromisoformat(str(ts_raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone()
        except ValueError:
            return None

    def _delta_text(self, current: int, previous: int, label: str, *, invert: bool = False) -> str:
        diff = current - previous
        if diff > 0:
            arrow = "↑"
            tone = "more" if not invert else "more"
        elif diff < 0:
            arrow = "↓"
            tone = "fewer"
        else:
            return f"→ No change vs prior period"
        return f"{arrow} {abs(diff)} {tone} {label} vs prior period"

    def _operations_overview(
        self, filters: ReportFilters, db_type: DatabaseType | None
    ) -> list[dict]:
        rows = []
        range_kw = {"date_from": filters.date_from, "date_to": filters.date_to}
        for op in self.OPERATIONS:
            ok, fail = self.monitoring.count_audit_for_period(
                filters.period, actions=(op,), db_type=db_type, **range_kw
            )
            total = ok + fail
            rate = round((ok / total) * 100, 1) if total else 0.0
            rows.append({
                "type": op.capitalize(),
                "success": ok,
                "failed": fail,
                "success_rate": rate,
            })
        return rows

    def _backup_analytics(self, backups: list[dict]) -> dict[str, Any]:
        if not backups:
            return {
                "total": 0,
                "latest": "—",
                "avg_size_mb": 0.0,
                "total_storage_mb": 0.0,
                "largest_mb": 0.0,
                "largest_name": "—",
            }

        sizes = [int(b.get("size_bytes") or 0) for b in backups]
        largest_idx = max(range(len(sizes)), key=lambda i: sizes[i])
        latest_ts = None
        latest_label = "—"
        for b in backups:
            raw = b.get("created_at")
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(str(raw))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
                    latest_label = ts.astimezone().strftime("%d %b %Y %H:%M")
            except ValueError:
                continue

        total_bytes = sum(sizes)
        avg_bytes = total_bytes / len(sizes) if sizes else 0
        return {
            "total": len(backups),
            "latest": latest_label,
            "avg_size_mb": round(avg_bytes / (1024 * 1024), 2),
            "total_storage_mb": round(total_bytes / (1024 * 1024), 2),
            "largest_mb": round(sizes[largest_idx] / (1024 * 1024), 2),
            "largest_name": str(backups[largest_idx].get("name", "—"))[:40],
        }

    def _health_summary(self, *, connected: bool, registered_servers: int) -> dict[str, int]:
        """Fast health summary without live database ping (keeps UI responsive)."""
        healthy = warning = critical = 0
        try:
            for result in self.monitoring.health_check(check_databases=False):
                status = str(result.status).lower()
                if status in ("healthy", "ok"):
                    healthy += 1
                elif status in ("degraded", "warning"):
                    warning += 1
                else:
                    critical += 1
            if connected:
                healthy += 1
            elif registered_servers > 0:
                warning += 1
            else:
                warning += 1
        except Exception:
            warning = 1
        return {"healthy": healthy, "warning": warning, "critical": critical}

    def _filtered_activity(self, filters: ReportFilters, limit: int = 25) -> list[dict]:
        rows: list[dict] = []
        for record in reversed(self.monitoring._iter_audit_records()):
            data = record.get("data", {})
            action = str(data.get("action", "event")).lower()
            status = str(data.get("status", "info")).lower()
            details = data.get("details") or {}

            if filters.operation_type != "All" and action != filters.operation_type.lower():
                continue
            if filters.status != "All" and status != filters.status.lower():
                continue

            server = str(data.get("resource") or details.get("server") or "default")[:24]
            if filters.server != "All" and filters.server.lower() not in server.lower():
                continue

            database = str(details.get("database") or details.get("db_type") or "—")[:20]
            if filters.database != "All" and filters.database.lower() not in database.lower():
                continue

            ts = self._parse_ts(record.get("timestamp", ""))
            if ts and not self.monitoring._timestamp_in_period(
                ts,
                filters.period,
                date_from=filters.date_from,
                date_to=filters.date_to,
            ):
                continue

            rows.append({
                "time": ts.strftime("%d %b %Y %H:%M") if ts else "—",
                "operation": action.capitalize(),
                "server": server,
                "database": database,
                "status": status.capitalize(),
                "duration": str(details.get("duration_ms", "—")),
            })
            if len(rows) >= limit:
                break
        return rows

    def raw_report_text(self, report: dict[str, Any]) -> str:
        lines = [
            "=" * 60,
            "  DATABASE AUTOMATION PRO — RAW REPORT",
            f"  {report.get('period_label', '')}",
            "=" * 60,
            "",
            f"Generated:          {report.get('generated_at', '')}",
            f"Connection:         {report.get('connection_name', '')}",
            f"Connection Status:  {report.get('connection_status', '')}",
            "",
            "KPI SUMMARY",
            "-" * 60,
            f"  Successful Operations    {report['kpi']['success_operations']:>8}",
            f"  Failed Operations        {report['kpi']['failed_operations']:>8}",
            f"  Total Backups            {report['kpi']['total_backups']:>8}",
            f"  Storage Used (MB)        {report['kpi']['storage_used_mb']:>8.2f}",
            "",
            "REPORT SUMMARY",
            "-" * 60,
        ]
        for key, val in report["summary"].items():
            lines.append(f"  {key.replace('_', ' ').title():<28} {val:>8}")
        lines.extend(["", "OPERATIONS OVERVIEW", "-" * 60])
        for op in report["operations_overview"]:
            lines.append(
                f"  {op['type']:<12} success={op['success']} failed={op['failed']} rate={op['success_rate']}%"
            )
        lines.extend(["", "BACKUP ANALYTICS", "-" * 60])
        for key, val in report["backup_analytics"].items():
            lines.append(f"  {key.replace('_', ' ').title():<28} {val}")
        lines.extend(["", "DATABASE HEALTH", "-" * 60])
        h = report["health"]
        lines.append(f"  Healthy: {h['healthy']}   Warning: {h['warning']}   Critical: {h['critical']}")
        lines.extend(["", "RECENT ACTIVITY", "-" * 60])
        for row in report["recent_activity"]:
            lines.append(
                f"  {row['time']}  {row['operation']:<10} {row['server']:<16} "
                f"{row['database']:<12} {row['status']:<8} {row['duration']}"
            )
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
