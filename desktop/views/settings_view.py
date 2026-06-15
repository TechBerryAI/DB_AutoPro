"""Settings view for environment and theme configuration."""

from __future__ import annotations

import customtkinter as ctk

from backend.core.config import get_settings
from desktop.constants import APP_NAME
from desktop.theme import ThemeManager
from desktop.ui_components import section_header
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
        super().__init__(master, theme, log_callback, **kwargs)
        self.theme_toggle_callback = theme_toggle_callback
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(20, 16), sticky="ew")
        section_header(header, self.theme, "Settings", "Application configuration and environment profile")

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

        theme_frame = ctk.CTkFrame(self, fg_color="transparent")
        theme_frame.grid(row=2, column=0, padx=24, pady=20, sticky="w")

        ctk.CTkLabel(
            theme_frame,
            text=f"Current theme: {self.theme.current}",
            font=ctk.CTkFont(size=13),
            text_color=self.theme.c("text_secondary"),
        ).pack(side="left", padx=(0, 12))
        ctk.CTkButton(
            theme_frame,
            text="Toggle Dark / Light",
            command=self._toggle_theme,
            width=160,
            height=36,
            corner_radius=8,
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

    def _toggle_theme(self) -> None:
        new_theme = self.theme.toggle()
        self.log(f"Theme switched to: {new_theme}")
        if self.theme_toggle_callback:
            self.theme_toggle_callback(new_theme)
