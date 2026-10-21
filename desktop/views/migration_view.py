"""Migration management view."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path

import customtkinter as ctk

from backend.connectors.base import get_connector_from_info
from backend.connectors.mysql import MySQLConnector
from backend.connectors.postgresql import PostgreSQLConnector
from backend.models.schemas import DatabaseType
from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.backup_proof import DISPOSABLE_RESTORE_DATABASE, inspect_backup_file, verify_postgresql_restore
from backend.services.pg_migration.precheck import (
    SCOPE_DATA,
    SCOPE_SCHEMA,
    SCOPE_SCHEMA_AND_DATA,
    STRATEGY_FAIL,
    STRATEGY_REPLACE,
    STRATEGY_SKIP,
    PUBLIC_TABLES_SQL,
    assess_migration,
    quote_ident,
)
from backend.services.pg_migration.engine import stream_copy_table
from backend.services.pg_migration.workflow import build_plan, start_migration
from backend.services.pg_mysql_migration.engine import MysqlTargetSession
from backend.services.pg_mysql_migration.precheck import assess_pg_mysql
from backend.services.pg_mysql_migration.schema import source_count_sql
from backend.services.pg_mysql_migration.workflow import build_pg_mysql_plan, start_pg_mysql_migration
from desktop.connection_store import load_saved_servers
from desktop.design_system import card, primary_button, secondary_button
from desktop.ops_widgets import page_header
from backend.services.migration_framework.destination import (
    create_mysql_destination,
    create_postgresql_destination,
    destination_copy_name,
    destination_identity_error,
    destination_name_error,
    mysql_destination_exists,
    server_probe_database,
)
from backend.services.migration_framework.discovery import list_accessible_databases
from backend.services.migration_framework.registry import target_types_for
from desktop.pg_migration_flow import (
    BACKUP_ACK_TEXT,
    CONNECTION_METHODS,
    METHOD_SAVED,
    METHOD_TYPED,
    MYSQL,
    PLAN_CONFIRM_TEXT,
    POSTGRESQL,
    REPLACE_CONFIRM_TEXT,
    SOURCE_TYPES,
    TARGET_TYPES,
    MigrationFormState,
    default_listen_port,
    manual_endpoint,
    secret_entry_show,
    typed_endpoint_error,
    describe_server_endpoint,
    describe_verified_target,
    find_saved_server,
    live_session_matches_server,
    migration_connection_result,
    open_migration_endpoint,
    outcome_counts,
    pair_summary,
    saved_server_choices,
    server_choice_label,
    parse_batch_size,
    parse_timeout_ms,
    replace_requires_confirmation,
    same_instance_reason,
    servers_for_type,
    start_block_reason,
    unsupported_migration_reason,
)
from desktop.theme import ThemeManager, themed_scrollable_frame
from desktop.views.base_view import BaseView

_MysqlSession = MysqlTargetSession

_SCOPES = {
    "Schema and data": SCOPE_SCHEMA_AND_DATA,
    "Schema only": SCOPE_SCHEMA,
    "Data only": SCOPE_DATA,
}
_STRATEGIES = {
    "Skip existing tables": STRATEGY_SKIP,
    "Stop if the table exists": STRATEGY_FAIL,
    "Replace existing tables": STRATEGY_REPLACE,
}


class MigrationView(BaseView):
    """Schema and data migration between independently selected databases."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self._servers: list = []
        self._plan = None
        self._validated_key = None
        self._source_test = None
        self._target_test = None
        self._backup = inspect_backup_file(None)
        self._busy = False
        self._cancel = threading.Event()
        self._started = 0.0
        self._done = 0
        self._total = 0
        self._operation_id = ""
        self._counts = outcome_counts([])
        self._syncing = False
        self._dest_manual = False
        self._ready = False
        try:
            self._build_ui()
            self._ready = True
            self._load_servers()
            self._show_endpoint_fields("source")
            self._show_endpoint_fields("target")
            self._sync_replace_row()
        except Exception as exc:
            self._show_build_error(exc)

    def refresh(self, *, force: bool = False) -> None:
        del force
        self._load_servers()
        self._reset_workflow()
        self._set_progress(0)
        self.status_label.configure(text="Ready")
        self.operation_label.configure(text="Operation ID: none")
        self.counts_label.configure(text=self._count_text())
        self.log("Migration page refreshed")

    def on_connection_changed(self) -> None:
        self._load_servers()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        scroll = themed_scrollable_frame(self, self.theme)
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)
        page_header(
            scroll,
            self.theme,
            "Database Migration",
            subtitle="Safely migrate database schema and data between supported database systems.",
            pady=(0, 12),
        )

        pair = ctk.CTkFrame(scroll, fg_color="transparent")
        pair.pack(fill="x", pady=(0, 10))
        pair.grid_columnconfigure(0, weight=1)
        pair.grid_columnconfigure(1, weight=1)
        self._build_source_card(pair).grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_target_card(pair).grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._build_options_card(scroll)
        self._build_safety_card(scroll)
        self._build_workflow_card(scroll)
        self._build_progress_card(scroll)
        self._build_results_card(scroll)
        self._build_backup_card(scroll)

    def _label_style(self) -> dict:
        return {
            "font": ctk.CTkFont(size=12),
            "text_color": self.theme.c("text_secondary"),
            "anchor": "w",
        }

    def _help(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
            justify="left",
            wraplength=720,
        )

    def _combo(self, parent, values, selected, command):
        box = ctk.CTkComboBox(parent, values=values, command=command)
        box.set(selected)
        return box

    def _bind_entry(self, entry, command) -> None:
        entry.bind("<KeyRelease>", lambda _e: command())
        entry.bind("<FocusOut>", lambda _e: command())

    def _row(self, body, row: int, text: str, widget) -> None:
        ctk.CTkLabel(body, text=text, **self._label_style()).grid(row=row, column=0, padx=(0, 12), pady=6, sticky="w")
        widget.grid(row=row, column=1, pady=6, sticky="ew")

    def _saved_row(self, parent, row: int, widget):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        self._row(frame, 0, "Saved server", widget)
        return frame

    def _fields(self, body) -> ctk.CTkFrame:
        fields = ctk.CTkFrame(body, fg_color="transparent")
        fields.pack(fill="x")
        fields.grid_columnconfigure(1, weight=1)
        return fields

    def _plain_entry(self, parent, *, placeholder: str = "", value: str = "") -> ctk.CTkEntry:
        entry = ctk.CTkEntry(parent, show=secret_entry_show(True), placeholder_text=placeholder)
        if value:
            entry.insert(0, value)
        return entry

    def _secret_field(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(wrap, show=secret_entry_show(False))
        entry.grid(row=0, column=0, sticky="ew")
        visible = ctk.BooleanVar(value=False)

        def toggle() -> None:
            visible.set(not visible.get())
            entry.configure(show=secret_entry_show(visible.get()))
            button.configure(text="Hide" if visible.get() else "Show")

        button = secondary_button(wrap, self.theme, text="Show", command=toggle, width=70)
        button.grid(row=0, column=1, padx=(8, 0))
        return wrap, entry

    def _build_typed_fields(self, parent, side: str):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(1, weight=1)
        host = self._plain_entry(frame, placeholder="192.168.1.10")
        port = self._plain_entry(frame, placeholder="5432", value=str(default_listen_port(self._side_type(side) if hasattr(self, "source_type") else POSTGRESQL)))
        username = self._plain_entry(frame, placeholder="Username")
        password_row, password = self._secret_field(frame)
        ssh = ctk.BooleanVar(value=False)
        linux_username = self._plain_entry(frame, placeholder="Linux username")
        linux_password_row, linux_password = self._secret_field(frame)
        ssh_host = self._plain_entry(frame, placeholder="Defaults to the database host")
        self._row(frame, 0, "Host", host)
        self._row(frame, 1, "Port", port)
        self._row(frame, 2, "Username", username)
        self._row(frame, 3, "Password", password_row)
        ssh_box = ctk.CTkCheckBox(
            frame,
            text="Connect through SSH",
            variable=ssh,
            command=lambda: self._on_ssh_toggle(side),
            text_color=self.theme.c("text_primary"),
        )
        ssh_box.grid(row=4, column=0, columnspan=2, pady=6, sticky="w")
        extras = ctk.CTkFrame(frame, fg_color="transparent")
        extras.grid_columnconfigure(1, weight=1)
        extras.grid(row=5, column=0, columnspan=2, sticky="ew")
        self._row(extras, 0, "Linux username", linux_username)
        self._row(extras, 1, "Linux password", linux_password_row)
        self._row(extras, 2, "SSH host", ssh_host)
        for widget in (host, port, username, password, linux_username, linux_password, ssh_host):
            self._bind_entry(widget, self._on_form_changed)
        setattr(self, f"{side}_host", host)
        setattr(self, f"{side}_port", port)
        setattr(self, f"{side}_username", username)
        setattr(self, f"{side}_password", password)
        setattr(self, f"{side}_ssh", ssh)
        setattr(self, f"{side}_linux_username", linux_username)
        setattr(self, f"{side}_linux_password", linux_password)
        setattr(self, f"{side}_ssh_host", ssh_host)
        setattr(self, f"{side}_ssh_extras", extras)
        setattr(self, f"{side}_typed", frame)
        return frame

    def _build_source_card(self, parent):
        shell, body = card(parent, self.theme, title="Source database")
        fields = self._fields(body)
        self.source_type = self._combo(fields, list(SOURCE_TYPES), POSTGRESQL, self._on_source_type)
        self.source_method = self._combo(fields, list(CONNECTION_METHODS), METHOD_SAVED, lambda _v: self._on_method("source"))
        self.source_server = self._combo(fields, ["(none)"], "(none)", self._on_source_server)
        self.source_database = ctk.CTkEntry(fields)
        self.source_db_list = self._combo(
            fields,
            ["Test Source to list databases"],
            "Test Source to list databases",
            self._on_source_db_pick,
        )
        self.tables = ctk.CTkEntry(fields, placeholder_text="Optional public table names, separated by commas")
        self._row(fields, 0, "Database type", self.source_type)
        self._row(fields, 1, "Connection", self.source_method)
        self.source_saved_row = self._saved_row(fields, 2, self.source_server)
        self.source_typed = self._build_typed_fields(fields, "source")
        self.source_typed.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._row(fields, 4, "Database name", self.source_database)
        self._row(fields, 5, "Accessible databases", self.source_db_list)
        self._row(fields, 6, "Tables", self.tables)
        self.source_details = self._help(body, describe_server_endpoint(None))
        self.source_details.pack(fill="x", pady=(2, 0))
        self.source_status = self._help(body, "Connection has not been tested for the current source.")
        self.source_status.pack(fill="x", pady=(2, 8))
        secondary_button(body, self.theme, text="Test Source", command=lambda: self._test_side("source"), width=130).pack(anchor="w")
        self._bind_entry(self.source_database, self._on_form_changed)
        self._bind_entry(self.tables, self._on_form_changed)
        return shell

    def _build_target_card(self, parent):
        shell, body = card(parent, self.theme, title="Target database")
        fields = self._fields(body)
        self.target_type = self._combo(fields, list(TARGET_TYPES), POSTGRESQL, self._on_target_type)
        self.target_method = self._combo(fields, list(CONNECTION_METHODS), METHOD_TYPED, lambda _v: self._on_method("target"))
        self.target_server = self._combo(fields, ["(none)"], "(none)", self._on_target_server)
        self.target_database = ctk.CTkEntry(fields)
        self.target_db_list = self._combo(
            fields,
            ["Test Target to list databases"],
            "Test Target to list databases",
            self._on_target_db_pick,
        )
        self.strategy = self._combo(fields, list(_STRATEGIES), "Skip existing tables", self._on_strategy)
        self._row(fields, 0, "Database type", self.target_type)
        self._row(fields, 1, "Connection", self.target_method)
        self.target_saved_row = self._saved_row(fields, 2, self.target_server)
        self.target_typed = self._build_typed_fields(fields, "target")
        self.target_typed.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._row(fields, 4, "Destination copy name", self.target_database)
        self._row(fields, 5, "Databases already on server", self.target_db_list)
        self._row(fields, 6, "Conflicts", self.strategy)
        self.target_details = self._help(body, describe_server_endpoint(None))
        self.target_details.pack(fill="x", pady=(2, 0))
        self._help(
            body,
            "Enter the target server details. A saved server is optional. Test Target checks the "
            "server login, not the destination copy name. Start creates that copy from the source "
            "if it is missing. Public tables, columns, constraints, indexes, and rows are copied. "
            "Users, roles, files, views, and routines are not. Typed passwords are not saved.",
        ).pack(fill="x", pady=(2, 0))
        self.target_status = self._help(body, "Connection has not been tested for the current target.")
        self.target_status.pack(fill="x", pady=(2, 8))
        secondary_button(body, self.theme, text="Test Target", command=lambda: self._test_side("target"), width=130).pack(anchor="w")
        self._bind_entry(self.target_database, self._on_dest_typed)
        return shell

    def _option_row(self, parent, label: str):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=6)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, width=160, **self._label_style()).grid(row=0, column=0, padx=(0, 12), sticky="w")
        return row

    def _show_build_error(self, exc: Exception) -> None:
        message = redact_text(str(exc)) or exc.__class__.__name__
        ctk.CTkLabel(
            self,
            text="The Migration page could not be built.",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(padx=24, pady=(24, 8), fill="x")
        ctk.CTkLabel(
            self,
            text=message,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("error"),
            anchor="w",
            justify="left",
            wraplength=720,
        ).pack(padx=24, fill="x")
        self.log(f"Migration page failed to open: {message}", "ERROR")

    def _build_options_card(self, parent) -> None:
        shell, body = card(parent, self.theme, title="Migration options")
        shell.pack(fill="x", pady=(0, 10))
        scope_row = self._option_row(body, "Scope")
        self.scope = self._combo(scope_row, list(_SCOPES), "Schema and data", self._on_form_changed)
        self.scope.grid(row=0, column=1, sticky="ew")
        batch_row = self._option_row(body, "Batch size")
        self.batch_size = ctk.CTkEntry(batch_row)
        self.batch_size.insert(0, "1000")
        self.batch_size.grid(row=0, column=1, sticky="ew")
        timeout_row = self._option_row(body, "Statement timeout (ms)")
        self.timeout_ms = ctk.CTkEntry(timeout_row)
        self.timeout_ms.insert(0, "0")
        self.timeout_ms.grid(row=0, column=1, sticky="ew")
        self.pair_help = self._help(body, pair_summary(POSTGRESQL, POSTGRESQL))
        self.pair_help.pack(fill="x", pady=(4, 0))
        self._help(
            body,
            "Batch size must be 1 to 100000. Timeout 0 means the session default is left unchanged. "
            "Only public tables are migrated. Views, functions, triggers, and non-public schemas are not copied.",
        ).pack(fill="x", pady=(4, 0))
        self._bind_entry(self.batch_size, self._on_form_changed)
        self._bind_entry(self.timeout_ms, self._on_form_changed)

    def _build_safety_card(self, parent) -> None:
        shell, body = card(parent, self.theme, title="Safety and confirmation")
        shell.pack(fill="x", pady=(0, 10))
        self.target_identity_label = self._help(
            body,
            "Test the target to show the verified database type, host, port, and name. "
            "Saved connection names are not proof that source and target are different databases.",
        )
        self.target_identity_label.pack(fill="x", pady=(0, 8))
        self.accept_backup = ctk.BooleanVar(value=False)
        self.confirm_replace = ctk.BooleanVar(value=False)
        self.confirm_plan = ctk.BooleanVar(value=False)
        self.backup_box = ctk.CTkCheckBox(
            body,
            text=BACKUP_ACK_TEXT,
            variable=self.accept_backup,
            text_color=self.theme.c("text_primary"),
        )
        self.backup_box.pack(anchor="w", pady=3)
        self.replace_box = ctk.CTkCheckBox(
            body,
            text=REPLACE_CONFIRM_TEXT,
            variable=self.confirm_replace,
            text_color=self.theme.c("error"),
        )
        self.replace_box.pack(anchor="w", pady=3)
        ctk.CTkCheckBox(
            body,
            text=PLAN_CONFIRM_TEXT,
            variable=self.confirm_plan,
            text_color=self.theme.c("text_primary"),
        ).pack(anchor="w", pady=3)
        self._help(
            body,
            "A backup file is not required to copy data. Checking the restore acknowledgement only records "
            "that restore readiness has not been proven.",
        ).pack(fill="x", pady=(6, 0))

    def _build_workflow_card(self, parent) -> None:
        shell, body = card(parent, self.theme, title="Workflow")
        shell.pack(fill="x", pady=(0, 10))
        self._help(
            body,
            "1. Test Source  2. Test Target  3. Validate Configuration  4. Preview Plan  "
            "5. Review and confirm  6. Start Migration",
        ).pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        secondary_button(row, self.theme, text="Test Source", command=lambda: self._test_side("source"), width=120).pack(side="left", padx=(0, 8), pady=(0, 8))
        secondary_button(row, self.theme, text="Test Target", command=lambda: self._test_side("target"), width=120).pack(side="left", padx=(0, 8), pady=(0, 8))
        secondary_button(row, self.theme, text="Validate Configuration", command=self._validate, width=180).pack(side="left", padx=(0, 8), pady=(0, 8))
        secondary_button(row, self.theme, text="Preview Plan", command=self._preview, width=130).pack(side="left", padx=(0, 8), pady=(0, 8))
        primary_button(row, self.theme, text="Start Migration", command=self._start, width=150).pack(side="left", padx=(0, 8), pady=(0, 8))
        secondary_button(row, self.theme, text="Cancel", command=self._cancel_migration, width=90).pack(side="left", pady=(0, 8))

    def _build_progress_card(self, parent) -> None:
        shell, body = card(parent, self.theme, title="Progress and results")
        shell.pack(fill="x", pady=(0, 10))
        self.operation_label = ctk.CTkLabel(body, text="Operation ID: none", font=ctk.CTkFont(size=12), text_color=self.theme.c("text_secondary"), anchor="w")
        self.operation_label.pack(fill="x")
        self.status_label = ctk.CTkLabel(body, text="Ready", font=ctk.CTkFont(size=12), text_color=self.theme.c("text_secondary"), anchor="w")
        self.status_label.pack(fill="x", pady=(2, 6))
        self.progress = ctk.CTkProgressBar(body)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(0, 6))
        self.time_label = ctk.CTkLabel(
            body,
            text="Elapsed: 0s. Remaining time is unknown until a table finishes.",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        )
        self.time_label.pack(fill="x")
        self.counts_label = ctk.CTkLabel(body, text=self._count_text(), font=ctk.CTkFont(size=12), text_color=self.theme.c("text_secondary"), anchor="w")
        self.counts_label.pack(fill="x", pady=(2, 0))

    def _build_results_card(self, parent) -> None:
        shell, body = card(parent, self.theme, title="Details")
        shell.pack(fill="both", expand=True, pady=(0, 10))
        self.results = ctk.CTkTextbox(body, height=220, activate_scrollbars=True)
        self.results.pack(fill="both", expand=True)

    def _build_backup_card(self, parent) -> None:
        shell, body = card(parent, self.theme, title="Optional backup inspection")
        shell.pack(fill="x", pady=(0, 8))
        self._help(
            body,
            "This section is not a migration input. A selected file, a recognized dump header, "
            "and a completed migration are not a restore test. A PostgreSQL dump does not prove a MySQL target.",
        ).pack(fill="x", pady=(0, 8))
        path_row = ctk.CTkFrame(body, fg_color="transparent")
        path_row.pack(fill="x")
        path_row.grid_columnconfigure(0, weight=1)
        self.backup_path = ctk.CTkEntry(path_row, placeholder_text="Optional local backup file")
        self.backup_path.grid(row=0, column=0, sticky="ew")
        secondary_button(path_row, self.theme, text="Browse", command=self._browse_backup, width=90).grid(row=0, column=1, padx=(8, 0))
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", pady=(8, 0))
        secondary_button(actions, self.theme, text="Inspect Backup", command=self._inspect_backup, width=140).pack(side="left", padx=(0, 8))
        secondary_button(actions, self.theme, text="Verify Restore", command=self._verify_restore, width=140).pack(side="left")
        self.backup_status = self._help(
            body,
            f"{self._backup.file_summary} {self._backup.format_summary} {self._backup.restore_summary}",
        )
        self.backup_status.pack(fill="x", pady=(8, 0))

    def _form_state(self) -> MigrationFormState:
        return MigrationFormState(
            source_type=self._side_type("source"),
            source_server=self.source_server.get(),
            source_database=self.source_database.get(),
            target_type=self._side_type("target"),
            target_server=self.target_server.get(),
            target_database=self.target_database.get(),
            scope=self.scope.get(),
            strategy=self.strategy.get(),
            tables=self.tables.get(),
            batch_size=self.batch_size.get(),
            timeout_ms=self.timeout_ms.get(),
            source_method=self.source_method.get(),
            target_method=self.target_method.get(),
            source_endpoint=self._endpoint_key("source"),
            target_endpoint=self._endpoint_key("target"),
        )

    def _reset_workflow(self) -> None:
        self._plan = None
        self._validated_key = None
        self._source_test = None
        self._target_test = None
        self.confirm_plan.set(False)
        self.source_status.configure(text="Connection has not been tested for the current source.")
        self.target_status.configure(text="Connection has not been tested for the current target.")
        self.target_identity_label.configure(
            text="Test the target to show the verified database type, host, port, and name. "
            "Saved connection names are not proof that source and target are different databases."
        )

    def _on_form_changed(self, _value=None) -> None:
        if not getattr(self, "_ready", False):
            return
        if not self._dest_manual and not self._syncing:
            self._sync_destination_from_source()
        state = self._form_state()
        if self._source_test and self._source_test.get("key") != state.source_key():
            self._source_test = None
            self.source_status.configure(text="Source settings changed. Test Source again.")
        if self._target_test and self._target_test.get("key") != state.target_key():
            self._target_test = None
            self.target_status.configure(text="Target settings changed. Test Target again.")
            self.target_identity_label.configure(text="Target identity is stale until Test Target succeeds again.")
        if self._validated_key != state.plan_key():
            self._plan = None
            self._validated_key = None
            self.confirm_plan.set(False)
        self._sync_replace_row()

    def _on_source_type(self, _value=None) -> None:
        if not getattr(self, "_ready", False) or self._syncing:
            return
        self._apply_default_port("source")
        self._fill_source_servers()
        self._fill_target_types()
        self._fill_target_servers()
        self._reset_database_list("source")
        self._apply_source_server()
        self._apply_target_server()
        self._update_pair_help()

    def _on_target_type(self, _value=None) -> None:
        if not getattr(self, "_ready", False) or self._syncing:
            return
        self._apply_default_port("target")
        self._fill_target_servers()
        self._reset_database_list("target")
        self._apply_target_server()
        self._update_pair_help()

    def _on_method(self, side: str) -> None:
        if not getattr(self, "_ready", False) or self._syncing:
            return
        self._show_endpoint_fields(side)
        self._reset_database_list(side)
        if side == "source":
            self._apply_source_server()
        else:
            self._apply_target_server()

    def _on_ssh_toggle(self, side: str) -> None:
        extras = getattr(self, f"{side}_ssh_extras", None)
        enabled = bool(getattr(self, f"{side}_ssh").get()) if extras is not None else False
        if extras is None:
            return
        if enabled:
            extras.grid()
        else:
            extras.grid_remove()
        if getattr(self, "_ready", False) and not self._syncing:
            self._on_form_changed()

    def _show_endpoint_fields(self, side: str) -> None:
        typed = getattr(self, f"{side}_typed")
        saved = getattr(self, f"{side}_saved_row")
        if self._method(side) == METHOD_TYPED:
            typed.grid()
            saved.grid_remove()
        else:
            typed.grid_remove()
            saved.grid()
        self._on_ssh_toggle(side)

    def _method(self, side: str) -> str:
        return self.source_method.get() if side == "source" else self.target_method.get()

    def _apply_default_port(self, side: str) -> None:
        box = getattr(self, f"{side}_port")
        box.configure(show=secret_entry_show(True))
        current = box.get().strip()
        port = str(default_listen_port(self._side_type(side)))
        box.configure(placeholder_text=port)
        if current and current not in {"5432", "3306"}:
            return
        box.delete(0, "end")
        box.insert(0, port)

    def _endpoint_key(self, side: str) -> str:
        if self._method(side) != METHOD_TYPED:
            return ""
        password = getattr(self, f"{side}_password").get()
        ssh = "ssh" if getattr(self, f"{side}_ssh").get() else "direct"
        return "|".join(
            [
                getattr(self, f"{side}_host").get().strip(),
                getattr(self, f"{side}_port").get().strip(),
                getattr(self, f"{side}_username").get().strip(),
                ssh,
                getattr(self, f"{side}_linux_username").get().strip(),
                getattr(self, f"{side}_ssh_host").get().strip(),
                str(len(password)),
                str(hash(password)),
            ]
        )

    def _on_source_server(self, _value=None) -> None:
        if not getattr(self, "_ready", False) or self._syncing:
            return
        self._reset_database_list("source")
        self._apply_source_server()

    def _on_target_server(self, _value=None) -> None:
        if not getattr(self, "_ready", False) or self._syncing:
            return
        self._reset_database_list("target")
        self._apply_target_server()

    def _on_source_db_pick(self, value=None) -> None:
        self._apply_discovered_database(self.source_database, value)
        self._sync_destination_from_source()
        self._on_form_changed()

    def _on_target_db_pick(self, value=None) -> None:
        self._dest_manual = True
        self._apply_discovered_database(self.target_database, value)
        self._on_form_changed()

    def _on_dest_typed(self) -> None:
        if not self._syncing:
            self._dest_manual = True
        self._on_form_changed()

    def _on_strategy(self, _value=None) -> None:
        if _STRATEGIES[self.strategy.get()] != STRATEGY_REPLACE:
            self.confirm_replace.set(False)
        self._on_form_changed()

    def _sync_replace_row(self) -> None:
        replace = _STRATEGIES.get(self.strategy.get()) == STRATEGY_REPLACE
        if replace:
            self.replace_box.pack(anchor="w", pady=3, after=self.backup_box)
        else:
            self.replace_box.pack_forget()
            self.confirm_replace.set(False)

    def _load_servers(self, _value=None) -> None:
        try:
            self._servers = load_saved_servers()
        except Exception:
            self._servers = []
        self._fill_source_servers()
        self._fill_target_types()
        self._fill_target_servers()
        self._apply_source_server()
        self._apply_target_server()
        self._update_pair_help()

    def _fill_target_types(self) -> None:
        types = list(target_types_for(self.source_type.get() or POSTGRESQL)) or list(TARGET_TYPES)
        current = self.target_type.get()
        self.target_type.configure(values=types)
        if current not in types:
            self.target_type.set(types[0])

    def _update_pair_help(self) -> None:
        if getattr(self, "pair_help", None) is None:
            return
        self.pair_help.configure(text=pair_summary(self.source_type.get(), self.target_type.get()))

    def _preferred_choice(self, servers: list, db_type: str | None = None) -> str | None:
        live = self.connection
        if live is None:
            return None
        live_type = str(getattr(live.db_type, "value", live.db_type))
        if db_type and live_type != db_type:
            return None
        for server in servers:
            if live_session_matches_server(live, server):
                return server_choice_label(server)
        return None

    def _set_choices(self, box, servers: list, preferred: str | None = None) -> None:
        labels = saved_server_choices(servers)
        box.configure(values=labels)
        if box.get() not in labels:
            box.set(preferred if preferred in labels else labels[0])

    def _fill_source_servers(self) -> None:
        wanted = self.source_type.get()
        servers = servers_for_type(self._servers, wanted)
        self._set_choices(self.source_server, servers, self._preferred_choice(servers, wanted))

    def _fill_target_servers(self) -> None:
        wanted = self.target_type.get()
        servers = servers_for_type(self._servers, wanted)
        self._set_choices(self.target_server, servers, self._preferred_choice(servers, wanted))

    def _apply_source_server(self) -> None:
        server = self._peek_server("source")
        self._syncing = True
        try:
            if self._method("source") == METHOD_SAVED:
                self._fill_database(self.source_database, server)
            self.source_details.configure(text=describe_server_endpoint(server))
            self._sync_destination_from_source(force=True)
        finally:
            self._syncing = False
        self._on_form_changed()

    def _apply_target_server(self) -> None:
        server = self._peek_server("target")
        self._syncing = True
        try:
            self._sync_destination_from_source(force=not self._dest_manual)
            self.target_details.configure(text=describe_server_endpoint(server))
        finally:
            self._syncing = False
        self._on_form_changed()

    def _sync_destination_from_source(self, *, force: bool = False) -> None:
        if self._dest_manual and not force:
            return
        if force:
            self._dest_manual = False
        name = destination_copy_name(self.source_database.get().strip(), self.target_type.get())
        if self.target_database.get().strip() == name:
            return
        self.target_database.delete(0, "end")
        if name:
            self.target_database.insert(0, name)

    def _peek_server(self, side: str):
        try:
            return self._server(side)
        except ValueError:
            return None

    def _server(self, side: str):
        db_type = self._side_type(side)
        if self._method(side) == METHOD_TYPED:
            return manual_endpoint(
                db_type=db_type,
                host=getattr(self, f"{side}_host").get(),
                port=getattr(self, f"{side}_port").get(),
                username=getattr(self, f"{side}_username").get(),
                password=getattr(self, f"{side}_password").get(),
                ssh_enabled=bool(getattr(self, f"{side}_ssh").get()),
                linux_username=getattr(self, f"{side}_linux_username").get(),
                linux_password=getattr(self, f"{side}_linux_password").get(),
                ssh_host=getattr(self, f"{side}_ssh_host").get(),
            )
        choice = self.source_server.get() if side == "source" else self.target_server.get()
        return find_saved_server(servers_for_type(self._servers, db_type), choice)

    @staticmethod
    def _fill_database(entry, server) -> None:
        entry.delete(0, "end")
        if server is not None:
            entry.insert(0, server.database)

    def _planned_result(self, side: str):
        server = self._require_matching_server(side)
        database = self._side_database(side)
        if side == "target":
            error = destination_name_error(database, self._side_type(side))
            if error:
                raise ValueError(error)
        elif not database:
            raise ValueError(f"Enter the {side} database name.")
        return migration_connection_result(server, database, self.connection)

    def _side_database(self, side: str) -> str:
        return self.source_database.get().strip() if side == "source" else self.target_database.get().strip()

    def _side_type(self, side: str) -> str:
        return self.source_type.get() if side == "source" else self.target_type.get()

    def _require_matching_server(self, side: str):
        if self._method(side) == METHOD_TYPED:
            error = typed_endpoint_error(
                getattr(self, f"{side}_host").get(),
                getattr(self, f"{side}_port").get(),
                getattr(self, f"{side}_username").get(),
                ssh_enabled=bool(getattr(self, f"{side}_ssh").get()),
                linux_username=getattr(self, f"{side}_linux_username").get(),
            )
            if error:
                raise ValueError(error)
            return self._server(side)
        server = self._server(side)
        if server is None:
            raise ValueError(
                f"Enter the {side} server details, or select a saved {side} connection "
                f"whose type is {self._side_type(side)}."
            )
        return server

    def _reset_database_list(self, side: str) -> None:
        box = self.source_db_list if side == "source" else self.target_db_list
        label = "Test Source to list databases" if side == "source" else "Test Target to list databases"
        box.configure(values=[label])
        box.set(label)

    def _apply_discovered_database(self, entry, value) -> None:
        name = str(value or "").strip()
        if not name or name.startswith("Test ") or name.startswith("("):
            return
        entry.delete(0, "end")
        entry.insert(0, name)

    def _set_discovered_databases(self, side: str, names: list[str], current: str) -> None:
        box = self.source_db_list if side == "source" else self.target_db_list
        values = list(dict.fromkeys([name for name in names if name]))
        if current and current not in values:
            values.insert(0, current)
        if not values:
            values = ["(none discovered)"]
        box.configure(values=values)
        box.set(current if current in values else values[0])

    @contextmanager
    def _open_side(self, side: str, *, server_only: bool = False, database: str | None = None):
        server = self._require_matching_server(side)
        if server_only:
            probe = server_probe_database(self._side_type(side))
            with open_migration_endpoint(server, probe, self.connection, allow_empty=not bool(probe)) as result:
                yield result, self._connector_from_result(side, result)
            return
        name = self._side_database(side) if database is None else database
        if side == "target":
            error = destination_name_error(name, self._side_type(side))
            if error:
                raise ValueError(error)
        elif not name:
            raise ValueError(f"Enter the {side} database name.")
        with open_migration_endpoint(server, name, self.connection) as result:
            yield result, self._connector_from_result(side, result)

    @contextmanager
    def _open_pair(self, *, target_server_only: bool = False):
        with self._open_side("source") as (source_result, source):
            with self._open_side("target", server_only=target_server_only) as (target_result, target):
                yield source_result, source, target_result, target

    def _ensure_destination(self, connector, db_type: str, name: str) -> bool:
        """Create the destination copy database when it is missing. Returns True if created."""
        if db_type == MYSQL:
            if mysql_destination_exists(connector.execute_query, name):
                return False
            create_mysql_destination(connector.execute_script, name)
            return True
        if db_type == POSTGRESQL:
            existing = list_accessible_databases(POSTGRESQL, connector.execute_query)
            if name in existing:
                return False
            conn = connector.open_connection()
            try:
                conn.autocommit = True
                create_postgresql_destination(lambda sql: conn.cursor().execute(sql), name)
            finally:
                conn.close()
            return True
        raise ValueError(unsupported_migration_reason(self.source_type.get(), db_type) or "Unsupported target type.")

    def _connector_from_result(self, side: str, result):
        kind = self._side_type(side)
        info = result.to_connection_info()
        if kind == POSTGRESQL:
            return PostgreSQLConnector(info)
        if kind == MYSQL:
            return MySQLConnector(info)
        if side == "target":
            return get_connector_from_info(DatabaseType(kind), info)
        raise ValueError(unsupported_migration_reason(self.source_type.get(), self.target_type.get()) or "Select a supported database type.")

    def _engine_block(self) -> str | None:
        return unsupported_migration_reason(self._side_type("source"), self._side_type("target"))

    def _selected_tables(self) -> list[str] | None:
        names = [part.strip() for part in self.tables.get().split(",") if part.strip()]
        return names or None

    def _settings(self) -> tuple[str, str, int, int]:
        scope = _SCOPES[self.scope.get()]
        strategy = _STRATEGIES[self.strategy.get()]
        batch, batch_error = parse_batch_size(self.batch_size.get())
        if batch_error:
            raise ValueError(batch_error)
        timeout, timeout_error = parse_timeout_ms(self.timeout_ms.get())
        if timeout_error:
            raise ValueError(timeout_error)
        return scope, strategy, batch, timeout

    def _write(self, text: str) -> None:
        self.results.insert("end", text + "\n")
        self.results.see("end")

    def _count_text(self) -> str:
        counts = self._counts
        return (
            f"Created {counts['created']}. Copied {counts['copied']}. "
            f"Skipped {counts['skipped']}. Failed {counts['failed']}. Replaced {counts['replaced']}."
        )

    def _set_progress(self, value: float) -> None:
        self.progress.set(max(0.0, min(value, 1.0)))

    def _tests_current(self) -> tuple[bool, bool]:
        state = self._form_state()
        source_ok = bool(self._source_test and self._source_test.get("ok") and self._source_test.get("key") == state.source_key())
        target_ok = bool(self._target_test and self._target_test.get("ok") and self._target_test.get("key") == state.target_key())
        return source_ok, target_ok

    def _read_observed_database(self, connector, kind: str) -> str:
        if kind == POSTGRESQL:
            rows = connector.execute_query("SELECT current_database() AS database_name")
        elif kind == MYSQL:
            rows = connector.execute_query("SELECT DATABASE() AS database_name")
        else:
            return str(getattr(getattr(connector, "config", None), "database", "") or "")
        return str((rows or [{}])[0].get("database_name") or "")

    def _test_side(self, side: str) -> None:
        selected = self._side_type(side)
        if side == "source" and selected != POSTGRESQL:
            self._write(
                f"{side.title()} was not tested. "
                f"{unsupported_migration_reason(selected, self._side_type('target'))}"
            )
            return

        def task() -> None:
            last_error = ""
            for attempt in range(2):
                try:
                    discovered: list[str] = []
                    dest_exists = None
                    with self._open_side(side, server_only=(side == "target")) as (result, connector):
                        ok = connector.test_connection()
                        version = connector.get_version() if ok else ""
                        observed = self._read_observed_database(connector, selected) if ok else ""
                        if ok:
                            discovered = list_accessible_databases(selected, connector.execute_query)
                    dest_name = self._side_database("target") if side == "target" else ""
                    if ok and side == "target":
                        dest_exists = dest_name.casefold() in {item.casefold() for item in discovered}
                    if ok:
                        shown_db = dest_name if side == "target" else (observed or result.database)
                        identity = describe_verified_target(
                            selected,
                            result.endpoint_host,
                            int(result.endpoint_port),
                            shown_db or result.endpoint_host,
                            version,
                        )
                        reused = live_session_matches_server(self.connection, self._server(side))
                        via = " using the current desktop session" if reused else ""
                        listed = f" Databases on the server: {', '.join(discovered)}." if discovered else ""
                        if side == "target" and dest_name:
                            source_name = self.source_database.get().strip()
                            if dest_exists:
                                extra = (
                                    f" Destination {dest_name} already exists. "
                                    "Public tables will be copied into it. Users, roles, files, views, and routines are not copied."
                                )
                            else:
                                extra = (
                                    f" Destination {dest_name} is not on this server yet. "
                                    f"Start will create it as a converted copy of {source_name or 'the source database'}. "
                                    "Users, roles, files, views, and routines are not copied."
                                )
                        else:
                            extra = listed
                        message = f"{side.title()} connection succeeded{via}: {identity}.{extra}"
                    else:
                        message = f"{side.title()} connection failed."

                    def show(
                        success=ok,
                        text=message,
                        observed_name=observed,
                        version_text=version,
                        names=discovered,
                        exists=dest_exists,
                    ) -> None:
                        state = self._form_state()
                        key = state.source_key() if side == "source" else state.target_key()
                        record = {
                            "ok": success,
                            "key": key,
                            "database": dest_name if side == "target" else observed_name,
                            "version": version_text,
                            "message": text,
                            "dest_exists": exists,
                            "discovered": names,
                        }
                        if side == "source":
                            self._source_test = record
                            self.source_status.configure(text=text)
                            self._sync_destination_from_source()
                        else:
                            self._target_test = record
                            self.target_status.configure(text=text)
                            self.target_identity_label.configure(text=text if success else "Target identity was not verified.")
                        if success:
                            current = dest_name if side == "target" else (observed_name or self._side_database(side))
                            self._set_discovered_databases(side, names, current)
                        self._write(text)

                    self.after(0, show)
                    return
                except Exception as exc:
                    last_error = redact_text(str(exc))
                    lost = "2013" in last_error or "Lost connection" in last_error
                    if side != "target" or attempt == 1 or not lost:
                        break
                    time.sleep(0.7)

            text = f"{side.title()} connection failed: {last_error}"

            def show_error() -> None:
                if side == "source":
                    self._source_test = None
                    self.source_status.configure(text=text)
                else:
                    self._target_test = None
                    self.target_status.configure(text=text)
                    self.target_identity_label.configure(text="Target identity was not verified.")
                self._write(text)

            self.after(0, show_error)

        threading.Thread(target=task, daemon=True).start()

    def _describe_backup(self, readiness) -> None:
        self._backup = readiness
        summary = f"{readiness.file_summary} {readiness.format_summary} {readiness.restore_summary}"
        self.backup_status.configure(text=summary)
        self._write(readiness.file_summary)
        self._write(readiness.format_summary)
        self._write(readiness.restore_summary)

    def _browse_backup(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Select a PostgreSQL backup to inspect",
            filetypes=[
                ("PostgreSQL dump", "*.dump"),
                ("SQL", "*.sql"),
                ("Backup", "*.backup"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.backup_path.delete(0, "end")
        self.backup_path.insert(0, path)
        self._inspect_backup()

    def _inspect_backup(self) -> None:
        raw = self.backup_path.get().strip()
        if not raw:
            from tkinter import filedialog

            path = filedialog.askopenfilename(
                title="Select a backup file to inspect",
                filetypes=[
                    ("PostgreSQL dump", "*.dump"),
                    ("SQL", "*.sql"),
                    ("Backup", "*.backup"),
                    ("All files", "*.*"),
                ],
            )
            if path:
                self.backup_path.delete(0, "end")
                self.backup_path.insert(0, path)
                raw = path
        readiness = inspect_backup_file(Path(raw) if raw else None)
        self._describe_backup(readiness)

    def _verify_restore(self) -> None:
        if self.target_type.get() == MYSQL:
            self._write(
                "Restore was not run. A PostgreSQL dump restore does not prove a MySQL target. "
                "Restore has not been proven."
            )
            return
        blocked = self._engine_block()
        if blocked:
            self._write(blocked)
            return
        if self.target_database.get().strip() != DISPOSABLE_RESTORE_DATABASE:
            self._write(
                "Restore was not run. The target database must be "
                f"{DISPOSABLE_RESTORE_DATABASE}. Restore has not been proven."
            )
            return

        def task() -> None:
            try:
                with self._open_pair() as (source_result, source, target_result, target):
                    self._verify_restore_with(source_result, source, target_result, target)
            except Exception as exc:
                self.after(0, lambda: self._write(f"Restore was not verified: {redact_text(str(exc))}"))

        threading.Thread(target=task, daemon=True).start()

    def _verify_restore_with(self, source_result, source, target_result, target) -> None:
        tables = [str(row.get("table_name")) for row in source.execute_query(PUBLIC_TABLES_SQL)]
        expectations = []
        for name in tables:
            rows = source.execute_query(f"SELECT count(*) AS row_count FROM public.{quote_ident(name)}")
            expectations.append(
                (f"SELECT count(*) AS row_count FROM public.{quote_ident(name)}", str(rows[0].get("row_count")))
            )
        if not expectations:
            self.after(0, lambda: self._write("Restore was not run because the source has no public tables to verify. Restore has not been proven."))
            return
        path = Path(self.backup_path.get().strip() or "db_autopro_migration_restore.dump")
        readiness = verify_postgresql_restore(
            source_host=source_result.host,
            source_port=int(source_result.port),
            source_user=source_result.username,
            source_password=source_result.password,
            source_database=self.source_database.get().strip(),
            target_host=target_result.host,
            target_port=int(target_result.port),
            target_user=target_result.username,
            target_password=target_result.password,
            target_database=DISPOSABLE_RESTORE_DATABASE,
            backup_path=path,
            target_is_empty=lambda: not target.execute_query(PUBLIC_TABLES_SQL),
            target_query=target.execute_query,
            expectations=expectations,
        )
        self.after(0, lambda: self._describe_backup(readiness))

    def _gate_read(self, *, preview: bool) -> str | None:
        if self._busy:
            return "Wait for the current operation to finish."
        blocked = self._engine_block()
        if blocked:
            return blocked
        try:
            self._settings()
        except ValueError as exc:
            return str(exc)
        try:
            same = same_instance_reason(self._planned_result("source"), self._planned_result("target"))
        except ValueError as exc:
            return str(exc)
        if same:
            return same
        source_ok, target_ok = self._tests_current()
        if not source_ok:
            return "Test the source connection before validating or starting."
        if not target_ok:
            return "Test the target connection before validating or starting."
        mismatch = destination_identity_error(
            self.target_database.get().strip(),
            (self._target_test or {}).get("database") or "",
            dest_exists=(self._target_test or {}).get("dest_exists"),
            discovered=(self._target_test or {}).get("discovered") or [],
        )
        if mismatch:
            return mismatch
        if preview and self._validated_key != self._form_state().plan_key():
            return "Validate the current configuration before previewing the plan."
        return None

    def _validate(self) -> None:
        reason = self._gate_read(preview=False)
        if reason:
            self._plan = None
            self._validated_key = None
            self._write(reason)
            self.status_label.configure(text="Validation blocked")
            return
        self._run_read(preview=False)

    def _preview(self) -> None:
        reason = self._gate_read(preview=True)
        if reason:
            self._plan = None
            self._write(reason)
            self.status_label.configure(text="Preview blocked")
            return
        self._run_read(preview=True)

    def _run_read(self, *, preview: bool) -> None:
        mysql_target = self._side_type("target") == MYSQL

        def task() -> None:
            try:
                scope, strategy, batch, timeout = self._settings()
                with self._open_pair(target_server_only=True) as (_source_result, source, _target_result, target):
                    self._assess_and_show(source, target, mysql_target, scope, strategy, batch, timeout, preview)
            except Exception as exc:
                self._plan = None
                self._validated_key = None
                self.after(0, lambda: self._write(f"Validation failed: {redact_text(str(exc))}"))

        threading.Thread(target=task, daemon=True).start()

    def _assess_and_show(self, source, target, mysql_target, scope, strategy, batch, timeout, preview) -> None:
        if mysql_target:
            report = assess_pg_mysql(
                source.execute_query,
                target.execute_query,
                scope=scope,
                selected_tables=self._selected_tables(),
                strategy=strategy,
                confirm_replace=self.confirm_replace.get(),
                accept_unproven_backup=self.accept_backup.get(),
                backup=self._backup,
                batch_size=batch,
                statement_timeout_ms=timeout,
                expected_database=self.target_database.get().strip(),
            )
            plan = build_pg_mysql_plan(report, scope=scope, strategy=strategy, batch_size=batch, statement_timeout_ms=timeout)
        else:
            report = assess_migration(
                source.execute_query,
                target.execute_query,
                scope=scope,
                selected_tables=self._selected_tables(),
                strategy=strategy,
                confirm_replace=self.confirm_replace.get(),
                accept_unproven_backup=self.accept_backup.get(),
                backup=self._backup,
                batch_size=batch,
                statement_timeout_ms=timeout,
            )
            plan = build_plan(report, scope=scope, strategy=strategy, batch_size=batch, statement_timeout_ms=timeout)
        key = self._form_state().plan_key()
        self._validated_key = key if report.ready else None
        self._plan = plan if preview and report.ready else None

        def show() -> None:
            self._write(plan.text)
            self.status_label.configure(text="Plan ready" if self._plan else ("Validated" if report.ready else "Validation blocked"))

        self.after(0, show)

    def _start_reason(self) -> str | None:
        settings_error = None
        try:
            self._settings()
        except ValueError as exc:
            settings_error = str(exc)
        same = None
        identity_error = None
        try:
            same = same_instance_reason(self._planned_result("source"), self._planned_result("target"))
            identity_error = destination_identity_error(
                self.target_database.get().strip(),
                (self._target_test or {}).get("database") or "",
                dest_exists=(self._target_test or {}).get("dest_exists"),
                discovered=(self._target_test or {}).get("discovered") or [],
            )
        except ValueError as exc:
            settings_error = settings_error or str(exc)
        source_ok, target_ok = self._tests_current()
        conflicts = list(getattr(getattr(self._plan, "precheck", None), "conflicts", []) or [])
        return start_block_reason(
            source_type=self._side_type("source"),
            target_type=self._side_type("target"),
            precheck_ready=bool(self._plan and self._plan.precheck.ready),
            plan_current=self._plan is not None and self._validated_key == self._form_state().plan_key(),
            confirmed=self.confirm_plan.get(),
            busy=self._busy,
            source_tested=source_ok,
            target_tested=target_ok,
            configuration_current=source_ok and target_ok and self._validated_key == self._form_state().plan_key(),
            same_instance=same is not None,
            replace_needs_confirm=replace_requires_confirmation(_STRATEGIES[self.strategy.get()], conflicts),
            replace_confirmed=self.confirm_replace.get(),
            settings_error=settings_error,
            identity_error=identity_error,
        )

    def _start(self) -> None:
        reason = self._start_reason()
        if reason:
            self._write(reason)
            return
        self._busy = True
        self._cancel = threading.Event()
        self._started = time.monotonic()
        self._done = 0
        self._total = len(self._plan.precheck.tables)
        self._operation_id = ""
        self._counts = outcome_counts([])
        self._set_progress(0)
        self.operation_label.configure(text="Operation ID: assigned when the engine starts")
        self.counts_label.configure(text=self._count_text())
        self.status_label.configure(text="Starting")
        self._tick()

        def task() -> None:
            try:
                dest = self.target_database.get().strip()
                with self._open_side("source") as (_source_result, source):
                    with self._open_side("target", server_only=True) as (_probe_result, probe):
                        self._ensure_destination(probe, self._side_type("target"), dest)
                    with self._open_side("target", database=dest) as (_target_result, target):
                        if getattr(self._plan, "engine", "postgresql") == "postgresql_to_mysql":
                            self._run_mysql_migration(source, target)
                            return
                        self._run_postgres_migration(source, target)
            except Exception as exc:
                self.after(0, lambda: self._show_failure(redact_text(str(exc))))

        threading.Thread(target=task, daemon=True).start()

    def _run_postgres_migration(self, source, target) -> None:
        target_conn = source_conn = None
        try:
            target_conn = target.open_connection()
            if self._plan.statement_timeout_ms:
                with target_conn.cursor() as cursor:
                    cursor.execute("SET statement_timeout = %s", (self._plan.statement_timeout_ms,))
                target_conn.commit()
            source_conn = source.open_connection()

            def execute(sql: str) -> None:
                with target_conn.cursor() as cursor:
                    cursor.execute(sql)
                target_conn.commit()

            def copy_rows(table: str, batch: int, cancel: threading.Event) -> int:
                return stream_copy_table(source_conn, target_conn, table, batch, cancel)

            operator = getattr(self.connection, "username", "") or "operator"
            result = start_migration(
                self._plan,
                confirmed=True,
                operator=operator,
                source_query=source.execute_query,
                target_query=target.execute_query,
                target_execute=execute,
                copy_rows=copy_rows,
                cancel=self._cancel,
                on_progress=self._on_progress,
            )
            self.after(0, lambda: self._show_result(result))
        except Exception as exc:
            self.after(0, lambda: self._show_failure(redact_text(str(exc))))
        finally:
            for conn in (source_conn, target_conn):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _run_mysql_migration(self, source, target) -> None:
        source_conn = target_conn = None
        try:
            source_conn = source.open_connection()
            target_conn = target.open_connection()
            session = _MysqlSession(target_conn)

            def fetch_batches(table: str, columns: list[str], batch_size: int, cancel: threading.Event):
                column_sql = ", ".join(quote_ident(name) for name in columns)
                statement = f"SELECT {column_sql} FROM public.{quote_ident(table)}"
                cursor = source_conn.cursor(name="pgmysql_stream")
                try:
                    cursor.itersize = batch_size
                    cursor.execute(statement)
                    while True:
                        if cancel.is_set():
                            return
                        rows = cursor.fetchmany(batch_size)
                        if not rows:
                            return
                        yield rows
                finally:
                    cursor.close()

            def count_source(table: str) -> int:
                rows = source.execute_query(source_count_sql(table))
                return int(rows[0].get("row_count") or 0)

            operator = getattr(self.connection, "username", "") or "operator"
            result = start_pg_mysql_migration(
                self._plan,
                confirmed=True,
                operator=operator,
                fetch_batches=fetch_batches,
                target=session,
                count_source=count_source,
                cancel=self._cancel,
                on_progress=self._on_progress,
            )
            self.after(0, lambda: self._show_result(result))
        except Exception as exc:
            self.after(0, lambda: self._show_failure(redact_text(str(exc))))
        finally:
            for conn in (source_conn, target_conn):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _show_result(self, result) -> None:
        self._busy = False
        self._operation_id = getattr(result, "migration_id", "") or ""
        self._counts = outcome_counts(getattr(result, "outcomes", []))
        finished = result.status.startswith("Completed") or result.status in {"Failed", "Cancelled", "Partially completed", "Verification failed", "Completed with warnings", "Completed and verified"}
        self._set_progress(1.0 if result.status.startswith("Completed") else (0.95 if self._total else 0))
        if not finished:
            self._set_progress(min(self.progress.get(), 0.95))
        self.operation_label.configure(text=f"Operation ID: {self._operation_id or 'none'}")
        self.status_label.configure(text=result.status)
        self.counts_label.configure(text=self._count_text())
        self._write(f"{result.status}: {result.summary}")
        for item in result.outcomes:
            self._write(f"{item.name}: {item.action}. {item.detail}")
        if result.status in {"Partially completed", "Cancelled", "Verification failed"}:
            self._write("Reconcile the interrupted operation before retrying the same target. This page does not rerun it automatically.")

    def _show_failure(self, message: str) -> None:
        self._busy = False
        self._set_progress(0)
        self.status_label.configure(text="Failed")
        self._write(f"Migration failed: {message}")

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._done = done
        self._total = total

        def show() -> None:
            if total > 0:
                fraction = done / total
                self._set_progress(1.0 if done >= total and not self._busy else min(fraction, 0.95))
            self.status_label.configure(text=message)

        self.after(0, show)

    def _tick(self) -> None:
        if not self._busy:
            return
        elapsed = int(time.monotonic() - self._started)
        if self._done > 0 and self._total > self._done:
            remaining = int(elapsed / self._done * (self._total - self._done))
            text = f"Elapsed: {elapsed}s. Estimated remaining: {remaining}s."
        else:
            text = f"Elapsed: {elapsed}s. Remaining time is unknown until a table finishes."
        self.time_label.configure(text=text)
        self.after(1000, self._tick)

    def _cancel_migration(self) -> None:
        self._cancel.set()
        self._write(
            "Cancellation requested. The current table or in-flight MySQL statement may finish. "
            "Committed MySQL DDL is not rolled back. The current table may remain uncertain."
        )
        self.log("Migration cancellation requested")
