"""Migration management view."""

from __future__ import annotations

import threading

import customtkinter as ctk

from backend.models.schemas import DatabaseType, MigrationRequest
from backend.services.migration_service import MigrationService
from desktop.design_system import primary_button
from desktop.ops_widgets import page_header
from desktop.theme import ThemeManager, themed_scrollable_frame
from desktop.views.base_view import BaseView


class MigrationView(BaseView):
    """Schema and data migration operations."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = MigrationService()
        self._build_ui()

    def refresh(self, *, force: bool = False) -> None:
        """Re-sync migration form with the active connection."""
        del force
        active = self.get_active_db_type().value
        try:
            self.source_type.set(active)
            self.target_type.set(active)
        except Exception:
            pass
        self.status_label.configure(text="Ready")
        self.progress.set(0)
        self.log("Migration page refreshed")

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
            "Migration",
            subtitle="Move schema and data between database targets.",
            pady=(0, 12),
        )

        form = self.card_frame(scroll)
        form.pack(fill="x", pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)

        db_types = [t.value for t in DatabaseType]
        active_type = self.get_active_db_type().value
        label_kwargs = {
            "font": ctk.CTkFont(size=12),
            "text_color": self.theme.c("text_secondary"),
            "anchor": "w",
        }

        ctk.CTkLabel(form, text="Source Type", **label_kwargs).grid(
            row=0, column=0, padx=16, pady=10, sticky="w"
        )
        self.source_type = ctk.CTkComboBox(form, values=db_types)
        self.source_type.set(active_type)
        self.source_type.grid(row=0, column=1, padx=16, pady=10, sticky="ew")

        ctk.CTkLabel(form, text="Target Type", **label_kwargs).grid(
            row=1, column=0, padx=16, pady=10, sticky="w"
        )
        self.target_type = ctk.CTkComboBox(form, values=db_types)
        self.target_type.set(active_type)
        self.target_type.grid(row=1, column=1, padx=16, pady=10, sticky="ew")

        ctk.CTkLabel(form, text="Connection", **label_kwargs).grid(
            row=2, column=0, padx=16, pady=10, sticky="w"
        )
        ctk.CTkLabel(
            form,
            text=self.connection.name if self.connection else "default",
            font=ctk.CTkFont(size=13),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=2, column=1, padx=16, pady=10, sticky="w")

        self.schema_only_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            form,
            text="Schema only",
            variable=self.schema_only_var,
            text_color=self.theme.c("text_primary"),
        ).grid(row=3, column=0, columnspan=2, padx=16, pady=10, sticky="w")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=4, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="ew")
        primary_button(
            btn_frame,
            self.theme,
            text="Start Migration",
            command=self._run_migration,
            width=160,
            height=38,
        ).pack(side="left")

        progress_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        progress_frame.pack(fill="x", pady=(0, 8))

        self.progress = ctk.CTkProgressBar(progress_frame)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(0, 6))

        self.status_label = ctk.CTkLabel(
            progress_frame,
            text="Ready",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        )
        self.status_label.pack(fill="x")

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

        self.results = ctk.CTkTextbox(results_card, height=220, activate_scrollbars=True)
        self.results.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")

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
