"""PostgreSQL administration patch page. SQL patches run only after review on a superuser primary."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from backend.connectors.base import get_connector
from backend.models.schemas import DatabaseType
from backend.services.patch_service import PatchService
from backend.services.postgres_patch.admin_check import assess_administrator, format_report
from backend.services.postgres_patch.identity import DatabaseIdentityError, read_database_identity
from backend.services.postgres_patch.sql_patch import format_patch_review, inspect_sql_patch
from backend.services.postgres_patch.workflow import (
    format_history_lines,
    format_operator_review,
    run_reviewed_patch,
)
from desktop.design_system import primary_button, secondary_button
from desktop.ops_widgets import page_header
from desktop.postgres_patch_flow import execution_block_reason
from desktop.theme import ThemeManager, themed_scrollable_frame
from desktop.views.base_view import BaseView


class PostgresPatchView(BaseView):
    """Administration checks, review, and SQL patch execution for a connected PostgreSQL server."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = PatchService()
        self._report = None
        self._identity = None
        self._file_review = None
        self._review_shown = False
        self._suppress_option = False
        self._busy = False
        self._build()

    def refresh(self, *, force: bool = False) -> None:
        del force
        self._show_connection()
        self._show_history()
        self.log("PostgreSQL administration patch refreshed")

    def on_connection_changed(self) -> None:
        self._report = None
        self._identity = None
        self._clear_review()
        self._show_connection()
        self._show_history()
        self._sync_buttons()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        scroll = themed_scrollable_frame(self, self.theme)
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)
        page_header(
            scroll,
            self.theme,
            "Patching",
            subtitle="Run administrator checks, review a SQL patch, then execute it. This page does not install packages.",
            pady=(0, 12),
        )

        self._section_title(scroll, "Connection Overview")
        form = self.card_frame(scroll)
        form.pack(fill="x", pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)
        self._label(form, "Connection").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        self.connection_label = ctk.CTkLabel(
            form,
            text="",
            font=ctk.CTkFont(size=13),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        )
        self.connection_label.grid(row=0, column=1, padx=16, pady=10, sticky="ew")

        self._section_title(scroll, "Administration Checks")
        checks = self.card_frame(scroll)
        checks.pack(fill="x", pady=(0, 8))
        self.check_button = primary_button(
            checks, self.theme, text="Run Administration Checks", command=self._start_checks
        )
        self.check_button.pack(anchor="w", padx=16, pady=16)
        self.results = self._textbox(scroll, "Run administration checks after PostgreSQL is connected.")

        self._section_title(scroll, "SQL Patch")
        patch = self.card_frame(scroll)
        patch.pack(fill="x", pady=(12, 8))
        patch.grid_columnconfigure(1, weight=1)
        self._label(patch, "SQL file").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        path_row = ctk.CTkFrame(patch, fg_color="transparent")
        path_row.grid(row=0, column=1, padx=16, pady=10, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)
        self.sql_file = ctk.CTkEntry(path_row, placeholder_text="Path to a .sql patch file")
        self.sql_file.grid(row=0, column=0, sticky="ew")
        self.sql_file.bind("<KeyRelease>", self._on_sql_changed)
        secondary_button(path_row, self.theme, text="Browse", command=self._browse, width=90).grid(
            row=0, column=1, padx=(8, 0)
        )
        self._label(patch, "Description").grid(row=1, column=0, padx=16, pady=10, sticky="w")
        self.description = ctk.CTkEntry(patch, placeholder_text="Optional note for patch history")
        self.description.grid(row=1, column=1, padx=16, pady=10, sticky="ew")
        self.description.bind("<KeyRelease>", self._on_sql_changed)
        self.validate_button = secondary_button(
            patch, self.theme, text="Validate SQL File", command=self._start_validate
        )
        self.validate_button.grid(row=2, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="w")
        self.repeat_var = ctk.BooleanVar(value=False)
        self.repeat_box = ctk.CTkCheckBox(
            patch,
            text="Run this completed patch again",
            variable=self.repeat_var,
            command=self._on_execution_option,
            text_color=self.theme.c("text_primary"),
        )
        self.repeat_box.grid(row=3, column=0, columnspan=2, padx=16, pady=(0, 4), sticky="w")
        self.nontx_var = ctk.BooleanVar(value=False)
        self.nontx_box = ctk.CTkCheckBox(
            patch,
            text="This script cannot be rolled back automatically. Run it anyway.",
            variable=self.nontx_var,
            command=self._on_execution_option,
            text_color=self.theme.c("text_primary"),
        )
        self.nontx_box.grid(row=4, column=0, columnspan=2, padx=16, pady=(0, 4), sticky="w")
        self.reconcile_var = ctk.BooleanVar(value=False)
        self.reconcile_box = ctk.CTkCheckBox(
            patch,
            text="I reconciled the interrupted run. Do not start it automatically.",
            variable=self.reconcile_var,
            command=self._on_execution_option,
            text_color=self.theme.c("text_primary"),
        )
        self.reconcile_box.grid(row=5, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="w")
        self.patch_preview = self._textbox(scroll, "Validate a SQL file to see its name, checksum, and preview.")

        self._section_title(scroll, "Review")
        review = self.card_frame(scroll)
        review.pack(fill="x", pady=(12, 8))
        self.review_button = secondary_button(review, self.theme, text="Open Review", command=self._open_review)
        self.review_button.pack(anchor="w", padx=16, pady=(16, 8))
        self.confirm_var = ctk.BooleanVar(value=False)
        self.confirm_box = ctk.CTkCheckBox(
            review,
            text="I am the signed-in operator and I confirm this SQL patch. A separate approver is not recorded.",
            variable=self.confirm_var,
            command=self._sync_buttons,
            text_color=self.theme.c("text_primary"),
        )
        self.confirm_box.pack(anchor="w", padx=16, pady=(0, 16))
        self.review_box = self._textbox(scroll, "Open the review after the SQL file is valid.")

        self._section_title(scroll, "Execution and Verification")
        execution = self.card_frame(scroll)
        execution.pack(fill="x", pady=(12, 8))
        self.execute_button = primary_button(execution, self.theme, text="Execute Patch", command=self._start_execute)
        self.execute_button.pack(anchor="w", padx=16, pady=(16, 8))
        self.reason_label = ctk.CTkLabel(
            execution,
            text="",
            justify="left",
            anchor="w",
            text_color=self.theme.c("text_secondary"),
        )
        self.reason_label.pack(fill="x", padx=16, pady=(0, 16))
        self.execution_box = self._textbox(scroll, "Execution status and verification status appear here.")

        self._section_title(scroll, "Patch History")
        history_actions = ctk.CTkFrame(scroll, fg_color="transparent")
        history_actions.pack(fill="x", pady=(0, 8))
        secondary_button(history_actions, self.theme, text="Refresh History", command=self._show_history).pack(
            side="left"
        )
        self.history_box = self._textbox(scroll, "Recorded PostgreSQL administration patches appear here.")
        self._show_connection()
        self._show_history()
        self._sync_buttons()

    def _section_title(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(fill="x", pady=(8, 6))

    def _label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        )

    def _textbox(self, parent, placeholder: str) -> ctk.CTkTextbox:
        box = ctk.CTkTextbox(parent, height=160, activate_scrollbars=True)
        box.pack(fill="x", pady=(0, 8))
        box.insert("end", placeholder)
        box.configure(state="disabled")
        return box

    def _write(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")

    def _show_connection(self) -> None:
        if not hasattr(self, "connection_label"):
            return
        text = self.connection.summary if self.connection else "PostgreSQL is not connected"
        self.connection_label.configure(text=text)

    def _operator(self) -> str:
        if self.owner_username:
            return self.owner_username
        if self.connection and self.connection.username:
            return self.connection.username
        return ""

    def _clear_review(self) -> None:
        self._file_review = None
        self._review_shown = False
        if hasattr(self, "confirm_var"):
            self.confirm_var.set(False)

    def _on_sql_changed(self, _event=None) -> None:
        self._suppress_option = True
        if hasattr(self, "repeat_var"):
            self.repeat_var.set(False)
            self.nontx_var.set(False)
            self.reconcile_var.set(False)
        self._suppress_option = False
        self._clear_review()
        self._sync_buttons()

    def _on_execution_option(self) -> None:
        if self._suppress_option or self._busy or not (self._report and self._report.ready_to_patch):
            return
        if not self.sql_file.get().strip():
            return
        self._start_validate()

    def _sync_buttons(self) -> None:
        if not hasattr(self, "check_button"):
            return
        connected = _postgres_connected(self.connection)
        checks_ready = bool(self._report and self._report.ready_to_patch)
        file_validated = bool(self._file_review and self._file_review.valid)
        duplicate = bool(self._file_review and self._file_review.duplicate_of and not self._file_review.allow_repeat)
        non_tx = bool(
            self._file_review and self._file_review.needs_non_transactional and not self._file_review.allow_non_transactional
        )
        interrupted = bool(self._file_review and self._file_review.interrupted and not self._file_review.reconcile_interrupted)
        path = self.sql_file.get().strip() if hasattr(self, "sql_file") else ""
        reason = execution_block_reason(
            connected=connected,
            checks_ready=checks_ready,
            file_validated=file_validated,
            duplicate=duplicate,
            non_transactional_unconfirmed=non_tx,
            interrupted_unreconciled=interrupted,
            review_shown=self._review_shown,
            confirmed=bool(self.confirm_var.get()) if hasattr(self, "confirm_var") else False,
            operator_known=bool(self._operator()),
            busy=self._busy,
        )
        self.check_button.configure(state="disabled" if self._busy or not connected else "normal")
        self.validate_button.configure(
            state="normal" if connected and checks_ready and path and not self._busy else "disabled"
        )
        self.review_button.configure(state="normal" if file_validated and not duplicate and not self._busy else "disabled")
        self.confirm_box.configure(state="normal" if self._review_shown and not self._busy else "disabled")
        if hasattr(self, "repeat_box"):
            self.repeat_box.configure(
                state="normal" if self._file_review and self._file_review.duplicate_of and not self._busy else "disabled"
            )
            self.nontx_box.configure(
                state="normal"
                if self._file_review and self._file_review.needs_non_transactional and not self._busy
                else "disabled"
            )
            self.reconcile_box.configure(
                state="normal" if self._file_review and self._file_review.interrupted and not self._busy else "disabled"
            )
        self.execute_button.configure(state="normal" if reason is None else "disabled")
        self.reason_label.configure(text=reason or "Execution is available for this reviewed patch.")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("SQL files", "*.sql"), ("All", "*.*")])
        if path:
            self.sql_file.delete(0, "end")
            self.sql_file.insert(0, path)
            self._on_sql_changed()

    def _start_checks(self) -> None:
        if self._busy or not _postgres_connected(self.connection):
            return
        self._busy = True
        self._clear_review()
        self._sync_buttons()
        self._write(self.results, "Running administration checks...")

        def task() -> None:
            identity = None
            try:
                report = assess_administrator(_live_query, backup_count=_local_backup_count())
                text = format_report(report)
                try:
                    identity = read_database_identity(_live_query)
                    text = f"{text}\n{identity.summary()}"
                except DatabaseIdentityError as exc:
                    identity = None
                    text = f"{text}\n{exc}"
            except Exception as exc:
                report = None
                text = f"Administration checks failed: {exc}"
            self.after(0, lambda: self._finish_checks(report, identity, text))

        threading.Thread(target=task, daemon=True).start()

    def _finish_checks(self, report, identity, text: str) -> None:
        self._report = report
        self._identity = identity
        self._busy = False
        self._write(self.results, text)
        self._sync_buttons()
        self.log("PostgreSQL administration checks finished")

    def _start_validate(self) -> None:
        if self._busy or not (self._report and self._report.ready_to_patch and self._identity):
            return
        path = self.sql_file.get().strip()
        description = self.description.get().strip()
        identity = self._identity
        endpoint = self.connection.endpoint_label if self.connection else ""
        self._busy = True
        self._clear_review()
        self._sync_buttons()

        def task() -> None:
            review = inspect_sql_patch(
                path,
                description=description,
                target_database=identity.database_name,
                system_identifier=identity.system_identifier,
                database_oid=identity.database_oid,
                configured_endpoint=endpoint,
                completed_identities=self.service.completed_patch_identities(),
                running_identities=self.service.running_patch_identities(),
                legacy_running=self.service.legacy_running_patches(),
                allow_repeat=bool(self.repeat_var.get()),
                allow_non_transactional=bool(self.nontx_var.get()),
                reconcile_interrupted=bool(self.reconcile_var.get()),
            )
            self.after(0, lambda: self._finish_validate(review))

        threading.Thread(target=task, daemon=True).start()

    def _finish_validate(self, review) -> None:
        self._file_review = review
        self._busy = False
        self._write(self.patch_preview, format_patch_review(review))
        self._sync_buttons()

    def _open_review(self) -> None:
        if not (self._report and self._file_review and self._file_review.valid):
            return
        self._review_shown = True
        self.confirm_var.set(False)
        self._write(self.review_box, format_operator_review(self._report, self._file_review))
        self._sync_buttons()

    def _start_execute(self) -> None:
        if self._busy or execution_block_reason(
            connected=_postgres_connected(self.connection),
            checks_ready=bool(self._report and self._report.ready_to_patch),
            file_validated=bool(self._file_review and self._file_review.valid),
            duplicate=bool(self._file_review and self._file_review.duplicate_of and not self._file_review.allow_repeat),
            non_transactional_unconfirmed=bool(
                self._file_review
                and self._file_review.needs_non_transactional
                and not self._file_review.allow_non_transactional
            ),
            interrupted_unreconciled=bool(
                self._file_review and self._file_review.interrupted and not self._file_review.reconcile_interrupted
            ),
            review_shown=self._review_shown,
            confirmed=bool(self.confirm_var.get()),
            operator_known=bool(self._operator()),
            busy=False,
        ):
            return
        self._busy = True
        self._sync_buttons()
        self._write(self.execution_box, "Starting the SQL patch.")
        report = self._report
        review = self._file_review
        operator = self._operator()
        target_host = self.connection.endpoint_label if self.connection else ""
        profile = self.get_active_profile()

        def on_progress(index: int, total: int) -> None:
            self.after(0, lambda index=index, total=total: self._write(self.execution_box, f"Running statement {index} of {total}."))

        def task() -> None:
            result = run_reviewed_patch(
                report=report,
                review=review,
                confirmed=True,
                operator=operator,
                service=self.service,
                target_host=target_host,
                verify_query=_live_query,
                profile=profile,
                on_progress=on_progress,
            )
            self.after(0, lambda: self._finish_execute(result.summary))

        threading.Thread(target=task, daemon=True).start()

    def _finish_execute(self, text: str) -> None:
        self._busy = False
        self._review_shown = False
        if hasattr(self, "confirm_var"):
            self.confirm_var.set(False)
        self._write(self.execution_box, text)
        self._show_history()
        self._sync_buttons()
        self.log("PostgreSQL SQL patch finished")

    def _show_history(self) -> None:
        if not hasattr(self, "history_box"):
            return
        database = ""
        if self._report and self._report.database_name:
            database = self._report.database_name.lower()
        elif self.connection and self.connection.database:
            database = self.connection.database.lower()
        entries = [
            entry
            for entry in self.service.get_history()
            if database and (entry.target_database or "").lower() == database
        ]
        self._write(self.history_box, format_history_lines(entries))


def _postgres_connected(connection) -> bool:
    if connection is None:
        return False
    db_type = getattr(connection, "db_type", None)
    value = getattr(db_type, "value", db_type)
    return str(value or "").strip().lower() == "postgresql"


def _live_query(sql: str) -> list[dict]:
    connector = get_connector(DatabaseType.POSTGRESQL, "default")
    return connector.execute_query(sql)


def _local_backup_count() -> int:
    """Count local backup names without reading file contents. -1 means the catalog could not be read."""
    try:
        from backend.services.backup_service import BackupService

        directory = BackupService().output_dir
        if not directory.exists():
            return 0
        return sum(1 for item in directory.iterdir() if not item.name.startswith("."))
    except Exception:
        return -1
