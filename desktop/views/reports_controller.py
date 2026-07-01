"""Reports view controller (MVC)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from backend.services.report_service import ReportFilters, ReportService
from desktop.report_export import default_export_stem, export_dashboard_csv, export_dashboard_pdf


class ReportsController:
    """Coordinates report generation, filtering, and export."""

    def __init__(self, log_callback: Callable[[str, str], None] | None = None) -> None:
        self.service = ReportService()
        self.filters = ReportFilters()
        self.last_report: dict | None = None
        self.log_callback = log_callback

    def log(self, message: str, level: str = "INFO") -> None:
        if self.log_callback:
            self.log_callback(message, level)

    def generate(self, context: dict) -> dict:
        self.last_report = self.service.build_report(self.filters, **context)
        return self.last_report

    def raw_text(self) -> str:
        if not self.last_report:
            return "No report generated yet."
        return self.service.raw_report_text(self.last_report)

    def export_csv(self, path: Path) -> Path:
        if not self.last_report:
            raise ValueError("Generate a report first")
        saved = export_dashboard_csv(self._export_payload(), path)
        self.log(f"CSV exported to {saved}", "SUCCESS")
        return saved

    def export_pdf(self, path: Path) -> Path:
        if not self.last_report:
            raise ValueError("Generate a report first")
        saved = export_dashboard_pdf(self._export_payload(), path)
        self.log(f"PDF exported to {saved}", "SUCCESS")
        return saved

    def _export_payload(self) -> dict:
        assert self.last_report is not None
        r = self.last_report
        return {
            "period": r.get("period", "today"),
            "period_label": r.get("period_label", ""),
            "generated_at": r.get("generated_at", ""),
            "connection_name": r.get("connection_name", ""),
            "linux_servers": r["summary"]["registered_servers"],
            "databases": r["summary"]["connected_databases"],
            "active_jobs": r["summary"]["active_jobs"],
            "pending_jobs": r["summary"]["pending_jobs"],
            "success_operations": r["kpi"]["success_operations"],
            "failed_operations": r["kpi"]["failed_operations"],
            "backup_success": sum(o["success"] for o in r["operations_overview"] if o["type"] == "Backup"),
            "backup_failed": sum(o["failed"] for o in r["operations_overview"] if o["type"] == "Backup"),
            "restore_success": sum(o["success"] for o in r["operations_overview"] if o["type"] == "Restore"),
            "restore_failed": sum(o["failed"] for o in r["operations_overview"] if o["type"] == "Restore"),
            "total_backups": r["kpi"]["total_backups"],
            "storage_used_mb": r["kpi"]["storage_used_mb"],
        }
