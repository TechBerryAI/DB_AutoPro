"""Main desktop application with sidebar navigation."""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

from backend.core.logging import setup_logging
from desktop.theme import ThemeManager
from desktop.views.backup_view import BackupView
from desktop.views.dashboard import DashboardView
from desktop.views.fdw_view import FDWView
from desktop.views.migration_view import MigrationView
from desktop.views.monitoring_view import MonitoringView
from desktop.views.patch_view import PatchView
from desktop.views.restore_view import RestoreView
from desktop.views.settings_view import SettingsView

NAV_ITEMS = [
    ("dashboard", "Dashboard", DashboardView),
    ("backup", "Backup", BackupView),
    ("restore", "Restore", RestoreView),
    ("patch", "Patches", PatchView),
    ("fdw", "FDW", FDWView),
    ("migration", "Migration", MigrationView),
    ("monitoring", "Monitoring", MonitoringView),
    ("settings", "Settings", SettingsView),
]


class DatabaseAutomationApp(ctk.CTk):
    """Database Automation Pro desktop application."""

    def __init__(self) -> None:
        super().__init__()
        setup_logging()
        self.theme_manager = ThemeManager("dark")
        self.views: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}

        self.title("Database Automation Pro")
        self.geometry("1200x750")
        self.minsize(900, 600)

        self._build_layout()
        self.navigate("dashboard")

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(len(NAV_ITEMS) + 2, weight=1)

        logo = ctk.CTkLabel(
            self.sidebar,
            text="DB Auto Pro",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        logo.grid(row=0, column=0, padx=20, pady=(25, 5), sticky="w")

        subtitle = ctk.CTkLabel(
            self.sidebar,
            text="Database Administration",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        subtitle.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        for i, (key, label, _) in enumerate(NAV_ITEMS):
            btn = ctk.CTkButton(
                self.sidebar,
                text=f"  {label}",
                anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray70", "gray30"),
                command=lambda k=key: self.navigate(k),
            )
            btn.grid(row=i + 2, column=0, padx=10, pady=2, sticky="ew")
            self.nav_buttons[key] = btn

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        log_frame = ctk.CTkFrame(self.content, height=120, corner_radius=0)
        log_frame.grid(row=1, column=0, sticky="ew")
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_frame, text="Execution Log", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=0, padx=10, pady=(5, 0), sticky="w"
        )

        self.log_text = ctk.CTkTextbox(log_frame, height=90, font=ctk.CTkFont(family="Consolas", size=11))
        self.log_text.grid(row=1, column=0, padx=10, pady=5, sticky="ew")

        self.view_container = ctk.CTkFrame(self.content, fg_color="transparent")
        self.view_container.grid(row=0, column=0, sticky="nsew")
        self.view_container.grid_columnconfigure(0, weight=1)
        self.view_container.grid_rowconfigure(0, weight=1)

    def append_log(self, message: str, level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] [{level}] {message}\n")
        self.log_text.see("end")

    def navigate(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(fg_color=("gray75", "gray25"))
            else:
                btn.configure(fg_color="transparent")

        for view in self.views.values():
            view.grid_forget()

        if key not in self.views:
            for nav_key, _, view_cls in NAV_ITEMS:
                if nav_key == key:
                    kwargs = {
                        "log_callback": self.append_log,
                    }
                    if view_cls is SettingsView:
                        kwargs["theme_toggle_callback"] = self._on_theme_change
                    self.views[key] = view_cls(
                        self.view_container,
                        self.theme_manager,
                        **kwargs,
                    )
                    self.views[key].grid(row=0, column=0, sticky="nsew")
                    break
        else:
            self.views[key].grid(row=0, column=0, sticky="nsew")

        self.append_log(f"Navigated to {key}")

    def _on_theme_change(self, theme_name: str) -> None:
        self.append_log(f"Theme changed to {theme_name}")
