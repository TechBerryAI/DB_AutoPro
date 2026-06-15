"""Restore management view."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from backend.models.schemas import DatabaseType, RestoreRequest
from backend.services.restore_service import RestoreService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class RestoreView(BaseView):
    """Database restore operations."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = RestoreService()
        self._build_ui()

    def _build_ui(self) -> None:
        row = 0
        context = self.create_connection_context()
        if context:
            context.grid(row=row, column=0, padx=20, pady=(12, 8), sticky="ew")
            row += 1

        title = self.create_section_title("Restore Management")
        title.grid(row=row, column=0, padx=20, pady=20, sticky="w")
        row += 1

        form = self.card_frame(self)
        form.grid(row=row, column=0, padx=20, pady=10, sticky="nsew")
        row += 1

        active_type = self.get_active_db_type().value
        ctk.CTkLabel(form, text="Database Type:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.db_type = ctk.CTkComboBox(form, values=[t.value for t in DatabaseType], width=200)
        self.db_type.set(active_type)
        self.db_type.grid(row=0, column=1, padx=10, pady=8)
        if self.connection:
            self.db_type.configure(state="disabled")

        ctk.CTkLabel(form, text="Connection:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
        ctk.CTkLabel(form, text=self.connection.name if self.connection else "default").grid(
            row=1, column=1, padx=10, pady=8, sticky="w"
        )

        ctk.CTkLabel(form, text="Backup Path:").grid(row=2, column=0, padx=10, pady=8, sticky="w")
        path_frame = ctk.CTkFrame(form, fg_color="transparent")
        path_frame.grid(row=2, column=1, padx=10, pady=8, sticky="w")
        self.backup_path = ctk.CTkEntry(path_frame, width=300)
        self.backup_path.pack(side="left")
        ctk.CTkButton(path_frame, text="Browse", width=80, command=self._browse).pack(side="left", padx=5)

        self.validate_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(form, text="Validate before restore", variable=self.validate_var).grid(
            row=3, column=0, columnspan=2, padx=10, pady=8, sticky="w"
        )

        ctk.CTkButton(self, text="Run Restore", command=self._run_restore, width=120).grid(
            row=row, column=0, padx=20, pady=10, sticky="w"
        )
        row += 1

        self.progress, self.status_label = self.create_progress_section()
        self.progress.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1
        self.status_label.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1

        self.results = ctk.CTkTextbox(self, height=200)
        self.results.grid(row=row, column=0, padx=20, pady=10, sticky="nsew")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(title="Select Backup File")
        if path:
            self.backup_path.delete(0, "end")
            self.backup_path.insert(0, path)

    def _update_progress(self, value: float, message: str) -> None:
        self.after(0, lambda: self.progress.set(value))
        self.after(0, lambda: self.status_label.configure(text=message))

    def _run_restore(self) -> None:
        def task():
            try:
                request = RestoreRequest(
                    profile=self.get_active_profile(),
                    db_type=self.get_active_db_type(),
                    backup_path=self.backup_path.get(),
                    validate_backup=self.validate_var.get(),
                )
                result = self.service.restore(request, progress_callback=self._update_progress)
                self.after(0, lambda: self.results.insert("end", f"Restore completed: {result.message}\n"))
                self.log("Restore completed")
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"Restore failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()
