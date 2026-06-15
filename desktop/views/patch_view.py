"""Patch management view."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from backend.models.schemas import DatabaseType, PatchRequest
from backend.services.patch_service import PatchService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class PatchView(BaseView):
    """SQL patch execution and history."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = PatchService()
        self._build_ui()

    def _build_ui(self) -> None:
        title = self.create_section_title("Patch Management")
        title.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        form = ctk.CTkFrame(self, corner_radius=10)
        form.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")

        ctk.CTkLabel(form, text="SQL File:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        path_frame = ctk.CTkFrame(form, fg_color="transparent")
        path_frame.grid(row=0, column=1, padx=10, pady=8)
        self.sql_file = ctk.CTkEntry(path_frame, width=300)
        self.sql_file.pack(side="left")
        ctk.CTkButton(path_frame, text="Browse", width=80, command=self._browse).pack(side="left", padx=5)

        ctk.CTkLabel(form, text="Description:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
        self.description = ctk.CTkEntry(form, width=300)
        self.description.grid(row=1, column=1, padx=10, pady=8)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=20, pady=10, sticky="w")
        ctk.CTkButton(btn_frame, text="Execute Patch", command=self._execute, width=120).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="View History", command=self._show_history, width=120).pack(side="left", padx=5)

        self.results = ctk.CTkTextbox(self, height=300)
        self.results.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")
        self.grid_rowconfigure(3, weight=1)

    def _browse(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("SQL files", "*.sql"), ("All", "*.*")])
        if path:
            self.sql_file.delete(0, "end")
            self.sql_file.insert(0, path)

    def _execute(self) -> None:
        def task():
            try:
                request = PatchRequest(
                    db_type=DatabaseType.POSTGRESQL,
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
