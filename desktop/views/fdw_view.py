"""FDW management view."""

from __future__ import annotations

import threading

import customtkinter as ctk

from backend.models.schemas import FDWRequest
from backend.services.fdw_service import FDWService
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class FDWView(BaseView):
    """PostgreSQL Foreign Data Wrapper management."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = FDWService()
        self._build_ui()

    def _build_ui(self) -> None:
        title = self.create_section_title("FDW Management")
        title.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        form = ctk.CTkFrame(self, corner_radius=10)
        form.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")

        fields = [
            ("server_name", "Server Name", "foreign_server"),
            ("foreign_host", "Foreign Host", "localhost"),
            ("foreign_port", "Foreign Port", "5432"),
            ("foreign_db", "Foreign DB", "postgres"),
            ("foreign_user", "Foreign User", "postgres"),
            ("foreign_password", "Foreign Password", ""),
            ("local_user", "Local User", "postgres"),
            ("schema_name", "Schema (optional)", ""),
        ]

        self.entries: dict[str, ctk.CTkEntry] = {}
        for i, (key, label, default) in enumerate(fields):
            ctk.CTkLabel(form, text=f"{label}:").grid(row=i, column=0, padx=10, pady=5, sticky="w")
            entry = ctk.CTkEntry(form, width=250)
            if default:
                entry.insert(0, default)
            entry.grid(row=i, column=1, padx=10, pady=5)
            self.entries[key] = entry

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=20, pady=10, sticky="w")
        ctk.CTkButton(btn_frame, text="Setup FDW", command=self._setup, width=120).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Validate", command=self._validate, width=120).pack(side="left", padx=5)

        self.results = ctk.CTkTextbox(self, height=200)
        self.results.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")

    def _build_request(self) -> FDWRequest:
        schema = self.entries["schema_name"].get()
        return FDWRequest(
            server_name=self.entries["server_name"].get(),
            foreign_host=self.entries["foreign_host"].get(),
            foreign_port=int(self.entries["foreign_port"].get() or 5432),
            foreign_db=self.entries["foreign_db"].get(),
            foreign_user=self.entries["foreign_user"].get(),
            foreign_password=self.entries["foreign_password"].get(),
            local_user=self.entries["local_user"].get(),
            schema_name=schema if schema else None,
        )

    def _setup(self) -> None:
        def task():
            try:
                result = self.service.setup_complete_fdw(self._build_request())
                self.after(0, lambda: self.results.insert("end", f"FDW setup complete: {result}\n"))
                self.log("FDW setup completed")
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"FDW setup failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()

    def _validate(self) -> None:
        def task():
            try:
                result = self.service.validate_fdw(self._build_request())
                self.after(0, lambda: self.results.insert("end", f"Validation: {result}\n"))
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Validation failed: {exc}\n"))

        threading.Thread(target=task, daemon=True).start()
