"""Enhanced execution log with badges, filtering, search, and auto-scroll."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from desktop.log_export import default_log_export_stem, export_logs_csv, export_logs_pdf
from desktop.theme import ThemeManager

LEVELS = ("ALL", "INFO", "SUCCESS", "WARNING", "ERROR")


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str


class ExecutionLogStore:
    """Shared in-memory log buffer used by the app and the Logs view."""

    MAX_ENTRIES = 500

    def __init__(self) -> None:
        self.entries: list[LogEntry] = []

    def add(self, message: str, level: str = "INFO") -> LogEntry:
        level = level.upper()
        if level not in ("INFO", "SUCCESS", "WARNING", "ERROR"):
            level = "INFO"
        entry = LogEntry(datetime.now().strftime("%H:%M:%S"), level, message)
        self.entries.append(entry)
        if len(self.entries) > self.MAX_ENTRIES:
            self.entries[:] = self.entries[-self.MAX_ENTRIES :]
        return entry

    def clear(self) -> None:
        self.entries.clear()


class ExecutionLogPanel(ctk.CTkFrame):
    """Color-coded execution log with filter, search, and auto-scroll."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        store: ExecutionLogStore | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=self._panel_bg(theme), border_width=1, border_color=theme.c("border"), **kwargs)
        self.theme = theme
        self.store = store or ExecutionLogStore()
        self._auto_scroll = ctk.BooleanVar(value=True)
        self._build_ui()
        self._configure_tags()
        self._render()

    @staticmethod
    def _panel_bg(theme: ThemeManager) -> str:
        return theme.c("bg_card")

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
        ).grid(row=0, column=3, padx=(0, 8))

        download_menu = ctk.CTkOptionMenu(
            toolbar,
            values=["Download CSV", "Download PDF"],
            command=self._on_download,
            width=120,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=self.theme.c("bg_card"),
            button_color=self.theme.c("bg_card_hover"),
            button_hover_color=self.theme.c("bg_elevated"),
            dropdown_fg_color=self.theme.c("bg_card"),
            text_color=self.theme.c("text_primary"),
        )
        download_menu.set("Download")
        download_menu.grid(row=0, column=4, padx=(0, 8))

        clear_btn = ctk.CTkButton(
            toolbar,
            text="Clear",
            width=60,
            height=28,
            corner_radius=6,
            fg_color=self.theme.c("bg_card"),
            hover_color=self.theme.c("bg_card_hover"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_secondary"),
            font=ctk.CTkFont(size=11),
            command=self.clear,
        )
        clear_btn.grid(row=0, column=5)

        self._text = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=self.theme.c("bg_card"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_secondary"),
            activate_scrollbars=True,
        )
        self._text.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="nsew")
        self._text.configure(state="disabled")

    def _configure_tags(self) -> None:
        tb = self._text._textbox
        for level in ("INFO", "SUCCESS", "WARNING", "ERROR"):
            color = self.theme.log_level_color(level)
            tb.tag_configure(level, foreground=color)
            tb.tag_configure(f"badge_{level}", foreground=color)
        tb.tag_configure("timestamp", foreground=self.theme.c("text_muted"))
        tb.tag_configure("message", foreground=self.theme.c("text_primary"))

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        self.configure(fg_color=self._panel_bg(theme), border_color=theme.c("border"))
        self._filter.configure(
            fg_color=theme.c("bg_elevated"),
            border_color=theme.c("border"),
            button_color=theme.c("bg_card_hover"),
            dropdown_fg_color=theme.c("bg_card"),
        )
        self._search.configure(fg_color=theme.c("bg_elevated"), border_color=theme.c("border"))
        self._text.configure(
            fg_color=theme.c("bg_card"),
            border_color=theme.c("border"),
            text_color=theme.c("text_secondary"),
        )
        self._configure_tags()

    def add_entry(self, message: str, level: str = "INFO") -> None:
        self.store.add(message, level)
        self._render()

    def clear(self) -> None:
        self.store.clear()
        self._render()

    def filtered_entries(self) -> list[LogEntry]:
        """Entries matching the current level filter and search query."""
        filter_level = self._filter.get()
        query = self._search.get().strip().lower()
        rows: list[LogEntry] = []
        for entry in self.store.entries:
            if filter_level != "ALL" and entry.level != filter_level:
                continue
            if query and query not in entry.message.lower() and query not in entry.level.lower():
                continue
            rows.append(entry)
        return rows

    def _export_rows(self) -> list[tuple[str, str, str]]:
        return [(e.timestamp, e.level, e.message) for e in self.filtered_entries()]

    def _on_download(self, choice: str) -> None:
        rows = self._export_rows()
        if not rows:
            messagebox.showinfo("Download", "No log entries match the current filter.")
            return

        stem = default_log_export_stem()
        if choice == "Download CSV":
            path = filedialog.asksaveasfilename(
                title="Download execution log (CSV)",
                defaultextension=".csv",
                initialfile=f"{stem}.csv",
                filetypes=[("CSV files", "*.csv")],
            )
            if path:
                saved = export_logs_csv(rows, path)
                messagebox.showinfo("Download", f"Log saved to:\n{saved}")
        elif choice == "Download PDF":
            path = filedialog.asksaveasfilename(
                title="Download execution log (PDF)",
                defaultextension=".pdf",
                initialfile=f"{stem}.pdf",
                filetypes=[("PDF files", "*.pdf")],
            )
            if path:
                saved = export_logs_pdf(rows, path)
                messagebox.showinfo("Download", f"Log saved to:\n{saved}")

    def _render(self) -> None:
        filter_level = self._filter.get()
        query = self._search.get().strip().lower()

        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        tb = self._text._textbox
        for entry in self.filtered_entries():
            tb.insert("end", f"[{entry.timestamp}] ", "timestamp")
            tb.insert("end", f" {entry.level:<7} ", entry.level)
            tb.insert("end", f"{entry.message}\n", "message")

        self._text.configure(state="disabled")
        if self._auto_scroll.get():
            self._text.see("end")
