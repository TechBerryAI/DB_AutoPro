"""Dashboard view with statistics and system metrics."""

from __future__ import annotations

import customtkinter as ctk

from backend.services.monitoring_service import MonitoringService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class DashboardView(BaseView):
    """Dashboard with key statistics and health overview."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.monitoring = MonitoringService()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure((0, 1, 2), weight=1)

        title = self.create_section_title("Dashboard")
        title.grid(row=0, column=0, columnspan=3, padx=20, pady=(20, 10), sticky="w")

        refresh_btn = ctk.CTkButton(self, text="Refresh", command=self.refresh, width=100)
        refresh_btn.grid(row=0, column=2, padx=20, pady=(20, 10), sticky="e")

        self.stat_cards: dict[str, ctk.CTkLabel] = {}
        stats = [
            ("total_backups", "Total Backups"),
            ("successful_backups", "Successful"),
            ("patch_count", "Patches Applied"),
            ("storage_used_mb", "Storage (MB)"),
            ("database_connections", "Connections"),
            ("pending_operations", "Pending Ops"),
        ]

        for i, (key, label) in enumerate(stats):
            row, col = divmod(i, 3)
            card = ctk.CTkFrame(self, corner_radius=10)
            card.grid(row=row + 1, column=col, padx=10, pady=10, sticky="nsew")

            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=13)).pack(pady=(15, 5))
            value_label = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=28, weight="bold"))
            value_label.pack(pady=(0, 15))
            self.stat_cards[key] = value_label

        metrics_frame = ctk.CTkFrame(self, corner_radius=10)
        metrics_frame.grid(row=3, column=0, columnspan=3, padx=20, pady=10, sticky="nsew")
        metrics_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            metrics_frame, text="System Metrics", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, padx=15, pady=10, sticky="w")

        self.metrics_text = ctk.CTkTextbox(metrics_frame, height=150, font=ctk.CTkFont(size=12))
        self.metrics_text.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

    def refresh(self) -> None:
        try:
            stats = self.monitoring.get_dashboard_stats()
            mapping = {
                "total_backups": str(stats.total_backups),
                "successful_backups": str(stats.successful_backups),
                "patch_count": str(stats.patch_count),
                "storage_used_mb": f"{stats.storage_used_mb:.1f}",
                "database_connections": str(stats.database_connections),
                "pending_operations": str(stats.pending_operations),
            }
            for key, value in mapping.items():
                if key in self.stat_cards:
                    self.stat_cards[key].configure(text=value)

            metrics = self.monitoring.get_system_metrics()
            self.metrics_text.delete("1.0", "end")
            self.metrics_text.insert("end", f"CPU: {metrics['cpu_percent']}%\n")
            self.metrics_text.insert("end", f"Memory: {metrics['memory']['used_mb']}/{metrics['memory']['total_mb']} MB\n")
            self.metrics_text.insert("end", f"Disk: {metrics['disk']['used_gb']}/{metrics['disk']['total_gb']} GB\n")
            self.metrics_text.insert("end", f"Platform: {metrics['platform']}\n")
            self.log("Dashboard refreshed")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
