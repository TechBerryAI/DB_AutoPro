"""Backup management view."""

from __future__ import annotations

import threading

import customtkinter as ctk

from backend.models.schemas import BackupMethod, BackupRequest, DatabaseType
from backend.services.backup_service import BackupService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class BackupView(BaseView):
    """Backup operations with progress tracking."""

    DB_TYPES = [t.value for t in DatabaseType]
    PG_METHODS = [m.value for m in BackupMethod if m.value.startswith("pg")]

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = BackupService()
        self._build_ui()

    def _build_ui(self) -> None:
        row = 0
        context = self.create_connection_context()
        if context:
            context.grid(row=row, column=0, padx=20, pady=(12, 8), sticky="ew")
            row += 1

        title = self.create_section_title("Backup Management")
        title.grid(row=row, column=0, padx=20, pady=20, sticky="w")
        row += 1

        form = self.card_frame(self)
        form.grid(row=row, column=0, padx=20, pady=10, sticky="nsew")
        row += 1

        active_type = self.get_active_db_type().value
        ctk.CTkLabel(form, text="Database Type:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.db_type = ctk.CTkComboBox(form, values=self.DB_TYPES, width=200)
        self.db_type.set(active_type)
        self.db_type.grid(row=0, column=1, padx=10, pady=8)
        if self.connection:
            self.db_type.configure(state="disabled")

        ctk.CTkLabel(form, text="Connection:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
        conn_label = self.connection.name if self.connection else "default"
        ctk.CTkLabel(form, text=conn_label).grid(row=1, column=1, padx=10, pady=8, sticky="w")

        ctk.CTkLabel(form, text="Method:").grid(row=2, column=0, padx=10, pady=8, sticky="w")
        self.method = ctk.CTkComboBox(form, values=self.PG_METHODS + ["mysqldump", "mongodump"], width=200)
        self.method.set("pg_dump")
        self.method.grid(row=2, column=1, padx=10, pady=8)

        ctk.CTkLabel(form, text="Database:").grid(row=3, column=0, padx=10, pady=8, sticky="w")
        self.database = ctk.CTkEntry(form, width=200, placeholder_text="Optional")
        if self.connection:
            self.database.insert(0, self.connection.database)
        self.database.grid(row=3, column=1, padx=10, pady=8)

        self.compress_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(form, text="Compress", variable=self.compress_var).grid(
            row=4, column=0, columnspan=2, padx=10, pady=8, sticky="w"
        )

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=row, column=0, padx=20, pady=10, sticky="w")
        row += 1

        ctk.CTkButton(btn_frame, text="Run Backup", command=self._run_backup, width=120).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Apply Retention", command=self._apply_retention, width=140).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="List Backups", command=self._list_backups, width=120).pack(side="left", padx=5)

        self.progress, self.status_label = self.create_progress_section()
        self.progress.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1
        self.status_label.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1

        self.results = ctk.CTkTextbox(self, height=200)
        self.results.grid(row=row, column=0, padx=20, pady=10, sticky="nsew")
        self.grid_rowconfigure(row, weight=1)

    def _update_progress(self, value: float, message: str) -> None:
        self.after(0, lambda: self.progress.set(value))
        self.after(0, lambda: self.status_label.configure(text=message))

    def _run_backup(self) -> None:
        def task():
            try:
                request = BackupRequest(
                    profile=self.get_active_profile(),
                    db_type=self.get_active_db_type(),
                    method=BackupMethod(self.method.get()),
                    database=self.database.get() or None,
                    compress=self.compress_var.get(),
                )
                self.log(f"Starting backup: {request.method.value}")
                result = self.service.create_backup(request, progress_callback=self._update_progress)
                self.after(0, lambda: self.results.insert("end", f"Success: {result.output_path}\n"))
                self.log(f"Backup completed: {result.output_path}")
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"Backup failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()

    def _apply_retention(self) -> None:
        deleted = self.service.apply_retention_policy()
        self.results.insert("end", f"Retention: deleted {deleted} old backups\n")
        self.log(f"Retention applied: {deleted} deleted")

    def _list_backups(self) -> None:
        backups = self.service.list_backups()
        self.results.delete("1.0", "end")
        for b in backups:
            self.results.insert("end", f"{b['name']} ({b['size_bytes']} bytes)\n")
