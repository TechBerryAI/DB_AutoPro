"""Monitoring and health check view."""

from __future__ import annotations

import customtkinter as ctk

from backend.services.monitoring_service import MonitoringService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class MonitoringView(BaseView):
    """Health checks, audit logs, and backup reports."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = MonitoringService()
        self._build_ui()

    def _build_ui(self) -> None:
        row = 0
        context = self.create_connection_context()
        if context:
            context.grid(row=row, column=0, padx=20, pady=(12, 8), sticky="ew")
            row += 1

        title = self.create_section_title("Monitoring")
        title.grid(row=row, column=0, padx=20, pady=20, sticky="w")
        row += 1

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1

        ctk.CTkButton(
            btn_frame,
            text="Health Check",
            command=self._health_check,
            width=130,
            height=36,
            corner_radius=8,
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            btn_frame,
            text="Backup Report",
            command=self._backup_report,
            width=130,
            height=36,
            corner_radius=8,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            btn_frame,
            text="Audit Logs",
            command=self._audit_logs,
            width=130,
            height=36,
            corner_radius=8,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
        ).pack(side="left", padx=5)

        self.results = ctk.CTkTextbox(
            self,
            height=400,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=self.theme.c("bg_card"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_secondary"),
        )
        self.results.grid(row=row, column=0, padx=20, pady=10, sticky="nsew")
        self.grid_rowconfigure(row, weight=1)

    def _health_check(self) -> None:
        results = self.service.health_check()
        self.results.delete("1.0", "end")
        for r in results:
            self.results.insert("end", f"[{r.status.upper()}] {r.service}\n")
            if r.latency_ms:
                self.results.insert("end", f"  Latency: {r.latency_ms}ms\n")
        self.log("Health check completed")

    def _backup_report(self) -> None:
        report = self.service.get_backup_report()
        self.results.delete("1.0", "end")
        self.results.insert("end", f"Total backups: {report['total']}\n")
        self.results.insert("end", f"Retention: {report['retention_days']} days\n\n")
        for b in report.get("backups", []):
            self.results.insert("end", f"  {b['name']} - {b['size_bytes']} bytes\n")

    def _audit_logs(self) -> None:
        logs = self.service.get_audit_logs(50)
        self.results.delete("1.0", "end")
        for line in logs:
            self.results.insert("end", f"{line}\n")
