"""Compact status footer bar for the main application shell."""

from __future__ import annotations

import customtkinter as ctk

from desktop.design_system import font
from desktop.theme import ThemeManager


class StatusFooterBar(ctk.CTkFrame):
    """Subtle application status bar — muted, compact, non-competing."""

    def __init__(self, master, theme: ThemeManager, **kwargs) -> None:
        super().__init__(
            master,
            height=32,
            corner_radius=0,
            fg_color=theme.c("bg_content"),
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
        inner.pack(fill="both", expand=True, padx=20, pady=4)

        fields = [
            ("connection", "● Disconnected"),
            ("version", "Version —"),
            ("uptime", "Uptime —"),
            ("sessions", "Sessions —"),
            ("refresh", "Last refresh —"),
        ]
        for i, (key, default) in enumerate(fields):
            if i > 0:
                ctk.CTkLabel(
                    inner,
                    text="|",
                    font=font(self.theme, 11),
                    text_color=self.theme.c("border"),
                ).pack(side="left", padx=10)
            lbl = ctk.CTkLabel(
                inner,
                text=default,
                font=font(self.theme, 11),
                text_color=self.theme.c("text_muted"),
            )
            lbl.pack(side="left")
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
            text = connection_status
            if not connected and connection_status.strip() in {"● Disconnected", "Disconnected"}:
                text = "● Disconnected"
            self._labels["connection"].configure(text=text, text_color=color)
        if version:
            self._labels["version"].configure(
                text=f"Version {version}" if not version.lower().startswith("version") else version
            )
        if uptime:
            self._labels["uptime"].configure(
                text=f"Uptime {uptime}" if not uptime.lower().startswith("uptime") else uptime
            )
        if sessions:
            self._labels["sessions"].configure(
                text=f"Sessions {sessions}" if not sessions.lower().startswith("sessions") else sessions
            )
        if last_refresh:
            text = last_refresh
            if not last_refresh.lower().startswith("last"):
                text = f"Last refresh {last_refresh}"
            self._labels["refresh"].configure(text=text)

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        self.configure(fg_color=theme.c("bg_content"))
        for lbl in self._labels.values():
            lbl.configure(text_color=theme.c("text_muted"))
