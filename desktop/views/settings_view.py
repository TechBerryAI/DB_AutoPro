"""Settings view for environment profile display."""

from __future__ import annotations

import customtkinter as ctk

from backend.core.config import get_settings
from desktop.constants import APP_NAME
from desktop.theme import ThemeManager
from desktop.ops_widgets import page_header
from desktop.views.base_view import BaseView


class SettingsView(BaseView):
    """Application settings and environment profile display."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        theme_toggle_callback=None,
        **kwargs,
    ) -> None:
        # theme_toggle_callback kept for older callers; theme switch lives in the shell header.
        del theme_toggle_callback
        super().__init__(master, theme, log_callback, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(16, 8), sticky="ew")
        page_header(
            header,
            self.theme,
            "Settings",
            subtitle="Application configuration and environment profile",
        )

        settings = get_settings()
        info = self.card_frame(self)
        info.grid(row=1, column=0, padx=24, pady=8, sticky="nsew")

        labels = [
            ("Application", APP_NAME),
            ("Environment", settings.environment),
            ("API Port", str(settings.api_port)),
            ("Database Type", settings.database_type),
            ("Backup Retention", f"{settings.backup.retention_days} days"),
            ("Log Level", settings.logging.level),
        ]

        if self.connection:
            labels.extend(
                [
                    ("Connection Name", self.connection.name),
                    ("Connected Host", f"{self.connection.host}:{self.connection.port}"),
                    ("Connected Database", self.connection.database),
                    ("Connected User", self.connection.username),
                ]
            )

        for i, (label, value) in enumerate(labels):
            ctk.CTkLabel(
                info,
                text=label.upper(),
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=self.theme.c("text_muted"),
            ).grid(row=i, column=0, padx=20, pady=10, sticky="w")
            ctk.CTkLabel(
                info,
                text=value,
                font=ctk.CTkFont(size=13),
                text_color=self.theme.c("text_primary"),
            ).grid(row=i, column=1, padx=20, pady=10, sticky="w")
