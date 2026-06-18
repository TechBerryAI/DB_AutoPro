"""Enhanced execution log with badges, filtering, search, and auto-scroll."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import customtkinter as ctk

from desktop.theme import ThemeManager

LEVELS = ("ALL", "INFO", "SUCCESS", "WARNING", "ERROR")
LEVEL_COLORS = {
    "INFO": "#94a3b8",
    "SUCCESS": "#22c55e",
    "WARNING": "#f59e0b",
    "ERROR": "#ef4444",
}
LEVEL_BADGE_BG = {
    "INFO": "#1e293b",
    "SUCCESS": "#14532d",
    "WARNING": "#78350f",
    "ERROR": "#7f1d1d",
}


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str


class ExecutionLogPanel(ctk.CTkFrame):
    """Color-coded execution log with filter, search, and auto-scroll."""

    def __init__(self, master, theme: ThemeManager, **kwargs) -> None:
        super().__init__(master, fg_color=theme.c("bg_sidebar"), **kwargs)
        self.theme = theme
        self._entries: list[LogEntry] = []
        self._auto_scroll = ctk.BooleanVar(value=True)
        self._build_ui()
        self._configure_tags()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 4))
        toolbar.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            toolbar,
            text="EXECUTION LOG",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self.theme.c("text_muted"),
        ).grid(row=0, column=0, sticky="w")

        self._filter = ctk.CTkComboBox(
            toolbar,
            values=list(LEVELS),
            width=100,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=self.theme.c("bg_elevated"),
            border_color=self.theme.c("border"),
            button_color=self.theme.c("bg_card_hover"),
            dropdown_fg_color=self.theme.c("bg_card"),
            command=lambda _: self._render(),
        )
        self._filter.set("ALL")
        self._filter.grid(row=0, column=1, padx=(12, 8))

        self._search = ctk.CTkEntry(
            toolbar,
            placeholder_text="Search logs…",
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=self.theme.c("bg_elevated"),
            border_color=self.theme.c("border"),
        )
        self._search.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self._search.bind("<KeyRelease>", lambda _: self._render())

        ctk.CTkCheckBox(
            toolbar,
            text="Auto-scroll",
            variable=self._auto_scroll,
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_secondary"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            border_color=self.theme.c("border"),
            width=20,
            height=20,
        ).grid(row=0, column=3, padx=(0, 4))

        clear_btn = ctk.CTkButton(
            toolbar,
            text="Clear",
            width=60,
            height=28,
            corner_radius=6,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            font=ctk.CTkFont(size=11),
            command=self.clear,
        )
        clear_btn.grid(row=0, column=4)

        self._text = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=self.theme.c("bg_elevated"),
            border_width=0,
            text_color=self.theme.c("text_secondary"),
            activate_scrollbars=True,
        )
        self._text.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="nsew")
        self._text.configure(state="disabled")

    def _configure_tags(self) -> None:
        tb = self._text._textbox
        for level, color in LEVEL_COLORS.items():
            tb.tag_configure(level, foreground=color)
            tb.tag_configure(f"badge_{level}", foreground=color)
        tb.tag_configure("timestamp", foreground=self.theme.c("text_muted"))
        tb.tag_configure("message", foreground=self.theme.c("text_secondary"))

    def add_entry(self, message: str, level: str = "INFO") -> None:
        level = level.upper()
        if level not in LEVEL_COLORS:
            level = "INFO"
        entry = LogEntry(datetime.now().strftime("%H:%M:%S"), level, message)
        self._entries.append(entry)
        if len(self._entries) > 500:
            self._entries = self._entries[-500:]
        self._render()

    def clear(self) -> None:
        self._entries.clear()
        self._render()

    def _render(self) -> None:
        filter_level = self._filter.get()
        query = self._search.get().strip().lower()

        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        tb = self._text._textbox
        for entry in self._entries:
            if filter_level != "ALL" and entry.level != filter_level:
                continue
            if query and query not in entry.message.lower() and query not in entry.level.lower():
                continue
            tb.insert("end", f"[{entry.timestamp}] ", "timestamp")
            tb.insert("end", f" {entry.level:<7} ", entry.level)
            tb.insert("end", f"{entry.message}\n", "message")

        self._text.configure(state="disabled")
        if self._auto_scroll.get():
            self._text.see("end")
