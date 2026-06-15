"""Migration management view."""

from __future__ import annotations

import threading

import customtkinter as ctk

from backend.models.schemas import DatabaseType, MigrationRequest
from backend.services.migration_service import MigrationService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class MigrationView(BaseView):
    """Schema and data migration operations."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = MigrationService()
        self._build_ui()

    def _build_ui(self) -> None:
        title = self.create_section_title("Migration")
        title.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        form = ctk.CTkFrame(self, corner_radius=10)
        form.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")

        db_types = [t.value for t in DatabaseType]

        ctk.CTkLabel(form, text="Source Type:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.source_type = ctk.CTkComboBox(form, values=db_types, width=200)
        self.source_type.set("postgresql")
        self.source_type.grid(row=0, column=1, padx=10, pady=8)

        ctk.CTkLabel(form, text="Target Type:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
        self.target_type = ctk.CTkComboBox(form, values=db_types, width=200)
        self.target_type.set("postgresql")
        self.target_type.grid(row=1, column=1, padx=10, pady=8)

        self.schema_only_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Schema only", variable=self.schema_only_var).grid(
            row=2, column=0, columnspan=2, padx=10, pady=8, sticky="w"
        )

        ctk.CTkButton(self, text="Start Migration", command=self._run_migration, width=140).grid(
            row=2, column=0, padx=20, pady=10, sticky="w"
        )

        self.progress, self.status_label = self.create_progress_section()
        self.progress.grid(row=3, column=0, padx=20, pady=5, sticky="w")
        self.status_label.grid(row=4, column=0, padx=20, pady=5, sticky="w")

        self.results = ctk.CTkTextbox(self, height=200)
        self.results.grid(row=5, column=0, padx=20, pady=10, sticky="nsew")

    def _update_progress(self, value: float, message: str) -> None:
        self.after(0, lambda: self.progress.set(value))
        self.after(0, lambda: self.status_label.configure(text=message))

    def _run_migration(self) -> None:
        def task():
            try:
                request = MigrationRequest(
                    source_type=DatabaseType(self.source_type.get()),
                    target_type=DatabaseType(self.target_type.get()),
                    schema_only=self.schema_only_var.get(),
                )
                result = self.service.migrate(request, progress_callback=self._update_progress)
                self.after(0, lambda: self.results.insert("end", f"Migration: {result.message}\n"))
                self.log("Migration completed")
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"Migration failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()
