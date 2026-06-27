"""Enterprise home dashboard with greeting header and key metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.core.config import get_settings
from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService
from desktop.connection_store import load_saved_servers
from desktop.db_catalog import fetch_server_databases
from desktop.employee_store import EmployeeSession
from desktop.theme import ThemeManager
from desktop.ui_components import home_greeting_card, key_metric_tile
from desktop.views.base_view import BaseView


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _role_label(role) -> str:
    labels = {"admin": "Administrator", "operator": "Operator", "viewer": "Viewer"}
    return labels.get(role.value, role.value.title())


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name[:2] if name else "U").upper()


def _datetime_text() -> str:
    return datetime.now().strftime("%A, %d %B %Y - %H:%M:%S")


class DashboardView(BaseView):
    """Home dashboard shown after employee login."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        navigate_callback: Callable[[str], None] | None = None,
        employee: EmployeeSession | None = None,
        **kwargs,
    ) -> None:
        initial_refresh = kwargs.pop("initial_refresh", True)
        super().__init__(master, theme, log_callback, **kwargs)
        self.monitoring = MonitoringService()
        self.navigate_callback = navigate_callback
        self.employee = employee
        self._metric_values: dict[str, ctk.CTkLabel] = {}
        self._metric_badges: dict[str, ctk.CTkLabel] = {}
        self._datetime_label: ctk.CTkLabel | None = None
        self._build_ui()
        if initial_refresh:
            self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=28, pady=(20, 16))
        scroll.grid_columnconfigure(0, weight=1)

        name = self.employee.full_name if self.employee else "User"
        role = _role_label(self.employee.role) if self.employee else "Operator"
        settings = get_settings()

        _, self._datetime_label = home_greeting_card(
            scroll,
            self.theme,
            greeting=f"{_greeting()}, {name}",
            role=role,
            initials=_initials(name),
            datetime_text=_datetime_text(),
            environment=settings.environment,
            on_refresh=self.refresh,
        )

        ctk.CTkLabel(
            scroll,
            text="Key Metrics",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 14))

        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 16))
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="metric")

        tiles = [
            (
                "linux_servers", "⬡", "#2563eb", "#eff6ff",
                "Linux Servers", "Registered", "#eff6ff", "#2563eb",
                "Registered SSH targets", "SSH",
            ),
            (
                "databases", "◎", "#7c3aed", "#f5f3ff",
                "Databases", "Connected", "#f5f3ff", "#7c3aed",
                "PostgreSQL, MySQL, Oracle, SQL Server", "Multi-DB",
            ),
            (
                "active_jobs", "▣", "#2563eb", "#eff6ff",
                "Active Jobs", "Live", "#eff6ff", "#2563eb",
                "Currently running", "Live",
            ),
            (
                "success_today", "✓", "#059669", "#ecfdf5",
                "Success Today", "Success", "#ecfdf5", "#059669",
                "Completed successfully", "Today",
            ),
            (
                "failed_today", "!", "#dc2626", "#fef2f2",
                "Failed Today", "OK", "#ecfdf5", "#059669",
                "Requires review", "Clear",
            ),
            (
                "pending_jobs", "◷", "#d97706", "#fffbeb",
                "Pending Jobs", "Queue", "#fffbeb", "#d97706",
                "Queued or in progress", "Queue",
            ),
        ]

        for i, spec in enumerate(tiles):
            key, icon, color, icon_bg, title, badge, badge_fg, badge_text, subtitle, link = spec
            row, col = divmod(i, 3)
            card, val_lbl, badge_lbl = key_metric_tile(
                grid,
                self.theme,
                icon=icon,
                icon_color=color,
                icon_bg=icon_bg,
                title=title,
                badge=badge,
                badge_fg=badge_fg,
                badge_text=badge_text,
                subtitle=subtitle,
                footer_link=link,
            )
            card.grid(
                row=row,
                column=col,
                padx=(0 if col == 0 else 10, 0),
                pady=(0 if row == 0 else 10, 0),
                sticky="nsew",
            )
            self._metric_values[key] = val_lbl
            self._metric_badges[key] = badge_lbl

    def _backup_stats_context(self) -> dict:
        profile = self.get_active_profile()
        local_dir: str | None = None
        remote_dir = "/mnt/backup"
        list_remote = False
        db_type: DatabaseType | None = None

        if self.connection:
            db_type = self.connection.db_type
            lookup = self.connection.saved_name or self.connection.name
            for server in load_saved_servers():
                if server.name == lookup and server.db_type == db_type.value:
                    if server.local_output_dir:
                        local_dir = server.local_output_dir
                    if server.remote_backup_dir:
                        remote_dir = server.remote_backup_dir
                    break
            if self.connection.remote_backup_dir:
                remote_dir = self.connection.remote_backup_dir
            if db_type == DatabaseType.MONGODB and self.connection.ssh_enabled:
                list_remote = True

        return {
            "profile": profile,
            "db_type": db_type,
            "list_remote": list_remote,
            "remote_dir": remote_dir,
            "local_dir": local_dir,
        }

    def _update_badge(self, key: str, text: str, fg: str, text_color: str) -> None:
        badge = self._metric_badges.get(key)
        if badge:
            badge.configure(text=text, fg_color=fg, text_color=text_color)

    def refresh(self) -> None:
        try:
            if self._datetime_label:
                self._datetime_label.configure(text=_datetime_text())

            ctx = self._backup_stats_context()
            db_type = ctx.get("db_type")

            linux_servers = len(load_saved_servers())

            db_count = 0
            if self.connection:
                try:
                    db_count = len(fetch_server_databases(self.connection))
                except Exception:
                    db_count = 0

            active_jobs, pending_jobs = self.monitoring.count_live_jobs()
            success_today, failed_today = self.monitoring.count_audit_today(
                actions=("backup", "restore"),
                db_type=db_type,
            )

            mapping = {
                "linux_servers": str(linux_servers),
                "databases": str(db_count),
                "active_jobs": str(active_jobs),
                "success_today": str(success_today),
                "failed_today": str(failed_today),
                "pending_jobs": str(pending_jobs),
            }
            for key, value in mapping.items():
                if key in self._metric_values:
                    self._metric_values[key].configure(text=value)

            if self.connection:
                self._update_badge("databases", "Connected", "#f5f3ff", "#7c3aed")
            else:
                self._update_badge("databases", "Offline", "#f1f5f9", "#64748b")

            if failed_today > 0:
                self._update_badge("failed_today", "Alert", "#fef2f2", "#dc2626")
            else:
                self._update_badge("failed_today", "OK", "#ecfdf5", "#059669")

            self.log("Dashboard refreshed", "SUCCESS")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
