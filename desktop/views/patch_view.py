"""Patch management view."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from backend.models.schemas import PatchRequest
from backend.services.patch_service import PatchService
from desktop.ops_widgets import page_header
from desktop.theme import ThemeManager, themed_scrollable_frame
from desktop.views.base_view import BaseView


class PatchView(BaseView):
    """SQL patch execution and history."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = PatchService()
        self._build_ui()

    def refresh(self, *, force: bool = False) -> None:
        """Re-sync patching page with the active connection."""
        del force
        self.log("Patching page refreshed")

    def _build_ui(self) -> None:
        # Same shell as Backup / Restore: page fills the content column.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = themed_scrollable_frame(self, self.theme)
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)

        page_header(
            scroll,
            self.theme,
            "Patching",
            subtitle="Execute SQL patch scripts and review history.",
            pady=(0, 12),
        )

        form = self.card_frame(scroll)
        form.pack(fill="x", pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)

        label_kwargs = {
            "font": ctk.CTkFont(size=12),
            "text_color": self.theme.c("text_secondary"),
            "anchor": "w",
        }

        ctk.CTkLabel(form, text="Connection", **label_kwargs).grid(
            row=0, column=0, padx=16, pady=10, sticky="w"
        )
        ctk.CTkLabel(
            form,
            text=self.connection.summary if self.connection else "default",
            font=ctk.CTkFont(size=13),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=0, column=1, padx=16, pady=10, sticky="ew")

        ctk.CTkLabel(form, text="SQL File", **label_kwargs).grid(
            row=1, column=0, padx=16, pady=10, sticky="w"
        )
        path_frame = ctk.CTkFrame(form, fg_color="transparent")
        path_frame.grid(row=1, column=1, padx=16, pady=10, sticky="ew")
        path_frame.grid_columnconfigure(0, weight=1)
        self.sql_file = ctk.CTkEntry(path_frame, placeholder_text="Path to .sql patch file")
        self.sql_file.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            path_frame,
            text="Browse",
            width=90,
            command=self._browse,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_primary"),
            border_width=1,
            border_color=self.theme.c("border"),
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(form, text="Description", **label_kwargs).grid(
            row=2, column=0, padx=16, pady=10, sticky="w"
        )
        self.description = ctk.CTkEntry(
            form, placeholder_text="Optional note for patch history"
        )
        self.description.grid(row=2, column=1, padx=16, pady=10, sticky="ew")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=3, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="ew")
        ctk.CTkButton(
            btn_frame,
            text="Execute Patch",
            command=self._execute,
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
        ).pack(side="left")
        ctk.CTkButton(
            btn_frame,
            text="View History",
            command=self._show_history,
            width=140,
            height=38,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_primary"),
            border_width=1,
            border_color=self.theme.c("border"),
        ).pack(side="left", padx=(8, 0))

        results_card = self.card_frame(scroll)
        results_card.pack(fill="both", expand=True, pady=(0, 8))
        results_card.grid_columnconfigure(0, weight=1)
        results_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            results_card,
            text="Results",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")

        self.results = ctk.CTkTextbox(results_card, height=280, activate_scrollbars=True)
        self.results.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("SQL files", "*.sql"), ("All", "*.*")])
        if path:
            self.sql_file.delete(0, "end")
            self.sql_file.insert(0, path)

    def _execute(self) -> None:
        def task():
            try:
                request = PatchRequest(
                    profile=self.get_active_profile(),
                    db_type=self.get_active_db_type(),
                    sql_file=self.sql_file.get(),
                    description=self.description.get(),
                )
                entry = self.service.execute_patch(request)
                msg = f"Patch {entry.patch_id}: {entry.status.value}\n"
                self.after(0, lambda: self.results.insert("end", msg))
                self.log(f"Patch executed: {entry.patch_id}")
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"Patch failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()

    def _show_history(self) -> None:
        history = self.service.get_history()
        self.results.delete("1.0", "end")
        for entry in reversed(history):
            self.results.insert(
                "end",
                f"[{entry.executed_at}] {entry.patch_id[:8]}... - {entry.status.value} - {entry.description}\n",
            )
