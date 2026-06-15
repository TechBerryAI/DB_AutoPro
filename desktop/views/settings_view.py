"""Settings view for environment and theme configuration."""

from __future__ import annotations

import customtkinter as ctk

from backend.core.config import get_settings
from desktop.theme import ThemeManager
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
        title = self.create_section_title("Settings")
        title.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        settings = get_settings()

        info = ctk.CTkFrame(self, corner_radius=10)
        info.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")

        labels = [
            ("Application", settings.app_name),
            ("Environment", settings.environment),
            ("API Port", str(settings.api_port)),
            ("Database Type", settings.database_type),
            ("Backup Retention", f"{settings.backup.retention_days} days"),
            ("Log Level", settings.logging.level),
        ]

        for i, (label, value) in enumerate(labels):
            ctk.CTkLabel(info, text=f"{label}:", font=ctk.CTkFont(weight="bold")).grid(
                row=i, column=0, padx=15, pady=8, sticky="w"
            )
            ctk.CTkLabel(info, text=value).grid(row=i, column=1, padx=15, pady=8, sticky="w")

        theme_frame = ctk.CTkFrame(self, fg_color="transparent")
        theme_frame.grid(row=2, column=0, padx=20, pady=20, sticky="w")

        ctk.CTkLabel(theme_frame, text=f"Current Theme: {self.theme.current}").pack(side="left", padx=5)
        ctk.CTkButton(
            theme_frame,
            text="Toggle Dark/Light",
            command=self._toggle_theme,
            width=160,
        ).pack(side="left", padx=10)

    def _toggle_theme(self) -> None:
        new_theme = self.theme.toggle()
        self.log(f"Theme switched to: {new_theme}")
        if self.theme_toggle_callback:
            self.theme_toggle_callback(new_theme)
