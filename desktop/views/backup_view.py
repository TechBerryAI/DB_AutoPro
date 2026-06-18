"""Backup management view."""

from __future__ import annotations

import threading

import customtkinter as ctk

from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    BackupRequest,
    DatabaseType,
    backup_methods_for,
)
from backend.services.backup_catalog import (
    category_description,
    describe_backup_command,
    method_label,
)
from backend.services.backup_service import BackupService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class BackupView(BaseView):
    """Backup operations with logical / physical segregation."""

    DB_TYPES = [t.value for t in DatabaseType]

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = BackupService()
        self._category = BackupCategory.LOGICAL
        self._build_ui()
        self._refresh_methods()

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
        form.grid(row=row, column=0, padx=20, pady=10, sticky="ew")
        row += 1

        active_type = self.get_active_db_type()
        ctk.CTkLabel(form, text="Database Type:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.db_type = ctk.CTkComboBox(form, values=self.DB_TYPES, width=200)
        self.db_type.set(active_type.value)
        self.db_type.grid(row=0, column=1, padx=10, pady=8)
        if self.connection:
            self.db_type.configure(state="disabled")

        ctk.CTkLabel(form, text="Connection:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
        conn_label = self.connection.name if self.connection else "default"
        ctk.CTkLabel(form, text=conn_label).grid(row=1, column=1, padx=10, pady=8, sticky="w")

        ctk.CTkLabel(form, text="Backup Type:").grid(row=2, column=0, padx=10, pady=8, sticky="nw")
        type_frame = ctk.CTkFrame(form, fg_color="transparent")
        type_frame.grid(row=2, column=1, padx=10, pady=8, sticky="w")

        self.category_seg = ctk.CTkSegmentedButton(
            type_frame,
            values=["Logical", "Physical"],
            command=self._on_category_changed,
            width=220,
        )
        self.category_seg.set("Logical")
        self.category_seg.pack(anchor="w")

        self.category_hint = ctk.CTkLabel(
            type_frame,
            text=category_description(BackupCategory.LOGICAL),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.get("text_muted"),
            wraplength=360,
            justify="left",
        )
        self.category_hint.pack(anchor="w", pady=(6, 0))

        ctk.CTkLabel(form, text="Command:").grid(row=3, column=0, padx=10, pady=8, sticky="nw")
        self.method = ctk.CTkComboBox(
            form, values=["pg_dump"], width=320, command=self._on_method_changed
        )
        self.method.set("pg_dump")
        self.method.grid(row=3, column=1, padx=10, pady=8, sticky="w")

        ctk.CTkLabel(form, text="Database:").grid(row=4, column=0, padx=10, pady=8, sticky="w")
        self.database = ctk.CTkEntry(form, width=200, placeholder_text="Optional")
        if self.connection:
            self.database.insert(0, self.connection.database)
        self.database.grid(row=4, column=1, padx=10, pady=8)

        self.compress_var = ctk.BooleanVar(value=True)
        self.compress_cb = ctk.CTkCheckBox(
            form, text="Compress output (logical backups)", variable=self.compress_var,
            command=self._update_command_preview,
        )
        self.compress_cb.grid(row=5, column=0, columnspan=2, padx=10, pady=8, sticky="w")

        ctk.CTkLabel(form, text="Preview:").grid(row=6, column=0, padx=10, pady=8, sticky="nw")
        self.command_preview = ctk.CTkTextbox(form, height=56, width=420, activate_scrollbars=False)
        self.command_preview.grid(row=6, column=1, padx=10, pady=8, sticky="ew")
        self.command_preview.configure(state="disabled")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=row, column=0, padx=20, pady=10, sticky="w")
        row += 1

        ctk.CTkButton(btn_frame, text="Run Backup", command=self._run_backup, width=120).pack(
            side="left", padx=5
        )
        ctk.CTkButton(btn_frame, text="Apply Retention", command=self._apply_retention, width=140).pack(
            side="left", padx=5
        )
        ctk.CTkButton(btn_frame, text="List Backups", command=self._list_backups, width=120).pack(
            side="left", padx=5
        )

        self.progress, self.status_label = self.create_progress_section()
        self.progress.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1
        self.status_label.grid(row=row, column=0, padx=20, pady=5, sticky="w")
        row += 1

        self.results = ctk.CTkTextbox(self, height=200)
        self.results.grid(row=row, column=0, padx=20, pady=10, sticky="nsew")
        self.grid_rowconfigure(row, weight=1)

    def _active_db_type(self) -> DatabaseType:
        if self.connection:
            return self.get_active_db_type()
        return DatabaseType(self.db_type.get())

    def _on_category_changed(self, value: str) -> None:
        self._category = BackupCategory.PHYSICAL if value == "Physical" else BackupCategory.LOGICAL
        self.category_hint.configure(text=category_description(self._category))
        self._refresh_methods()

    def _on_method_changed(self, _value: str | None = None) -> None:
        self._sync_form_state()
        self._update_command_preview()

    def _refresh_methods(self) -> None:
        db_type = self._active_db_type()
        methods = backup_methods_for(db_type, self._category)
        labels = [method_label(m) for m in methods] if methods else ["(not available)"]

        self.method.configure(values=labels if methods else ["(not available for this type)"])
        if methods:
            self.method.set(labels[0])
        else:
            self.method.set("(not available for this type)")
        self._sync_form_state()
        self._update_command_preview()

    def _sync_form_state(self) -> None:
        method_value = self._selected_method_value()
        physical = self._category == BackupCategory.PHYSICAL
        cluster_wide = method_value in (BackupMethod.PG_DUMPALL.value, BackupMethod.PG_BASEBACKUP.value)

        if cluster_wide or physical:
            self.database.configure(state="disabled")
        else:
            self.database.configure(state="normal")

        if physical:
            self.compress_var.set(False)
            self.compress_cb.configure(state="disabled")
        else:
            self.compress_cb.configure(state="normal")

    def _selected_method_value(self) -> str:
        label = self.method.get()
        for method in BackupMethod:
            if method_label(method) == label or method.value == label:
                return method.value
        return label

    def _build_request(self) -> BackupRequest:
        method_value = self._selected_method_value()
        if method_value not in {m.value for m in BackupMethod}:
            raise ValueError(f"No backup command available for {self._category.value} backups")

        return BackupRequest(
            profile=self.get_active_profile(),
            db_type=self._active_db_type(),
            method=BackupMethod(method_value),
            database=self.database.get() or None,
            compress=self.compress_var.get(),
        )

    def _update_command_preview(self) -> None:
        try:
            request = self._build_request()
            preview = describe_backup_command(request)
            if request.compress and self._category == BackupCategory.LOGICAL:
                preview += "  →  gzip compression after export"
        except ValueError as exc:
            preview = str(exc)

        self.command_preview.configure(state="normal")
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("1.0", preview)
        self.command_preview.configure(state="disabled")

    def _update_progress(self, value: float, message: str) -> None:
        self.after(0, lambda: self.progress.set(value))
        self.after(0, lambda: self.status_label.configure(text=message))

    def _run_backup(self) -> None:
        def task():
            try:
                request = self._build_request()
                category = self._category.value
                self.log(f"Starting {category} backup: {request.method.value}")
                result = self.service.create_backup(request, progress_callback=self._update_progress)
                self.after(
                    0,
                    lambda: self.results.insert(
                        "end", f"[{category}] Success: {result.output_path}\n"
                    ),
                )
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
