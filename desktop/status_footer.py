"""Compact status footer bar for the main application shell."""

from __future__ import annotations

import customtkinter as ctk

from desktop.design_system import font
from desktop.theme import ThemeManager


class StatusFooterBar(ctk.CTkFrame):
    """Footer showing connection status, version, sessions, and last refresh."""

    def __init__(self, master, theme: ThemeManager, **kwargs) -> None:
        super().__init__(
            master,
            height=48,
            corner_radius=0,
            fg_color=theme.c("bg_card") if theme.is_light else theme.c("bg_glass"),
            border_width=0,
            **kwargs,
        )
        self.theme = theme
        self._labels: dict[str, ctk.CTkLabel] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_propagate(False)
        ctk.CTkFrame(self, height=1, fg_color=self.theme.c("border_subtle")).pack(fill="x")
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=24, pady=10)

        fields = [
            ("connection", "● Disconnected"),
            ("version", "Version —"),
            ("uptime", "Uptime —"),
            ("sessions", "Sessions —"),
            ("refresh", "Last refresh —"),
        ]
        for key, default in fields:
            pill = ctk.CTkFrame(
                inner,
                fg_color=self.theme.c("bg_elevated"),
                corner_radius=999,
                border_width=1,
                border_color=self.theme.c("border_subtle"),
            )
            pill.pack(side="left", padx=(0, 10))
            lbl = ctk.CTkLabel(
                pill,
                text=default,
                font=font(self.theme, 11, weight="bold"),
                text_color=self.theme.c("text_muted"),
            )
            lbl.pack(padx=14, pady=4)
            self._labels[key] = lbl

    def update(
        self,
        *,
        connection_status: str = "",
        version: str = "",
        uptime: str = "",
        sessions: str = "",
        last_refresh: str = "",
    ) -> None:
        if connection_status:
            connected = "connected" in connection_status.lower()
            color = self.theme.c("success") if connected else self.theme.c("text_muted")
            self._labels["connection"].configure(text=connection_status, text_color=color)
        if version:
            self._labels["version"].configure(text=f"DB {version}")
        if uptime:
            self._labels["uptime"].configure(text=f"Engine {uptime}")
        if sessions:
            self._labels["sessions"].configure(text=f"Sessions {sessions}")
        if last_refresh:
            self._labels["refresh"].configure(text=f"Refreshed {last_refresh}")

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        self.configure(
            fg_color=theme.c("bg_card") if theme.is_light else theme.c("bg_glass"),
        )
        for lbl in self._labels.values():
            lbl.configure(text_color=theme.c("text_muted"))
