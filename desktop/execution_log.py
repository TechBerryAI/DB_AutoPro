"""Enhanced execution log with badges, filtering, search, and auto-scroll."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from desktop.design_system import font
from desktop.log_export import default_log_export_stem, export_logs_csv, export_logs_pdf
from desktop.theme import ThemeManager

LEVELS = ("ALL", "INFO", "SUCCESS", "WARNING", "ERROR")

# Dashboard deep-link presets → chip label + match rules.
PRESET_FILTERS: dict[str, dict] = {
    "alerts": {
        "label": "Errors & warnings",
        "levels": frozenset({"WARNING", "ERROR"}),
        "keywords": (),
    },
    "jobs": {
        "label": "Running jobs (backup / restore)",
        "levels": None,  # any level
        "keywords": (
            "backup",
            "restore",
            "running",
            "in progress",
            "automation job",
            "pg_dump",
            "mysqldump",
            "mongodump",
            "mongorestore",
        ),
    },
}


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
        super().__init__(
            master,
            fg_color=self._panel_bg(theme),
            corner_radius=theme.radius("lg"),
            border_width=1,
            border_color=theme.c("border"),
            **kwargs,
        )
        self.theme = theme
        self.store = store or ExecutionLogStore()
        self._auto_scroll = ctk.BooleanVar(value=True)
        self._preset_filter: str | None = None
        self._chip_row: ctk.CTkFrame | None = None
        self._chip_label: ctk.CTkLabel | None = None
        self._build_ui()
        self._configure_tags()
        self._render()

    @staticmethod
    def _panel_bg(theme: ThemeManager) -> str:
        return theme.c("terminal_bg") if not theme.is_light else theme.c("bg_card")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._chip_row = ctk.CTkFrame(self, fg_color="transparent")
        # Hidden until a dashboard preset is applied.
        self._chip_inner = ctk.CTkFrame(
            self._chip_row,
            fg_color=self.theme.c("primary_muted"),
            corner_radius=self.theme.radius("pill"),
            border_width=1,
            border_color=self.theme.c("border"),
        )
        self._chip_label = ctk.CTkLabel(
            self._chip_inner,
            text="",
            font=font(self.theme, 11),
            text_color=self.theme.c("text_primary"),
        )
        self._chip_label.pack(side="left", padx=(12, 4), pady=4)
        clear_chip = ctk.CTkButton(
            self._chip_inner,
            text="✕",
            width=28,
            height=24,
            corner_radius=12,
            fg_color="transparent",
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_secondary"),
            font=font(self.theme, 12),
            cursor="hand2",
            command=self.clear_preset_filter,
        )
        clear_chip.pack(side="left", padx=(0, 6), pady=4)
        self._chip_inner.pack(side="left")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 6))
        toolbar.grid_columnconfigure(2, weight=1)

        header = ctk.CTkFrame(toolbar, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w")
        ctk.CTkFrame(header, width=3, height=14, corner_radius=2, fg_color=self.theme.c("primary")).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            header,
            text="EXECUTION LOG",
            font=font(self.theme, 10, weight="bold"),
            text_color=self.theme.c("text_muted"),
        ).pack(side="left")

        self._filter = ctk.CTkComboBox(
            toolbar,
            values=list(LEVELS),
            width=108,
            height=32,
            font=font(self.theme, 11),
            fg_color=self.theme.c("bg_elevated"),
            border_color=self.theme.c("border"),
            button_color=self.theme.c("bg_card_hover"),
            dropdown_fg_color=self.theme.c("bg_card"),
            command=lambda _: self._render(),
        )
        self._filter.set("ALL")
        self._filter.grid(row=0, column=1, padx=(14, 8))

        self._search = ctk.CTkEntry(
            toolbar,
            placeholder_text="Search logs…",
            height=32,
            font=font(self.theme, 11),
            fg_color=self.theme.c("bg_elevated"),
            border_color=self.theme.c("border"),
            corner_radius=self.theme.radius("sm"),
        )
        self._search.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self._search.bind("<KeyRelease>", lambda _: self._render())

        ctk.CTkCheckBox(
            toolbar,
            text="Auto-scroll",
            variable=self._auto_scroll,
            font=font(self.theme, 11),
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
            width=64,
            height=32,
            corner_radius=self.theme.radius("sm"),
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_secondary"),
            font=font(self.theme, 11),
            command=self.clear,
        )
        clear_btn.grid(row=0, column=5)

        self._text = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Cascadia Mono", size=11),
            fg_color=self.theme.c("terminal_bg") if not self.theme.is_light else self.theme.c("bg_input"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_secondary"),
            corner_radius=self.theme.radius("md"),
            activate_scrollbars=True,
        )
        self._text.grid(row=2, column=0, padx=16, pady=(0, 12), sticky="nsew")
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
            fg_color=theme.c("terminal_bg") if not theme.is_light else theme.c("bg_input"),
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

    def refresh(self) -> None:
        """Re-draw entries from the shared store (header Refresh)."""
        self._render()

    def apply_preset_filter(self, preset: str | None) -> None:
        """Apply or clear a named preset from Dashboard deep-links."""
        if preset and preset in PRESET_FILTERS:
            self._preset_filter = preset
            spec = PRESET_FILTERS[preset]
            if self._chip_label is not None:
                self._chip_label.configure(text=spec["label"])
            if self._chip_row is not None and not self._chip_row.winfo_manager():
                self._chip_row.grid(row=0, column=0, sticky="w", padx=16, pady=(10, 0))
            # Keep the level combo aligned with alerts when possible.
            if preset == "alerts":
                try:
                    self._filter.set("ALL")
                except Exception:
                    pass
        else:
            self.clear_preset_filter()
            return
        self._render()

    def clear_preset_filter(self) -> None:
        self._preset_filter = None
        if self._chip_row is not None and self._chip_row.winfo_manager():
            self._chip_row.grid_forget()
        self._render()

    def _matches_preset(self, entry: LogEntry) -> bool:
        if not self._preset_filter:
            return True
        spec = PRESET_FILTERS.get(self._preset_filter)
        if not spec:
            return True
        levels = spec.get("levels")
        if levels is not None and entry.level not in levels:
            return False
        keywords = spec.get("keywords") or ()
        if keywords:
            msg = entry.message.lower()
            if not any(k in msg for k in keywords):
                return False
        return True

    def filtered_entries(self) -> list[LogEntry]:
        """Entries matching the current level filter, preset, and search query."""
        filter_level = self._filter.get()
        query = self._search.get().strip().lower()
        rows: list[LogEntry] = []
        for entry in self.store.entries:
            if not self._matches_preset(entry):
                continue
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
