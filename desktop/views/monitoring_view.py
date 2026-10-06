"""Audit Log view — application activity trail (not live server monitoring)."""

from __future__ import annotations

import customtkinter as ctk

from backend.services.monitoring_service import MonitoringService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class MonitoringView(BaseView):
    """Audit trail from logs/audit.log.

    Kept as MonitoringView for import compatibility; the UI is Audit Log only.
    Health checks and backup reports are not shown here (Monitoring was removed
    from navigation).
    """

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        auto_action: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = MonitoringService()
        self._auto_action = auto_action
        self._build_ui()
        self.after(80, self.refresh)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(16, 0), sticky="ew")
        from desktop.ops_widgets import page_header

        page_header(
            header,
            self.theme,
            "Audit Log",
            subtitle=(
                "Application activity trail from this workstation "
                "(connect, backup, restore, and other recorded actions)."
            ),
        )

        self.results = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=self.theme.c("surface"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_primary"),
            corner_radius=self.theme.radius("sm"),
        )
        self.results.grid(row=1, column=0, padx=24, pady=(0, 16), sticky="nsew")

    def refresh(self) -> None:
        """Reload audit lines from disk."""
        try:
            logs = self.service.get_audit_logs(200)
        except Exception as exc:
            self.results.delete("1.0", "end")
            self.results.insert(
                "end",
                f"Could not read the audit log.\n\nReason: {exc}\n\n"
                "Suggested action: confirm logs/audit.log is readable, then Refresh.",
            )
            self.log(f"Audit log read failed: {exc}", "ERROR")
            return

        self.results.delete("1.0", "end")
        if not logs:
            self.results.insert(
                "end",
                "No audit events yet.\n\n"
                "Events appear here after you sign in, connect, run backups, "
                "restores, or other recorded operations.",
            )
            return

        for line in logs:
            self.results.insert("end", f"{line}\n")
        self.log(f"Loaded {len(logs)} audit log entr{'y' if len(logs) == 1 else 'ies'}")
