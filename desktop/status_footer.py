"""Compact status footer bar for the main application shell."""

from __future__ import annotations

import re

import customtkinter as ctk

from desktop.design_system import font
from desktop.theme import ThemeManager


def format_footer_version(raw: str) -> str:
    """Turn verbose engine version() output into a short footer label."""
    text = (raw or "").strip()
    if not text or text in {"—", "-", "…", "..."}:
        return "—"
    if text.lower().startswith("version "):
        text = text[8:].strip()

    patterns = (
        r"(PostgreSQL)\s+(\d+(?:\.\d+)+)",
        r"(MySQL|MariaDB)\s+(\d+(?:\.\d+)+)",
        r"(MongoDB)\s+[^\d]*(\d+(?:\.\d+)+)",
        r"(Oracle Database)\s+(\d+[a-zA-Z0-9.]*)",
        r"(Microsoft SQL Server)\s+(\d+(?:\.\d+)*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            engine = match.group(1)
            nice = {
                "postgresql": "PostgreSQL",
                "mysql": "MySQL",
                "mariadb": "MariaDB",
                "mongodb": "MongoDB",
                "oracle database": "Oracle",
                "microsoft sql server": "SQL Server",
            }.get(engine.lower(), engine)
            return f"{nice} {match.group(2)}"

    match = re.search(r"\b(\d+(?:\.\d+){1,3})\b", text)
    if match and len(text) > 24:
        return match.group(1)
    if len(text) > 28:
        return text[:25].rstrip() + "…"
    return text


class StatusFooterBar(ctk.CTkFrame):
    """Labelled footer segments at fixed positions (not a pipe-separated soup)."""

    _SEGMENTS = (
        ("connection", "Connection"),
        ("version", "Version"),
        ("uptime", "Uptime"),
        ("sessions", "Sessions"),
    )

    def __init__(self, master, theme: ThemeManager, **kwargs) -> None:
        super().__init__(
            master,
            height=36,
            corner_radius=0,
            fg_color=theme.c("bg_content"),
            border_width=0,
            **kwargs,
        )
        self.theme = theme
        self._captions: dict[str, ctk.CTkLabel] = {}
        self._values: dict[str, ctk.CTkLabel] = {}
        self._build_ui()
        self.show_idle()

    def _build_ui(self) -> None:
        self.grid_propagate(False)
        ctk.CTkFrame(self, height=1, fg_color=self.theme.c("border_subtle")).pack(fill="x")
        self._inner = ctk.CTkFrame(self, fg_color="transparent")
        self._inner.pack(fill="both", expand=True, padx=16, pady=4)
        for i in range(len(self._SEGMENTS)):
            self._inner.grid_columnconfigure(i, weight=1, uniform="footer_seg")

        for i, (key, caption) in enumerate(self._SEGMENTS):
            cell = ctk.CTkFrame(self._inner, fg_color="transparent")
            cell.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            cap = ctk.CTkLabel(
                cell,
                text=caption,
                font=font(self.theme, 10),
                text_color=self.theme.c("text_secondary"),
                anchor="w",
            )
            cap.pack(anchor="w")
            val = ctk.CTkLabel(
                cell,
                text="—",
                font=font(self.theme, 12),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            )
            val.pack(anchor="w")
            self._captions[key] = cap
            self._values[key] = val

    def show_idle(self) -> None:
        self._values["connection"].configure(
            text="Disconnected · No active session",
            text_color=self.theme.c("text_secondary"),
        )
        for key in ("version", "uptime", "sessions"):
            self._values[key].configure(text="—", text_color=self.theme.c("text_secondary"))

    def show_connecting(self, connection_status: str) -> None:
        self._values["connection"].configure(
            text=_strip_connection_prefix(connection_status) or "Connected",
            text_color=self.theme.c("success"),
        )
        for key in ("version", "uptime", "sessions"):
            self._values[key].configure(text="…", text_color=self.theme.c("text_muted"))

    def update(
        self,
        *,
        connection_status: str = "",
        version: str = "",
        uptime: str = "",
        sessions: str = "",
        last_refresh: str = "",  # kept for callers; refresh time lives in the shell header
        idle: bool = False,
    ) -> None:
        del last_refresh
        if idle or (connection_status and "disconnected" in connection_status.lower()):
            self.show_idle()
            return

        if connection_status and not any((version, uptime, sessions)):
            self.show_connecting(connection_status)
            return

        if connection_status:
            self._values["connection"].configure(
                text=_strip_connection_prefix(connection_status) or connection_status,
                text_color=self.theme.c("success"),
            )
        if version:
            short = format_footer_version(version)
            self._values["version"].configure(
                text=short,
                text_color=self.theme.c("text_secondary"),
            )
        if uptime:
            text = uptime
            if text.lower().startswith("uptime"):
                text = text.split(None, 1)[-1]
            self._values["uptime"].configure(
                text=text or "—",
                text_color=self.theme.c("text_secondary"),
            )
        if sessions != "":
            text = sessions
            lower = sessions.lower()
            if lower.startswith("db sessions"):
                text = sessions.split(None, 2)[-1]
            elif lower.startswith("sessions"):
                text = sessions.split(None, 1)[-1]
            self._values["sessions"].configure(
                text=text,
                text_color=self.theme.c("text_secondary"),
            )

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        self.configure(fg_color=theme.c("bg_content"))
        for cap in self._captions.values():
            cap.configure(text_color=theme.c("text_secondary"))
        connected = "disconnect" not in self._values["connection"].cget("text").lower()
        self._values["connection"].configure(
            text_color=theme.c("success") if connected else theme.c("text_secondary")
        )
        for key in ("version", "uptime", "sessions"):
            self._values[key].configure(text_color=theme.c("text_primary"))


def _strip_connection_prefix(status: str) -> str:
    text = (status or "").strip()
    if text.startswith("●"):
        text = text[1:].strip()
    if text.lower().startswith("connected"):
        # "Connected · name" → keep "name" under Connection caption when possible
        parts = re.split(r"[·|]", text, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
        return "Connected"
    return text
