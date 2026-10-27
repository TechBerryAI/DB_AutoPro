"""Backup management view."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    BackupRequest,
    BackupSourceType,
    DatabaseType,
    MySQLBackupScope,
    MySQLDumpFormat,
    PgBackupScope,
    PgDumpFormat,
    backup_methods_for,
)
from backend.services.backup_catalog import (
    category_description,
    describe_backup_command,
    method_label,
)
from backend.services.backup_service import BackupService
from desktop.connection_store import SavedServer, load_saved_servers, upsert_server
from desktop.db_catalog import (
    fetch_mongodb_collections,
    fetch_mongodb_database_size_bytes,
    fetch_mysql_tables,
    fetch_postgresql_schemas,
    fetch_postgresql_tables,
    fetch_server_databases,
)
from backend.services.monitoring_service import MonitoringService
from desktop.design_system import primary_button, secondary_button
from desktop.ops_widgets import empty_state
from desktop.remote_directory_browser import RemoteDirectoryBrowser
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView

PG_SCOPE_LABELS: list[tuple[str, PgBackupScope]] = [
    ("Entire Database", PgBackupScope.ENTIRE_DATABASE),
    ("Single Schema", PgBackupScope.SINGLE_SCHEMA),
    ("Multiple Schemas", PgBackupScope.MULTIPLE_SCHEMAS),
    ("Selected Tables", PgBackupScope.SELECTED_TABLES),
    ("Data Only", PgBackupScope.DATA_ONLY),
    ("Schema Only", PgBackupScope.SCHEMA_ONLY),
    ("Selected Objects (Advanced)", PgBackupScope.SELECTED_OBJECTS),
]

PG_SCOPE_LABEL_TO_VALUE = {label: value for label, value in PG_SCOPE_LABELS}
PG_SCOPE_VALUE_TO_LABEL = {value: label for label, value in PG_SCOPE_LABELS}

PG_FORMAT_LABELS: list[tuple[str, PgDumpFormat]] = [
    ("Plain SQL", PgDumpFormat.PLAIN),
    ("Custom Format", PgDumpFormat.CUSTOM),
    ("Tar Format", PgDumpFormat.TAR),
    ("Directory Format", PgDumpFormat.DIRECTORY),
]
PG_FORMAT_LABEL_TO_VALUE = {label: value for label, value in PG_FORMAT_LABELS}
PG_FORMAT_VALUE_TO_LABEL = {value: label for label, value in PG_FORMAT_LABELS}

MYSQL_SCOPE_LABELS: list[tuple[str, MySQLBackupScope]] = [
    ("Entire Database", MySQLBackupScope.ENTIRE_DATABASE),
    ("Selected Tables", MySQLBackupScope.SELECTED_TABLES),
    ("Data Only", MySQLBackupScope.DATA_ONLY),
    ("Schema Only", MySQLBackupScope.SCHEMA_ONLY),
]
MYSQL_SCOPE_LABEL_TO_VALUE = {label: value for label, value in MYSQL_SCOPE_LABELS}

MYSQL_FORMAT_LABELS: list[tuple[str, MySQLDumpFormat]] = [
    ("Plain SQL (.sql)", MySQLDumpFormat.PLAIN),
    ("Compressed SQL (.sql.gz)", MySQLDumpFormat.COMPRESSED),
]
MYSQL_FORMAT_LABEL_TO_VALUE = {label: value for label, value in MYSQL_FORMAT_LABELS}

ALL_DATABASES_LABEL = "All databases"
SELECT_DATABASE_LABEL = "Select a database"
_IGNORED_DATABASE_VALUES = {
    "",
    "CTkComboBox",
    ALL_DATABASES_LABEL,
    SELECT_DATABASE_LABEL,
    "Loading databases...",
}


class BackupView(BaseView):
    """Backup operations with logical / physical segregation."""

    DB_TYPES = [t.value for t in DatabaseType]

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = BackupService()
        self._category = BackupCategory.LOGICAL
        # Path starts empty — user must type or browse (File / Folder). No project default.
        self._local_output_dir = ""
        self._remote_output_dir = "/mnt/backup"
        self._other_ssh_connected = False
        self._other_connected_host = ""
        self._cached_other_ssh: dict | None = None
        self._mongodb_collections: list[dict] = []
        self._mongo_size_bytes = 0
        self._other_ssh_password_visible = False
        self._collection_rows: list[ctk.CTkFrame] = []
        self._selected_collection_name: str | None = None
        self._scope_selected_targets: list[str] = []
        self._scope_available_names: list[str] = []
        self._tables_schema: str | None = None
        self._schema_available_names: list[str] = []
        self._results_host: ctk.CTkFrame | None = None
        self._results_empty: ctk.CTkFrame | None = None
        self._build_ui()
        self._init_remote_defaults()
        self._refresh_methods()
        self._sync_results_empty_state()

    def _init_remote_defaults(self) -> None:
        default_dump = "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump"
        self._remote_output_dir = "/mnt/backup"
        if not self.remote_binary.get().strip():
            self.remote_binary.insert(0, default_dump)
        if not self.remote_dir_entry.get().strip():
            self.remote_dir_entry.insert(0, self._remote_output_dir)
        if hasattr(self, "other_dir_entry") and not self.other_dir_entry.get().strip():
            self.other_dir_entry.insert(0, self._remote_output_dir)

        saved = self._find_saved_server()
        if saved:
            if saved.backup_folder_name:
                self.backup_folder_entry.delete(0, "end")
                self.backup_folder_entry.insert(0, saved.backup_folder_name)
            if saved.remote_backup_dir:
                self._remote_output_dir = saved.remote_backup_dir
                self.remote_dir_entry.delete(0, "end")
                self.remote_dir_entry.insert(0, saved.remote_backup_dir)
            if saved.mongodump_path:
                self.remote_binary.delete(0, "end")
                self.remote_binary.insert(0, saved.mongodump_path)
            if saved.local_output_dir and not self.output_dir_entry.get().strip():
                self._apply_backup_path(saved.local_output_dir)
            if self._is_mongodb_session() and saved.backup_collection:
                self._selected_collection_name = saved.backup_collection
                self.collection_combo.configure(
                    values=["All collections", saved.backup_collection]
                )
                self.collection_combo.set(saved.backup_collection)

        if self._ssh_available() and self._remote_browser:
            self._remote_browser.set_initial_path(
                self.remote_dir_entry.get().strip() or self._remote_output_dir
            )

        self._sync_source_fields()
        self._update_ssh_status_label()
        self._update_collection_scope_label()

    def refresh(self, *, force: bool = False) -> None:
        """Reload backup form data from the active connection / server."""
        del force  # always re-fetch on header Refresh
        if self.connection and hasattr(self, "db_type"):
            try:
                self.db_type.set(self.connection.db_type.value)
            except Exception:
                pass
        self._init_remote_defaults()
        self._sync_source_fields()
        self._populate_database_dropdown()
        try:
            self._load_scope_targets()
        except Exception:
            pass
        if self._is_mongodb_session():
            try:
                self._load_collections()
            except Exception:
                pass
        self._update_ssh_status_label()
        self._update_collection_scope_label()
        self._update_command_preview()
        self._sync_results_empty_state()
        self.log("Backup page refreshed")

    def _has_backup_history(self) -> bool:
        """True when audit log has at least one backup event for this user/session."""
        try:
            monitoring = MonitoringService()
            user = self.owner_username
            ok, fail = monitoring.count_audit_for_period(
                "yearly",
                actions=("backup",),
                user=user,
            )
            return (ok + fail) > 0
        except Exception:
            return False

    def _focus_run_backup(self) -> None:
        """Scroll/focus the primary Run Backup action from the empty state."""
        try:
            self.results.delete("1.0", "end")
            self.results.insert("end", "Ready to run a backup. Configure options above, then click Run Backup.\n")
        except Exception:
            pass
        self._hide_results_empty_state()
        try:
            self.status_label.configure(text="Ready — choose options and click Run Backup")
        except Exception:
            pass

    def _hide_results_empty_state(self) -> None:
        if self._results_empty is not None:
            try:
                self._results_empty.destroy()
            except Exception:
                pass
            self._results_empty = None
        if hasattr(self, "results") and self.results is not None:
            try:
                self.results.grid(row=0, column=0, sticky="nsew")
            except Exception:
                pass

    def _sync_results_empty_state(self) -> None:
        """Show 'No backups yet' when there is no backup history."""
        host = self._results_host
        if host is None or not hasattr(self, "results"):
            return
        if self._has_backup_history():
            self._hide_results_empty_state()
            return
        # Already showing empty state.
        if self._results_empty is not None and self._results_empty.winfo_exists():
            return
        try:
            self.results.grid_forget()
        except Exception:
            pass
        wrap = ctk.CTkFrame(host, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        self._results_empty = wrap
        empty_state(
            wrap,
            self.theme,
            icon="archive",
            title="No backups yet",
            message="Run your first backup to populate history and recovery options.",
            action_text="Run backup",
            action_command=self._focus_run_backup,
            tone="info",
        )

    def _is_mongodb_session(self) -> bool:
        return self._active_db_type() == DatabaseType.MONGODB

    def _database_hint_text(self) -> str:
        db_type = self._active_db_type()
        if db_type == DatabaseType.MONGODB:
            return "mongodump: choose All databases for a full server backup, or one database name."
        if db_type == DatabaseType.POSTGRESQL:
            return "pg_dump backs up the selected PostgreSQL database."
        if db_type == DatabaseType.MYSQL:
            return "mysqldump backs up the selected MySQL database."
        if db_type == DatabaseType.MSSQL:
            return "BACKUP DATABASE writes a native .bak of the selected SQL Server database."
        if db_type == DatabaseType.ORACLE:
            return "expdp exports the selected Oracle database."
        return "Select the database this backup command should use."

    def _initial_database_value(self) -> str:
        if self.connection and (self.connection.database or "").strip():
            return self.connection.database.strip()
        if self.connection and self.connection.db_type == DatabaseType.MONGODB:
            return ALL_DATABASES_LABEL
        return SELECT_DATABASE_LABEL

    def on_connection_changed(self) -> None:
        """Switch the backup command and database list to the server just connected."""
        if not hasattr(self, "database"):
            return
        active = self.get_active_db_type()
        self.db_type.configure(state="normal")
        self.db_type.set(active.value)
        if self.connection:
            self.db_type.configure(state="disabled")
            self.connection_value.configure(text=self.connection.name)
        else:
            self.connection_value.configure(text="default")
        initial = self._initial_database_value()
        self.database.configure(values=[initial])
        self.database.set(initial)
        self.database_hint.configure(text=self._database_hint_text())
        self._mongodb_collections = []
        self._mongo_size_bytes = 0
        self._refresh_methods()
        self._populate_database_dropdown()

    def _merged_ssh_settings(self) -> dict[str, str | int | bool]:
        """SSH settings from the active connection, with saved-profile fallback."""
        saved = self._find_saved_server()
        c = self.connection
        ssh_enabled = bool(c and c.ssh_enabled) or bool(saved and saved.ssh_enabled)
        if not ssh_enabled:
            return {"ssh_enabled": False}

        def pick(conn_val: str | int | None, saved_val: str | int | None, default: str | int = "") -> str | int:
            if conn_val not in (None, ""):
                return conn_val
            if saved_val not in (None, ""):
                return saved_val
            return default

        host = str(pick(c.host if c else "", saved.host if saved else "", ""))
        endpoint = c.endpoint_host if c else host
        return {
            "ssh_enabled": True,
            "ssh_host": str(pick(c.ssh_host if c else None, saved.ssh_host if saved else None, endpoint)),
            "fallback_host": endpoint,
            "ssh_port": int(pick(c.ssh_port if c else None, saved.ssh_port if saved else None, 22)),
            "linux_username": str(pick(c.linux_username if c else None, saved.linux_username if saved else None, "")),
            "linux_password": str(pick(c.linux_password if c else None, saved.linux_password if saved else None, "")),
            "remote_backup_dir": str(
                pick(
                    getattr(c, "remote_backup_dir", "") if c else "",
                    saved.remote_backup_dir if saved else "",
                    "/mnt/backup",
                )
            ),
            "mongodump_path": str(
                pick(
                    getattr(c, "mongodump_path", "") if c else "",
                    saved.mongodump_path if saved else "",
                    "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump",
                )
            ),
        }

    def _ssh_available(self) -> bool:
        settings = self._merged_ssh_settings()
        return bool(
            settings.get("ssh_enabled")
            and settings.get("linux_username")
            and settings.get("ssh_host")
        )

    def _toggle_other_ssh_password_visibility(self) -> None:
        self._other_ssh_password_visible = not self._other_ssh_password_visible
        self.other_ssh_password.configure(show="" if self._other_ssh_password_visible else "*")
        self.other_ssh_password_toggle.configure(
            text="🙈" if self._other_ssh_password_visible else "👁"
        )

    def _other_ssh_ready(self) -> bool:
        return bool(
            self.other_ssh_host.get().strip()
            and self.other_ssh_user.get().strip()
            and self.other_ssh_password.get().strip()
        )

    def _read_other_ssh_fields(self) -> dict:
        """Read remote Linux SSH settings from the form (main thread only)."""
        host = self.other_ssh_host.get().strip()
        port_raw = self.other_ssh_port.get().strip() or "22"
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise RuntimeError("SSH port must be a number (e.g. 22).") from exc
        return {
            "ssh_host": host,
            "fallback_host": host,
            "ssh_port": port,
            "username": self.other_ssh_user.get().strip(),
            "password": self.other_ssh_password.get().strip(),
            "remote_backup_dir": self.other_dir_entry.get().strip() or "/mnt/backup",
            "mongodump_path": self.remote_binary.get().strip()
            or "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump",
        }

    def _other_ssh_options(self) -> dict:
        """SSH options for remote Linux server — safe for background threads when cached."""
        if self._cached_other_ssh:
            return dict(self._cached_other_ssh)
        return self._read_other_ssh_fields()

    @staticmethod
    def _verify_ssh_dict(ssh: dict) -> None:
        from backend.services.ssh_client import _ssh_kwargs, run_ssh_command

        host = ssh.get("ssh_host")
        user = ssh.get("username")
        code, out, err = run_ssh_command(
            command="echo db_autopro_ssh_ok",
            timeout=30,
            **_ssh_kwargs(ssh),
        )
        if code != 0 or "db_autopro_ssh_ok" not in (out or ""):
            detail = (err or out or f"exit {code}").strip()
            raise RuntimeError(
                f"Could not connect to Linux server {user}@{host} via SSH.\n{detail}"
            )

    def _verify_other_ssh_connection(self, ssh: dict | None = None) -> None:
        self._verify_ssh_dict(ssh or self._read_other_ssh_fields())

    def _ssh_hint_hosts(self) -> list[str]:
        hints: list[str] = []
        settings = self._merged_ssh_settings()
        if settings.get("ssh_enabled") and settings.get("ssh_host"):
            hints.append(str(settings["ssh_host"]))
        saved = self._find_saved_server()
        if saved and saved.ssh_host:
            hints.append(str(saved.ssh_host))
        seen: set[str] = set()
        unique: list[str] = []
        for host in hints:
            host = host.strip()
            if host and host not in seen:
                seen.add(host)
                unique.append(host)
        return unique

    def _enhance_ssh_error_message(self, message: str, host: str) -> str:
        hints = [h for h in self._ssh_hint_hosts() if h != host.strip()]
        if hints and any(
            token in message.lower() for token in ("timed out", "unreachable", "refused", "failed")
        ):
            saved = self._find_saved_server()
            name = saved.name if saved else "your connection"
            return (
                f"{message}\n\n"
                f"Tip: Saved connection '{name}' uses SSH host {hints[0]}. "
                f"Click 'Use connection SSH' or enter that IP instead of {host}."
            )
        return message

    def _apply_connection_ssh_to_other_form(self) -> None:
        settings = self._merged_ssh_settings()
        if not settings.get("ssh_enabled"):
            self.status_label.configure(
                text="No SSH on the active connection. Enable SSH Tunnel on Servers, save, and Connect.",
                text_color=self.theme.c("error"),
            )
            return
        self._reset_other_ssh_state()
        self.other_ssh_host.delete(0, "end")
        self.other_ssh_host.insert(0, str(settings.get("ssh_host") or ""))
        self.other_ssh_port.delete(0, "end")
        self.other_ssh_port.insert(0, str(settings.get("ssh_port") or 22))
        self.other_ssh_user.delete(0, "end")
        self.other_ssh_user.insert(0, str(settings.get("linux_username") or ""))
        self.other_ssh_password.delete(0, "end")
        self.other_ssh_password.insert(0, str(settings.get("linux_password") or ""))
        remote_dir = str(settings.get("remote_backup_dir") or "/mnt/backup")
        self._remote_output_dir = remote_dir
        self.other_dir_entry.delete(0, "end")
        self.other_dir_entry.insert(0, remote_dir)
        host = self.other_ssh_host.get().strip()
        self.other_connect_hint.configure(
            text=f"Filled from connection SSH ({host}) — click Connect & Browse."
        )
        self.status_label.configure(
            text=f"SSH fields filled from connection — {host}",
            text_color=self.theme.c("text_muted"),
        )
        self._update_ssh_status_label()
        self._update_command_preview()

    def _prefill_other_ssh_if_empty(self) -> None:
        if self.other_ssh_host.get().strip():
            return
        self._apply_connection_ssh_to_other_form()

    def _reset_other_ssh_state(self) -> None:
        self._other_ssh_connected = False
        self._other_connected_host = ""
        self._cached_other_ssh = None
        if hasattr(self, "other_ssh_status"):
            self.other_ssh_status.grid_remove()
        if hasattr(self, "other_browse_section"):
            self.other_browse_section.grid_remove()
        if self._other_browser:
            self._other_browser.set_enabled(False)

    def _on_other_ssh_changed(self) -> None:
        if self._selected_destination() != "Remote Linux Server":
            return
        self._reset_other_ssh_state()
        ready = self._other_ssh_ready()
        if hasattr(self, "other_connect_hint"):
            if ready:
                self.other_connect_hint.configure(
                    text="Credentials ready — click Connect & Browse."
                )
            else:
                self.other_connect_hint.configure(
                    text="Fill host, username, and password, then click Connect & Browse."
                )
        self._update_ssh_status_label()
        self._update_command_preview()

    def _connect_other_server(self) -> None:
        if not self._other_ssh_ready():
            if self._other_browser:
                self._other_browser.status_label.configure(
                    text="Error: Enter SSH host, username, and password first."
                )
            return

        try:
            ssh = self._read_other_ssh_fields()
        except Exception as exc:
            self._on_other_ssh_failed(str(exc))
            return

        host = ssh["ssh_host"]
        self.other_ssh_status.configure(fg_color=self.theme.c("bg_elevated"))
        self.other_ssh_status_label.configure(
            text=f"Connecting to {host}...",
            text_color=self.theme.c("text_muted"),
        )
        self.other_ssh_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.other_browse_section.grid_remove()
        self.status_label.configure(
            text=f"Connecting to Remote Linux Server {host} via SSH...",
            text_color=self.theme.c("text_muted"),
        )

        def task(ssh_opts: dict = ssh, connect_host: str = host) -> None:
            try:
                self._verify_other_ssh_connection(ssh_opts)
                self.after(0, lambda: self._on_other_ssh_connected(connect_host, ssh_opts))
            except Exception as exc:
                self.after(0, lambda msg=str(exc), err=exc: self._on_other_ssh_failed(msg, err))

        threading.Thread(target=task, daemon=True).start()

    def _on_other_ssh_connected(self, host: str, ssh: dict) -> None:
        self._other_ssh_connected = True
        self._other_connected_host = host
        self._cached_other_ssh = dict(ssh)
        self._ensure_remote_path_default()
        remote_dir = self.other_dir_entry.get().strip() or self._remote_output_dir or "/mnt/backup"
        self._cached_other_ssh["remote_backup_dir"] = remote_dir
        self.other_ssh_status.configure(fg_color=self.theme.c("success_muted"))
        self.other_ssh_status_label.configure(
            text=f"Connected to the server — {host}",
            text_color=self.theme.c("success"),
        )
        self.other_ssh_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.other_connect_hint.configure(text="Connected — browse and choose a backup folder below.")
        self.other_browse_section.grid(row=6, column=0, columnspan=2, sticky="ew")
        self.other_dir_entry.configure(state="normal")
        self.status_label.configure(
            text=f"Connected to the server — {host}",
            text_color=self.theme.c("success"),
        )
        if self._other_browser:
            self._other_browser.set_enabled(True)
            path = remote_dir or "/"
            self._other_browser.load_directory(path)
        self._update_ssh_status_label()
        self._update_command_preview()

    def _on_other_ssh_failed(self, message: str, host_key_error: Exception | None = None) -> None:
        host = self.other_ssh_host.get().strip()
        message = self._enhance_ssh_error_message(message, host)
        short = message.split("\n", 1)[0]
        self._other_ssh_connected = False
        self._other_connected_host = ""
        self._cached_other_ssh = None
        self.other_ssh_status.configure(fg_color=self.theme.c("error_muted"))
        self.other_ssh_status_label.configure(
            text=f"Connection failed: {short}",
            text_color=self.theme.c("error"),
            wraplength=520,
            justify="left",
        )
        self.other_ssh_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.other_browse_section.grid_remove()
        if self._other_browser:
            self._other_browser.set_enabled(False)
        self.status_label.configure(text=short, text_color=self.theme.c("error"))
        if hasattr(self, "results"):
            self.results.delete("1.0", "end")
            self.results.insert("end", f"SSH connection failed:\n{message}\n")
        self._update_ssh_status_label()
        self._update_command_preview()
        if host_key_error is not None:
            from desktop.ssh_host_trust import maybe_offer_host_key_trust

            maybe_offer_host_key_trust(self, host_key_error)

    def _on_other_path_selected(self, path: str) -> None:
        self.other_dir_entry.delete(0, "end")
        self.other_dir_entry.insert(0, path)
        self._remote_output_dir = path
        if self._cached_other_ssh:
            self._cached_other_ssh["remote_backup_dir"] = path
        self._update_command_preview()

    def _on_other_dir_typed(self, _event=None) -> None:
        path = self.other_dir_entry.get().strip()
        if path:
            self._remote_output_dir = path
            if self._cached_other_ssh:
                self._cached_other_ssh["remote_backup_dir"] = path
        self._update_command_preview()

    def _find_saved_server(self) -> SavedServer | None:
        if not self.connection:
            return None
        lookup = self.connection.saved_name or self.connection.name
        db_type = self._active_db_type().value
        for server in load_saved_servers(self.owner_username):
            if server.name == lookup and server.db_type == db_type:
                return server
        return None

    def _save_backup_prefs(self) -> None:
        if not self.connection:
            return
        server = self._find_saved_server()
        if not server:
            return
        server.local_output_dir = self.output_dir_entry.get().strip()
        server.remote_backup_dir = self.remote_dir_entry.get().strip()
        if self._is_mongodb_session():
            server.mongodump_path = self.remote_binary.get().strip()
            server.backup_folder_name = self.backup_folder_entry.get().strip()
            coll = self._selected_collection()
            server.backup_collection = coll or ""
        upsert_server(server, self.owner_username)

    def _ssh_options(self) -> dict:
        settings = self._merged_ssh_settings()
        if not settings.get("ssh_enabled") or not settings.get("linux_username"):
            return {}
        return {
            "ssh_host": settings["ssh_host"],
            "fallback_host": settings["fallback_host"],
            "ssh_port": int(settings["ssh_port"]),
            "username": settings["linux_username"],
            "password": settings["linux_password"],
            "mongodump_path": settings["mongodump_path"],
            "remote_backup_dir": settings["remote_backup_dir"],
        }

    def _sync_ssh_profile(self) -> None:
        """Push SSH credentials into the runtime profile used by backup services."""
        from dataclasses import replace

        from desktop.connection import apply_connection

        c = self.connection
        if not c:
            raise ValueError("Connect to a database server first.")
        settings = self._merged_ssh_settings()
        if not settings.get("ssh_enabled"):
            raise ValueError(
                "Linux Server backup needs SSH. On Servers, enable SSH Tunnel, "
                "enter Linux host/user/password, save, and Connect."
            )
        enriched = replace(
            c,
            ssh_enabled=True,
            linux_username=str(settings["linux_username"]),
            linux_password=str(settings["linux_password"]),
            ssh_host=str(settings["ssh_host"]),
            ssh_port=int(settings["ssh_port"]),
            remote_backup_dir=str(settings.get("remote_backup_dir") or "/mnt/backup"),
            mongodump_path=str(settings.get("mongodump_path") or ""),
        )
        apply_connection(enriched)

    def _verify_ssh_connection(self, ssh: dict | None = None) -> None:
        """Confirm SSH to the Linux server works before starting a remote backup."""
        if ssh is not None:
            self._verify_ssh_dict(ssh)
            return
        if self._is_other_server_backup():
            if self._cached_other_ssh:
                self._verify_ssh_dict(self._cached_other_ssh)
                return
            self._verify_other_ssh_connection(self._read_other_ssh_fields())
            return
        self._sync_ssh_profile()
        ssh_opts = self._ssh_options()
        if not ssh_opts:
            raise ValueError("SSH is not configured for this backup destination.")
        self._verify_ssh_dict(ssh_opts)

    def _test_ssh_connection(self) -> None:
        self.ssh_status_label.configure(text="Testing SSH connection...", text_color=self.theme.c("text_muted"))

        def task() -> None:
            try:
                self._verify_ssh_connection()
                if self._is_other_server_backup():
                    ssh = self._other_ssh_options()
                else:
                    ssh = self._ssh_options()
                host = ssh.get("ssh_host")
                user = ssh.get("username")
                msg = f"SSH connected: {user}@{host}"
                color = self.theme.c("success")
            except Exception as exc:
                msg = str(exc)
                color = self.theme.c("error")
                host_key_error = exc
            else:
                host_key_error = None

            def show(msg: str = msg, color: str = color, err=host_key_error) -> None:
                self.ssh_status_label.configure(text=msg, text_color=color)
                if err is not None:
                    from desktop.ssh_host_trust import maybe_offer_host_key_trust

                    maybe_offer_host_key_trust(self, err)

            self.after(0, show)

        threading.Thread(target=task, daemon=True).start()

    def _update_ssh_status_label(self) -> None:
        if not hasattr(self, "ssh_status_label"):
            return
        if self._is_other_server_backup():
            if self._other_ssh_connected:
                host = self._other_connected_host or self.other_ssh_host.get().strip()
                self.ssh_status_label.configure(
                    text=f"Connected to the server — {host}",
                    text_color=self.theme.c("success"),
                )
            elif self._other_ssh_ready():
                host = self.other_ssh_host.get().strip()
                user = self.other_ssh_user.get().strip()
                self.ssh_status_label.configure(
                    text=f"Remote Linux SSH: {user}@{host} — click Connect & Browse to verify",
                    text_color=self.theme.c("text_muted"),
                )
            else:
                self.ssh_status_label.configure(
                    text="Enter SSH host, username, and password for the Remote Linux Server.",
                    text_color=self.theme.c("text_muted"),
                )
            return
        if not self._ssh_available():
            self.ssh_status_label.configure(
                text=(
                    "SSH not configured — enable SSH Tunnel on Servers, save, and Connect "
                    "to use Linux Server backup."
                ),
                text_color=self.theme.c("text_muted"),
            )
            return
        ssh = self._ssh_options()
        host = ssh.get("ssh_host", "?")
        user = ssh.get("username", "?")
        port = ssh.get("ssh_port", 22)
        self.ssh_status_label.configure(
            text=f"Linux SSH: {user}@{host}:{port} — click Test SSH to verify",
            text_color=self.theme.c("text_muted"),
        )

    def _ensure_remote_path_default(self) -> None:
        """Fill Server path when a Linux destination is selected and the field is empty."""
        if self._is_other_server_backup():
            entry = self.other_dir_entry
        elif hasattr(self, "remote_dir_entry"):
            entry = self.remote_dir_entry
        else:
            return
        current = entry.get().strip()
        if current:
            return
        default = (self._remote_output_dir or "/mnt/backup").strip() or "/mnt/backup"
        entry.delete(0, "end")
        entry.insert(0, default)
        self._remote_output_dir = default

    def _on_remote_path_selected(self, path: str) -> None:
        self.remote_dir_entry.delete(0, "end")
        self.remote_dir_entry.insert(0, path)
        self._remote_output_dir = path
        self._update_command_preview()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        from desktop.theme import themed_scrollable_frame

        scroll = themed_scrollable_frame(self, self.theme)
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)

        context = self.create_connection_context(scroll)
        if context:
            context.pack(fill="x", pady=(0, 8))

        from desktop.ops_widgets import page_header

        page_header(
            scroll,
            self.theme,
            "Backup",
            subtitle="Create and manage database backups for the active connection.",
            pady=(0, 12),
        )

        form = self.card_frame(scroll)
        form.pack(fill="x", pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)

        active_type = self.get_active_db_type()
        ctk.CTkLabel(form, text="Database Type:").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        self.db_type = ctk.CTkComboBox(form, values=self.DB_TYPES)
        self.db_type.set(active_type.value)
        self.db_type.grid(row=0, column=1, padx=16, pady=10, sticky="ew")
        if self.connection:
            self.db_type.configure(state="disabled")

        ctk.CTkLabel(form, text="Connection:").grid(row=1, column=0, padx=16, pady=10, sticky="w")
        conn_label = self.connection.name if self.connection else "default"
        self.connection_value = ctk.CTkLabel(form, text=conn_label)
        self.connection_value.grid(row=1, column=1, padx=16, pady=10, sticky="w")

        self.database_label = ctk.CTkLabel(form, text="Database:")
        self.database_label.grid(row=2, column=0, padx=16, pady=10, sticky="nw")
        db_col = ctk.CTkFrame(form, fg_color="transparent")
        db_col.grid(row=2, column=1, padx=16, pady=10, sticky="ew")
        db_col.grid_columnconfigure(0, weight=1)

        initial_db = self._initial_database_value()
        self.database = ctk.CTkComboBox(
            db_col,
            values=[initial_db],
            command=self._on_database_changed,
        )
        self.database.set(initial_db)
        self.database.pack(fill="x")

        self.database_hint = ctk.CTkLabel(
            db_col,
            text=self._database_hint_text(),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.database_hint.pack(anchor="w", pady=(6, 0))

        self._populate_database_dropdown()

        ctk.CTkLabel(form, text="Backup Type:").grid(row=3, column=0, padx=16, pady=10, sticky="nw")
        type_frame = ctk.CTkFrame(form, fg_color="transparent")
        type_frame.grid(row=3, column=1, padx=16, pady=10, sticky="ew")
        type_frame.grid_columnconfigure(0, weight=1)

        self.category_seg = ctk.CTkSegmentedButton(
            type_frame,
            values=["Logical", "Physical"],
            command=self._on_category_changed,
        )
        self.category_seg.set("Logical")
        self.category_seg.grid(row=0, column=0, sticky="w")

        self.category_hint = ctk.CTkLabel(
            type_frame,
            text=category_description(BackupCategory.LOGICAL),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        )
        self.category_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.scope_label = ctk.CTkLabel(form, text="Backup Scope:")
        self.scope_label.grid(row=4, column=0, padx=16, pady=10, sticky="nw")
        self.scope_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.scope_frame.grid(row=4, column=1, padx=16, pady=10, sticky="ew")
        self.scope_frame.grid_columnconfigure(0, weight=1)

        self.scope_combo = ctk.CTkComboBox(
            self.scope_frame,
            values=[label for label, _ in PG_SCOPE_LABELS],
            command=self._on_scope_changed,
        )
        self.scope_combo.set("Entire Database")
        self.scope_combo.grid(row=0, column=0, sticky="ew")

        self.scope_hint = ctk.CTkLabel(
            self.scope_frame,
            text="Full pg_dump of the selected PostgreSQL database.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.scope_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        # Schema picker — shown only for PostgreSQL "Selected Tables".
        self.schema_pick_label = ctk.CTkLabel(self.scope_frame, text="Schema:")
        self.schema_pick_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.schema_pick_combo = ctk.CTkComboBox(
            self.scope_frame,
            values=["Select a schema first"],
            command=self._on_tables_schema_selected,
        )
        self.schema_pick_combo.set("")
        self.schema_pick_combo.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.schema_pick_hint = ctk.CTkLabel(
            self.scope_frame,
            text="Choose a schema first, then pick tables from that schema.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.schema_pick_hint.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.schema_pick_label.grid_remove()
        self.schema_pick_combo.grid_remove()
        self.schema_pick_hint.grid_remove()

        self.scope_target_label = ctk.CTkLabel(self.scope_frame, text="Scope target(s):")
        self.scope_target_label.grid(row=5, column=0, sticky="w", pady=(8, 0))

        self.scope_target_combo = ctk.CTkComboBox(
            self.scope_frame,
            values=["Loading..."],
            command=self._on_scope_target_selected,
        )
        self.scope_target_combo.set("")
        self.scope_target_combo.grid(row=6, column=0, sticky="ew", pady=(6, 0))

        self.scope_target_entry = ctk.CTkEntry(self.scope_frame)
        self.scope_target_entry.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        self.scope_target_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())
        self.scope_target_entry.grid_remove()

        self.scope_target_hint = ctk.CTkLabel(
            self.scope_frame,
            text="Choose a name from the dropdown.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.scope_target_hint.grid(row=7, column=0, sticky="ew", pady=(6, 0))

        self.file_ext_label = ctk.CTkLabel(form, text="Backup Format:")
        self.file_ext_label.grid(row=5, column=0, padx=16, pady=10, sticky="nw")
        self.format_col = ctk.CTkFrame(form, fg_color="transparent")
        self.format_col.grid(row=5, column=1, padx=16, pady=10, sticky="ew")
        self.format_col.grid_columnconfigure(0, weight=1)
        self.file_ext = ctk.CTkComboBox(
            self.format_col,
            values=[label for label, _ in PG_FORMAT_LABELS],
            command=self._on_file_extension_changed,
        )
        self.file_ext.set("Custom Format")
        self.file_ext.grid(row=0, column=0, sticky="ew")
        self.format_hint = ctk.CTkLabel(
            self.format_col,
            text=(
                "Custom Format is compressed and usually near DB size. "
                "Plain SQL is uncompressed text (.sql) and can be many times larger."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.format_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.command_label = ctk.CTkLabel(form, text="Command:")
        self.command_label.grid(row=6, column=0, padx=16, pady=10, sticky="nw")
        self.method = ctk.CTkComboBox(
            form, values=["pg_dump"], command=self._on_method_changed
        )
        self.method.set("pg_dump")
        self.method.grid(row=6, column=1, padx=16, pady=10, sticky="ew")

        self.collections_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.collections_frame.grid(row=8, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.collections_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.collections_frame, text="Collection:").grid(row=0, column=0, sticky="w", pady=6)

        coll_row = ctk.CTkFrame(self.collections_frame, fg_color="transparent")
        coll_row.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        coll_row.grid_columnconfigure(0, weight=1)

        self.collection_combo = ctk.CTkComboBox(
            coll_row,
            values=["All collections"],
            command=self._on_collection_changed,
        )
        self.collection_combo.set("All collections")
        self.collection_combo.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            coll_row,
            text="Load Collections",
            width=130,
            command=self._load_collections,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(
            self.collections_frame,
            text="Collections in selected database — click one for a single-collection backup:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 4))

        self.collections_list = ctk.CTkScrollableFrame(
            self.collections_frame,
            height=120,
            fg_color=self.theme.c("bg_elevated"),
            corner_radius=8,
        )
        self.collections_list.grid(row=2, column=0, columnspan=2, sticky="ew")

        self.collection_scope_label = ctk.CTkLabel(
            self.collections_frame,
            text="Scope: all collections in this database",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("primary"),
            anchor="w",
        )
        self.collection_scope_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.collection_size_label = ctk.CTkLabel(
            self.collections_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.collection_size_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ctk.CTkLabel(
            self.collections_frame,
            text=(
                "Click a collection to select it for a single-collection backup "
                "(adds --collection to mongodump). Choose “All collections” for the full database."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.destination_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.destination_frame.grid(row=9, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.destination_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.destination_frame, text="Backup destination:").grid(
            row=0, column=0, sticky="nw", pady=6
        )
        dest_col = ctk.CTkFrame(self.destination_frame, fg_color="transparent")
        dest_col.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        dest_col.grid_columnconfigure(0, weight=1)

        self.destination_seg = ctk.CTkSegmentedButton(
            dest_col,
            values=["Local Server", "Linux Server", "Remote Linux Server"],
            command=self._on_destination_changed,
        )
        self.destination_seg.set("Local Server")
        self.destination_seg.grid(row=0, column=0, sticky="w")

        self.destination_hint = ctk.CTkLabel(
            dest_col,
            text="Save the backup on this PC (Local Server).",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.destination_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.output_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.output_frame.grid(row=10, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.output_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.output_frame, text="Local backup path:").grid(
            row=0, column=0, sticky="w", pady=6
        )

        output_path_row = ctk.CTkFrame(self.output_frame, fg_color="transparent")
        output_path_row.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        output_path_row.grid_columnconfigure(0, weight=1)

        self.output_dir_entry = ctk.CTkEntry(
            output_path_row,
            placeholder_text="File or folder path — type or browse any backup location",
        )
        self.output_dir_entry.grid(row=0, column=0, sticky="ew")
        self.output_dir_entry.bind("<KeyRelease>", lambda _e: self._on_output_dir_changed())

        ctk.CTkButton(
            output_path_row,
            text="File",
            width=60,
            command=self._browse_backup_file,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkButton(
            output_path_row,
            text="Folder",
            width=70,
            command=self._browse_backup_folder,
        ).grid(row=0, column=2, padx=(6, 0))

        self.backup_path_hint = ctk.CTkLabel(
            self.output_frame,
            text=(
                "Pick a file or folder on this PC where the backup will be saved. "
                "No default path — select it manually each time."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.backup_path_hint.grid(row=1, column=1, padx=(12, 0), sticky="w")

        self.mongo_options_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.mongo_options_frame.grid(row=11, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.mongo_options_frame.grid_columnconfigure(1, weight=1)

        self.mongo_folder_label = ctk.CTkLabel(self.mongo_options_frame, text="Backup folder:")
        self.mongo_folder_label.grid(row=0, column=0, sticky="w", pady=6)
        self.backup_folder_entry = ctk.CTkEntry(
            self.mongo_options_frame,
            placeholder_text="e.g. db_backup or prod_db1 (timestamp added automatically)",
        )
        self.backup_folder_entry.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.backup_folder_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        self.mongo_folder_hint = ctk.CTkLabel(
            self.mongo_options_frame,
            text="Optional. Use <timestamp> in the name to control where the date is added.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        )
        self.mongo_folder_hint.grid(row=1, column=1, padx=(12, 0), sticky="w")

        self.gzip_var = ctk.BooleanVar(value=False)
        self.gzip_cb = ctk.CTkCheckBox(
            self.mongo_options_frame,
            text="Gzip archive (--gzip)",
            variable=self.gzip_var,
            command=self._update_command_preview,
        )
        self.gzip_cb.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.remote_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.remote_frame.grid(row=12, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.remote_frame.grid_columnconfigure(1, weight=1)

        self.remote_binary_label = ctk.CTkLabel(self.remote_frame, text="Mongodump binary:")
        self.remote_binary_label.grid(row=0, column=0, sticky="w", pady=6)
        self.remote_binary = ctk.CTkEntry(self.remote_frame)
        self.remote_binary.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.remote_binary.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(self.remote_frame, text="Server path:").grid(row=1, column=0, sticky="nw", pady=6)
        remote_dir_col = ctk.CTkFrame(self.remote_frame, fg_color="transparent")
        remote_dir_col.grid(row=1, column=1, padx=(12, 0), pady=6, sticky="ew")
        remote_dir_col.grid_columnconfigure(0, weight=1)

        self.remote_dir_entry = ctk.CTkEntry(
            remote_dir_col,
            placeholder_text="Linux folder on the server, e.g. /mnt/backup",
        )
        self.remote_dir_entry.grid(row=0, column=0, sticky="ew")
        self.remote_dir_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ssh_row = ctk.CTkFrame(self.remote_frame, fg_color="transparent")
        ssh_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ssh_row.grid_columnconfigure(0, weight=1)

        self.ssh_status_label = ctk.CTkLabel(
            ssh_row,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.ssh_status_label.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            ssh_row,
            text="Test SSH",
            width=90,
            command=self._test_ssh_connection,
        ).grid(row=0, column=1, padx=(8, 0), sticky="e")

        ctk.CTkLabel(
            self.remote_frame,
            text="Browse folders on the Linux server",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 4))

        self._remote_browser: RemoteDirectoryBrowser | None = None
        browser_host = ctk.CTkFrame(self.remote_frame, fg_color="transparent")
        browser_host.grid(row=4, column=0, columnspan=2, sticky="ew")
        browser_host.grid_columnconfigure(0, weight=1)
        self._remote_browser = RemoteDirectoryBrowser(
            browser_host,
            self.theme,
            get_ssh=self._ssh_options,
            initial_path=self._remote_output_dir,
            on_selection_changed=self._on_remote_path_selected,
        )
        self._remote_browser.grid(row=0, column=0, sticky="ew")

        self.remote_path_hint = ctk.CTkLabel(
            self.remote_frame,
            text=(
                "Select a folder on the Linux server where the backup file will be written. "
                "Requires SSH enabled on the connection."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.remote_path_hint.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.other_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.other_frame.grid(row=13, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.other_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.other_frame, text="Linux host:").grid(row=0, column=0, sticky="w", pady=6)
        self.other_ssh_host = ctk.CTkEntry(self.other_frame, placeholder_text="e.g. 192.168.1.50")
        self.other_ssh_host.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.other_ssh_host.bind("<KeyRelease>", lambda _e: self._on_other_ssh_changed())

        ctk.CTkLabel(self.other_frame, text="SSH port:").grid(row=1, column=0, sticky="w", pady=6)
        self.other_ssh_port = ctk.CTkEntry(self.other_frame, placeholder_text="22")
        self.other_ssh_port.insert(0, "22")
        self.other_ssh_port.grid(row=1, column=1, padx=(12, 0), pady=6, sticky="ew")

        ctk.CTkLabel(self.other_frame, text="Linux username:").grid(row=2, column=0, sticky="w", pady=6)
        self.other_ssh_user = ctk.CTkEntry(
            self.other_frame, placeholder_text="e.g. ubuntu, root, techadmin"
        )
        self.other_ssh_user.grid(row=2, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.other_ssh_user.bind("<KeyRelease>", lambda _e: self._on_other_ssh_changed())

        ctk.CTkLabel(self.other_frame, text="Linux password:").grid(row=3, column=0, sticky="w", pady=6)
        other_pw_row = ctk.CTkFrame(self.other_frame, fg_color="transparent")
        other_pw_row.grid(row=3, column=1, padx=(12, 0), pady=6, sticky="ew")
        other_pw_row.grid_columnconfigure(0, weight=1)
        self.other_ssh_password = ctk.CTkEntry(
            other_pw_row, placeholder_text="password", show="*"
        )
        self.other_ssh_password.grid(row=0, column=0, sticky="ew")
        self.other_ssh_password.bind("<KeyRelease>", lambda _e: self._on_other_ssh_changed())
        self.other_ssh_password_toggle = ctk.CTkButton(
            other_pw_row,
            text="👁",
            width=40,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_primary"),
            border_width=1,
            border_color=self.theme.c("border"),
            font=ctk.CTkFont(size=16),
            command=self._toggle_other_ssh_password_visibility,
        )
        self.other_ssh_password_toggle.grid(row=0, column=1, padx=(8, 0))

        other_connect_row = ctk.CTkFrame(self.other_frame, fg_color="transparent")
        other_connect_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        ctk.CTkButton(
            other_connect_row,
            text="Connect & Browse",
            width=150,
            command=self._connect_other_server,
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
        ).pack(side="left")
        ctk.CTkButton(
            other_connect_row,
            text="Use connection SSH",
            width=150,
            command=self._apply_connection_ssh_to_other_form,
        ).pack(side="left", padx=(8, 0))
        self.other_connect_hint = ctk.CTkLabel(
            other_connect_row,
            text="Fill host, username, and password, then click Connect & Browse.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        )
        self.other_connect_hint.pack(side="left", padx=(12, 0))

        self.other_ssh_status = ctk.CTkFrame(
            self.other_frame,
            fg_color=self.theme.c("success_muted"),
            corner_radius=8,
        )
        self.other_ssh_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.other_ssh_status.grid_columnconfigure(0, weight=1)
        self.other_ssh_status_label = ctk.CTkLabel(
            self.other_ssh_status,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("success"),
            anchor="w",
            justify="left",
        )
        self.other_ssh_status_label.grid(row=0, column=0, padx=12, pady=10, sticky="ew")
        self.other_ssh_status.grid_remove()

        self.other_browse_section = ctk.CTkFrame(self.other_frame, fg_color="transparent")
        self.other_browse_section.grid(row=6, column=0, columnspan=2, sticky="ew")
        self.other_browse_section.grid_columnconfigure(1, weight=1)
        self.other_browse_section.grid_remove()

        ctk.CTkLabel(self.other_browse_section, text="Server path:").grid(
            row=0, column=0, sticky="nw", pady=6
        )
        other_dir_col = ctk.CTkFrame(self.other_browse_section, fg_color="transparent")
        other_dir_col.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        other_dir_col.grid_columnconfigure(0, weight=1)

        self.other_dir_entry = ctk.CTkEntry(
            other_dir_col,
            placeholder_text="Folder on the Remote Linux Server, e.g. /mnt/backup",
        )
        self.other_dir_entry.grid(row=0, column=0, sticky="ew")
        self.other_dir_entry.bind("<KeyRelease>", self._on_other_dir_typed)

        ctk.CTkLabel(
            self.other_browse_section,
            text="Browse Remote Linux Server",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 4))

        other_browser_host = ctk.CTkFrame(self.other_browse_section, fg_color="transparent")
        other_browser_host.grid(row=2, column=0, columnspan=2, sticky="ew")
        other_browser_host.grid_columnconfigure(0, weight=1)
        self._other_browser = RemoteDirectoryBrowser(
            other_browser_host,
            self.theme,
            get_ssh=self._other_ssh_options,
            initial_path=self._remote_output_dir,
            on_selection_changed=self._on_other_path_selected,
        )
        self._other_browser.grid(row=0, column=0, sticky="ew")
        self._other_browser.set_enabled(False)

        ctk.CTkLabel(form, text="Preview:").grid(row=14, column=0, padx=16, pady=10, sticky="nw")
        self.command_preview = ctk.CTkTextbox(form, height=88, activate_scrollbars=True)
        self.command_preview.grid(row=14, column=1, padx=16, pady=10, sticky="ew")
        self.command_preview.configure(state="disabled")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=15, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="ew")

        # Same charcoal/primary CTA as Migration "Start Migration".
        primary_button(
            btn_frame,
            self.theme,
            text="Run Backup",
            command=self._run_backup,
            width=140,
            height=38,
        ).pack(side="left", padx=(0, 10))

        # Apply Retention — disabled in UI for now; backend apply_retention_policy() kept for future use.

        secondary_button(
            btn_frame,
            self.theme,
            text="List Backups",
            command=self._list_backups,
            width=120,
            height=38,
        ).pack(side="left")

        progress_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        progress_frame.pack(fill="x", pady=(0, 8))

        self.progress = ctk.CTkProgressBar(progress_frame)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(0, 6))

        self.status_label = ctk.CTkLabel(progress_frame, text="Ready", font=ctk.CTkFont(size=12))
        self.status_label.pack(anchor="w")

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

        self._results_host = ctk.CTkFrame(results_card, fg_color="transparent")
        self._results_host.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        self._results_host.grid_columnconfigure(0, weight=1)
        self._results_host.grid_rowconfigure(0, weight=1)

        self.results = ctk.CTkTextbox(self._results_host, height=160, activate_scrollbars=True)
        self.results.grid(row=0, column=0, sticky="nsew")

    def _active_db_type(self) -> DatabaseType:
        if self.connection:
            return self.get_active_db_type()
        return DatabaseType(self.db_type.get())

    def _on_category_changed(self, value: str) -> None:
        self._category = BackupCategory.PHYSICAL if value == "Physical" else BackupCategory.LOGICAL
        self.category_hint.configure(text=category_description(self._category))
        self._refresh_methods()

    def _on_file_extension_changed(self, _value: str | None = None) -> None:
        self._update_command_preview()

    def _show_pg_scope(self) -> bool:
        return (
            self._active_db_type() == DatabaseType.POSTGRESQL
            and self._category == BackupCategory.LOGICAL
            and self._selected_method_value() == BackupMethod.PG_DUMP.value
        )

    def _show_mysql_scope(self) -> bool:
        return (
            self._active_db_type() == DatabaseType.MYSQL
            and self._category == BackupCategory.LOGICAL
            and self._selected_method_value() == BackupMethod.MYSQLDUMP.value
        )

    def _show_logical_scope(self) -> bool:
        return self._show_pg_scope() or self._show_mysql_scope()

    def _selected_pg_scope(self) -> PgBackupScope:
        label = self.scope_combo.get()
        return PG_SCOPE_LABEL_TO_VALUE.get(label, PgBackupScope.ENTIRE_DATABASE)

    def _selected_mysql_scope(self) -> MySQLBackupScope:
        label = self.scope_combo.get()
        return MYSQL_SCOPE_LABEL_TO_VALUE.get(label, MySQLBackupScope.ENTIRE_DATABASE)

    def _pg_scope_requires_targets(self, scope: PgBackupScope) -> bool:
        return scope in (
            PgBackupScope.SINGLE_SCHEMA,
            PgBackupScope.MULTIPLE_SCHEMAS,
            PgBackupScope.SELECTED_TABLES,
            PgBackupScope.SELECTED_OBJECTS,
        )

    def _mysql_scope_requires_targets(self, scope: MySQLBackupScope) -> bool:
        return scope == MySQLBackupScope.SELECTED_TABLES

    def _pg_scope_uses_advanced_args(self, scope: PgBackupScope) -> bool:
        return scope == PgBackupScope.SELECTED_OBJECTS

    def _pg_scope_hint_text(self, scope: PgBackupScope) -> tuple[str, str, str]:
        if scope == PgBackupScope.SINGLE_SCHEMA:
            return (
                "Back up one schema from the selected PostgreSQL database.",
                "Schema:",
                "Choose a schema from the dropdown.",
            )
        if scope == PgBackupScope.MULTIPLE_SCHEMAS:
            return (
                "Back up multiple schemas from the selected PostgreSQL database.",
                "Schemas:",
                "Choose schemas from the dropdown. Pick again to add another; all chosen names stay shown.",
            )
        if scope == PgBackupScope.SELECTED_TABLES:
            return (
                "Back up only the selected tables. Choose a schema first, then tables.",
                "Tables:",
                "Choose tables from the selected schema. Pick again to add another; all chosen names stay shown.",
            )
        if scope == PgBackupScope.DATA_ONLY:
            return (
                "Back up row data only; table definitions are excluded.",
                "",
                "",
            )
        if scope == PgBackupScope.SCHEMA_ONLY:
            return (
                "Back up schema definitions only; table data is excluded.",
                "",
                "",
            )
        if scope == PgBackupScope.SELECTED_OBJECTS:
            return (
                "Advanced pg_dump object filters. Use raw pg_dump selector arguments.",
                "Advanced pg_dump args:",
                "Example: --table=public.users --exclude-table-data=public.audit_log",
            )
        return (
            "Full pg_dump of the selected PostgreSQL database.",
            "",
            "",
        )

    def _mysql_scope_hint_text(self, scope: MySQLBackupScope) -> tuple[str, str, str]:
        if scope == MySQLBackupScope.SELECTED_TABLES:
            return (
                "Back up only the selected tables from the selected MySQL database.",
                "Tables:",
                "Choose tables from the dropdown. Pick again to add another; all chosen names stay shown.",
            )
        if scope == MySQLBackupScope.DATA_ONLY:
            return (
                "Back up row data only (--no-create-info); CREATE TABLE statements are excluded.",
                "",
                "",
            )
        if scope == MySQLBackupScope.SCHEMA_ONLY:
            return (
                "Back up schema definitions only (--no-data); table data is excluded.",
                "",
                "",
            )
        return (
            "Full mysqldump of the selected MySQL database.",
            "",
            "",
        )

    def _parse_pg_scope_targets(self) -> list[str]:
        scope = self._selected_pg_scope()
        if scope == PgBackupScope.SELECTED_OBJECTS:
            return []
        if scope == PgBackupScope.SELECTED_TABLES:
            if self._scope_selected_targets:
                return [self._qualify_selected_table(t) for t in self._scope_selected_targets]
            selected = self.scope_target_combo.get().strip()
            if selected and selected not in (
                "Loading...",
                "No tables found",
                "Select a database first",
                "Select a schema first",
                "Failed to load names",
            ):
                # Multi display or single unqualified name
                parts = [item.strip() for item in selected.split(",") if item.strip()]
                return [self._qualify_selected_table(p) for p in parts]
            return []
        if self._scope_selected_targets:
            return list(self._scope_selected_targets)
        selected = self.scope_target_combo.get().strip()
        if selected and selected not in (
            "Loading...",
            "No schemas found",
            "No tables found",
            "Select a database first",
            "Select a schema first",
            "Failed to load names",
        ):
            return [item.strip() for item in selected.split(",") if item.strip()]
        return []

    def _parse_mysql_scope_targets(self) -> list[str]:
        if self._scope_selected_targets:
            return list(self._scope_selected_targets)
        selected = self.scope_target_combo.get().strip()
        if selected and selected not in (
            "Loading...",
            "No tables found",
            "Select a database first",
            "Failed to load names",
        ):
            return [item.strip() for item in selected.split(",") if item.strip()]
        return []

    def _on_scope_changed(self, _value: str | None = None) -> None:
        self._scope_selected_targets = []
        self._tables_schema = None
        self._schema_available_names = []
        self._sync_scope_visibility()
        self._load_scope_targets()
        self._update_command_preview()

    def _on_pg_scope_changed(self, _value: str | None = None) -> None:
        # Backward-compatible alias
        self._on_scope_changed(_value)

    def _selected_tables_schema(self) -> str | None:
        if not hasattr(self, "schema_pick_combo"):
            return self._tables_schema
        value = self.schema_pick_combo.get().strip()
        if value in (
            "",
            "Loading...",
            "No schemas found",
            "Select a database first",
            "Select a schema first",
            "Failed to load names",
        ):
            return None
        return value

    def _qualify_selected_table(self, table_name: str) -> str:
        """Return schema.table for pg_dump --table=."""
        name = table_name.strip()
        if not name:
            return name
        if "." in name:
            return name
        schema = self._selected_tables_schema() or self._tables_schema or "public"
        return f"{schema}.{name}"

    def _on_tables_schema_selected(self, value: str | None = None) -> None:
        selected = (value if value is not None else self.schema_pick_combo.get()).strip()
        if selected in (
            "",
            "Loading...",
            "No schemas found",
            "Select a database first",
            "Select a schema first",
            "Failed to load names",
        ):
            return
        if selected == self._tables_schema and self._scope_available_names:
            return
        self._tables_schema = selected
        self._scope_selected_targets = []
        self._load_tables_for_schema(selected)
        self._update_command_preview()

    def _on_scope_target_selected(self, value: str | None = None) -> None:
        selected = (value if value is not None else self.scope_target_combo.get()).strip()
        if not selected or selected in (
            "Loading...",
            "No schemas found",
            "No tables found",
            "Select a database first",
            "Select a schema first",
            "Failed to load names",
            "",
        ):
            return

        if "," in selected and selected not in self._scope_available_names:
            return

        if self._show_mysql_scope():
            scope = self._selected_mysql_scope()
            if scope == MySQLBackupScope.SELECTED_TABLES:
                if selected not in self._scope_selected_targets:
                    self._scope_selected_targets.append(selected)
                self.scope_target_combo.set(", ".join(self._scope_selected_targets))
            else:
                self._scope_selected_targets = [selected]
                self.scope_target_combo.set(selected)
            self._update_command_preview()
            return

        scope = self._selected_pg_scope()
        if scope == PgBackupScope.SINGLE_SCHEMA:
            self._scope_selected_targets = [selected]
            self.scope_target_combo.set(selected)
        elif scope == PgBackupScope.SELECTED_TABLES:
            qualified = self._qualify_selected_table(selected)
            if qualified not in self._scope_selected_targets:
                self._scope_selected_targets.append(qualified)
            # Show short table names in the combo when they belong to the active schema.
            schema = self._selected_tables_schema() or ""
            display_parts: list[str] = []
            for item in self._scope_selected_targets:
                if schema and item.startswith(f"{schema}."):
                    display_parts.append(item[len(schema) + 1 :])
                else:
                    display_parts.append(item)
            self.scope_target_combo.set(", ".join(display_parts))
        elif scope == PgBackupScope.MULTIPLE_SCHEMAS:
            if selected not in self._scope_selected_targets:
                self._scope_selected_targets.append(selected)
            self.scope_target_combo.set(", ".join(self._scope_selected_targets))
        else:
            self._scope_selected_targets = [selected]
            self.scope_target_combo.set(selected)
        self._update_command_preview()

    def _load_scope_targets(self) -> None:
        if not self._show_logical_scope():
            return

        if self._show_mysql_scope():
            scope = self._selected_mysql_scope()
            if not self._mysql_scope_requires_targets(scope):
                return
            if not self.connection:
                self._set_scope_target_values([], placeholder="Select a database first")
                return
            db_name = self._backup_database_name()
            if not db_name:
                self._set_scope_target_values([], placeholder="Select a database first")
                return
            self._set_scope_target_values([], placeholder="Loading...")

            def mysql_task():
                try:
                    names = fetch_mysql_tables(self.connection, db_name)
                    self.after(
                        0,
                        lambda: self._set_scope_target_values(
                            names, placeholder="No tables found"
                        ),
                    )
                except Exception:
                    self.after(
                        0,
                        lambda: self._set_scope_target_values(
                            [], placeholder="Failed to load names"
                        ),
                    )

            threading.Thread(target=mysql_task, daemon=True).start()
            return

        scope = self._selected_pg_scope()
        if not self._pg_scope_requires_targets(scope) or self._pg_scope_uses_advanced_args(scope):
            return
        if not self.connection:
            if scope == PgBackupScope.SELECTED_TABLES:
                self._set_schema_pick_values([], placeholder="Select a database first")
                self._set_scope_target_values([], placeholder="Select a schema first")
            else:
                self._set_scope_target_values([], placeholder="Select a database first")
            return

        db_name = self._backup_database_name()
        if not db_name:
            if scope == PgBackupScope.SELECTED_TABLES:
                self._set_schema_pick_values([], placeholder="Select a database first")
                self._set_scope_target_values([], placeholder="Select a schema first")
            else:
                self._set_scope_target_values([], placeholder="Select a database first")
            return

        # Selected Tables: load schemas first; tables load after schema pick.
        if scope == PgBackupScope.SELECTED_TABLES:
            self._set_schema_pick_values([], placeholder="Loading...")
            self._set_scope_target_values([], placeholder="Select a schema first")

            def schema_task():
                try:
                    names = fetch_postgresql_schemas(self.connection, db_name)
                    self.after(
                        0,
                        lambda: self._apply_schema_pick_values(
                            names, placeholder="No schemas found"
                        ),
                    )
                except Exception:
                    self.after(
                        0,
                        lambda: self._set_schema_pick_values(
                            [], placeholder="Failed to load names"
                        ),
                    )

            threading.Thread(target=schema_task, daemon=True).start()
            return

        self._set_scope_target_values([], placeholder="Loading...")

        def task():
            try:
                names = fetch_postgresql_schemas(self.connection, db_name)
                empty = "No schemas found"
                self.after(0, lambda: self._set_scope_target_values(names, placeholder=empty))
            except Exception:
                self.after(
                    0,
                    lambda: self._set_scope_target_values([], placeholder="Failed to load names"),
                )

        threading.Thread(target=task, daemon=True).start()

    def _load_tables_for_schema(self, schema: str) -> None:
        if not self.connection:
            self._set_scope_target_values([], placeholder="Select a database first")
            return
        db_name = self._backup_database_name()
        if not db_name:
            self._set_scope_target_values([], placeholder="Select a database first")
            return
        self._set_scope_target_values([], placeholder="Loading...")

        def task():
            try:
                names = fetch_postgresql_tables(self.connection, db_name, schema=schema)
                self.after(
                    0,
                    lambda: self._set_scope_target_values(
                        names, placeholder="No tables found", auto_select=False
                    ),
                )
            except Exception:
                self.after(
                    0,
                    lambda: self._set_scope_target_values(
                        [], placeholder="Failed to load names", auto_select=False
                    ),
                )

        threading.Thread(target=task, daemon=True).start()

    def _set_schema_pick_values(self, names: list[str], *, placeholder: str) -> None:
        if not hasattr(self, "schema_pick_combo"):
            return
        self._schema_available_names = names
        if names:
            self.schema_pick_combo.configure(values=names)
            current = self._tables_schema if self._tables_schema in names else names[0]
            self.schema_pick_combo.set(current)
        else:
            self.schema_pick_combo.configure(values=[placeholder])
            self.schema_pick_combo.set(placeholder)
            self._tables_schema = None

    def _apply_schema_pick_values(self, names: list[str], *, placeholder: str) -> None:
        if not hasattr(self, "schema_pick_combo"):
            return
        self._schema_available_names = names
        self._scope_selected_targets = []
        if not names:
            self.schema_pick_combo.configure(values=[placeholder])
            self.schema_pick_combo.set(placeholder)
            self._tables_schema = None
            self._set_scope_target_values([], placeholder="No tables found", auto_select=False)
            return
        preferred = self._tables_schema if self._tables_schema in names else names[0]
        self._tables_schema = preferred
        self.schema_pick_combo.configure(values=names)
        self.schema_pick_combo.set(preferred)
        self._load_tables_for_schema(preferred)

    def _set_scope_target_values(
        self, names: list[str], *, placeholder: str, auto_select: bool = True
    ) -> None:
        self._scope_available_names = names
        if names:
            self.scope_target_combo.configure(values=names)
            if self._scope_selected_targets:
                display = ", ".join(self._scope_selected_targets)
                if self._selected_pg_scope() == PgBackupScope.SELECTED_TABLES:
                    schema = self._selected_tables_schema() or ""
                    display_parts = []
                    for item in self._scope_selected_targets:
                        if schema and item.startswith(f"{schema}."):
                            display_parts.append(item[len(schema) + 1 :])
                        else:
                            display_parts.append(item)
                    display = ", ".join(display_parts)
                if (
                    len(self._scope_selected_targets) == 1
                    and self._scope_selected_targets[0] in names
                ):
                    self.scope_target_combo.set(self._scope_selected_targets[0])
                else:
                    self.scope_target_combo.set(display)
            elif auto_select:
                self.scope_target_combo.set(names[0])
                # For Selected Tables, wait for explicit table picks (schema already chosen).
                if self._show_pg_scope() and self._selected_pg_scope() == PgBackupScope.SELECTED_TABLES:
                    self.scope_target_combo.set(names[0])
                    self._scope_selected_targets = []
                else:
                    self._scope_selected_targets = [names[0]]
                self._update_command_preview()
            else:
                self.scope_target_combo.set(names[0])
                self._scope_selected_targets = []
                self._update_command_preview()
        else:
            self.scope_target_combo.configure(values=[placeholder])
            self.scope_target_combo.set(placeholder)

    def _show_pg_file_extension(self) -> bool:
        return (
            self._active_db_type() == DatabaseType.POSTGRESQL
            and self._category == BackupCategory.LOGICAL
            and self._selected_method_value() == BackupMethod.PG_DUMP.value
        )

    def _show_mysql_file_extension(self) -> bool:
        return (
            self._active_db_type() == DatabaseType.MYSQL
            and self._category == BackupCategory.LOGICAL
            and self._selected_method_value() == BackupMethod.MYSQLDUMP.value
        )

    def _show_backup_format(self) -> bool:
        return self._show_pg_file_extension() or self._show_mysql_file_extension()

    def _configure_scope_format_options(self) -> None:
        """Swap scope/format dropdown values for PostgreSQL vs MySQL."""
        if not hasattr(self, "scope_combo") or not hasattr(self, "file_ext"):
            return
        if self._show_mysql_scope() or (
            self._active_db_type() == DatabaseType.MYSQL
            and self._category == BackupCategory.LOGICAL
        ):
            self.scope_combo.configure(values=[label for label, _ in MYSQL_SCOPE_LABELS])
            current_scope = self.scope_combo.get()
            if current_scope not in MYSQL_SCOPE_LABEL_TO_VALUE:
                self.scope_combo.set("Entire Database")
            self.file_ext.configure(values=[label for label, _ in MYSQL_FORMAT_LABELS])
            current_fmt = self.file_ext.get()
            if current_fmt not in MYSQL_FORMAT_LABEL_TO_VALUE:
                self.file_ext.set("Plain SQL (.sql)")
            self.format_hint.configure(
                text=(
                    "Plain SQL (.sql) is a text dump. "
                    "Compressed SQL (.sql.gz) gzips the dump after mysqldump completes."
                )
            )
        elif self._show_pg_scope() or (
            self._active_db_type() == DatabaseType.POSTGRESQL
            and self._category == BackupCategory.LOGICAL
        ):
            self.scope_combo.configure(values=[label for label, _ in PG_SCOPE_LABELS])
            current_scope = self.scope_combo.get()
            if current_scope not in PG_SCOPE_LABEL_TO_VALUE:
                self.scope_combo.set("Entire Database")
            self.file_ext.configure(values=[label for label, _ in PG_FORMAT_LABELS])
            current_fmt = self.file_ext.get()
            if current_fmt not in PG_FORMAT_LABEL_TO_VALUE:
                self.file_ext.set("Custom Format")
            self.format_hint.configure(
                text=(
                    "Custom Format is compressed and usually near DB size. "
                    "Plain SQL is uncompressed text (.sql) and can be many times larger."
                )
            )

    def _sync_scope_visibility(self) -> None:
        if not hasattr(self, "scope_combo"):
            return
        self._configure_scope_format_options()
        if not self._show_logical_scope():
            self.scope_label.grid_remove()
            self.scope_frame.grid_remove()
            self._hide_schema_pick()
            return

        self.scope_label.grid()
        self.scope_frame.grid()

        if self._show_mysql_scope():
            self._hide_schema_pick()
            scope = self._selected_mysql_scope()
            hint, target_label, target_hint = self._mysql_scope_hint_text(scope)
            self.scope_hint.configure(text=hint)
            if self._mysql_scope_requires_targets(scope):
                self.scope_target_label.grid()
                self.scope_target_hint.grid()
                self.scope_target_label.configure(text=target_label)
                self.scope_target_hint.configure(text=target_hint)
                self.scope_target_entry.grid_remove()
                self.scope_target_combo.grid()
            else:
                self.scope_target_label.grid_remove()
                self.scope_target_entry.grid_remove()
                self.scope_target_combo.grid_remove()
                self.scope_target_hint.grid_remove()
            return

        scope = self._selected_pg_scope()
        hint, target_label, target_hint = self._pg_scope_hint_text(scope)
        self.scope_hint.configure(text=hint)

        if scope == PgBackupScope.SELECTED_TABLES:
            self._show_schema_pick()
            self.scope_target_label.grid()
            self.scope_target_hint.grid()
            self.scope_target_label.configure(text=target_label)
            self.scope_target_hint.configure(text=target_hint)
            self.scope_target_entry.grid_remove()
            self.scope_target_combo.grid()
            return

        self._hide_schema_pick()
        if self._pg_scope_requires_targets(scope):
            self.scope_target_label.grid()
            self.scope_target_hint.grid()
            self.scope_target_label.configure(text=target_label)
            self.scope_target_hint.configure(text=target_hint)
            if self._pg_scope_uses_advanced_args(scope):
                self.scope_target_combo.grid_remove()
                self.scope_target_entry.grid()
            else:
                self.scope_target_entry.grid_remove()
                self.scope_target_combo.grid()
        else:
            self.scope_target_label.grid_remove()
            self.scope_target_entry.grid_remove()
            self.scope_target_combo.grid_remove()
            self.scope_target_hint.grid_remove()

    def _show_schema_pick(self) -> None:
        if not hasattr(self, "schema_pick_combo"):
            return
        self.schema_pick_label.grid()
        self.schema_pick_combo.grid()
        self.schema_pick_hint.grid()

    def _hide_schema_pick(self) -> None:
        if not hasattr(self, "schema_pick_combo"):
            return
        self.schema_pick_label.grid_remove()
        self.schema_pick_combo.grid_remove()
        self.schema_pick_hint.grid_remove()

    def _sync_pg_scope_visibility(self) -> None:
        self._sync_scope_visibility()

    def _sync_format_visibility(self) -> None:
        if not hasattr(self, "file_ext"):
            return
        self._configure_scope_format_options()
        if self._show_backup_format():
            self.file_ext_label.grid()
            self.format_col.grid()
            self.file_ext.configure(state="normal")
        else:
            self.file_ext_label.grid_remove()
            self.format_col.grid_remove()

    def _sync_pg_file_extension_visibility(self) -> None:
        self._sync_format_visibility()

    def _selected_pg_dump_format(self) -> PgDumpFormat | None:
        if not self._show_pg_file_extension():
            return None
        label = self.file_ext.get()
        return PG_FORMAT_LABEL_TO_VALUE.get(label, PgDumpFormat.CUSTOM)

    def _selected_mysql_dump_format(self) -> MySQLDumpFormat | None:
        if not self._show_mysql_file_extension():
            return None
        label = self.file_ext.get()
        return MYSQL_FORMAT_LABEL_TO_VALUE.get(label, MySQLDumpFormat.PLAIN)

    def _selected_pg_file_extension(self) -> str | None:
        """Legacy helper kept for callers; prefer _selected_pg_dump_format."""
        fmt = self._selected_pg_dump_format()
        if fmt is None:
            return None
        if fmt == PgDumpFormat.PLAIN:
            return "sql"
        if fmt == PgDumpFormat.TAR:
            return "tar"
        if fmt == PgDumpFormat.DIRECTORY:
            return "directory"
        return "dump"

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
        self._sync_destination_fields()
        self._sync_pg_scope_visibility()
        self._sync_pg_file_extension_visibility()
        self._load_scope_targets()
        self._update_command_preview()

    def _on_output_dir_changed(self) -> None:
        path = self.output_dir_entry.get().strip()
        self._local_output_dir = path
        self._update_command_preview()

    def _browse_initial_dir(self) -> str | None:
        current = self.output_dir_entry.get().strip() or self._local_output_dir
        if not current:
            return None
        from pathlib import Path

        p = Path(current)
        if p.is_dir():
            return str(p)
        if p.parent.exists():
            return str(p.parent)
        return None

    def _browse_backup_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose backup file location",
            initialdir=self._browse_initial_dir(),
            defaultextension=".dump",
            filetypes=[
                ("Custom dump", "*.dump"),
                ("SQL files", "*.sql"),
                ("Gzip SQL", "*.sql.gz"),
                ("Tar archive", "*.tar"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._apply_backup_path(path)

    def _browse_backup_folder(self) -> None:
        path = filedialog.askdirectory(
            title="Select folder on this PC to save backup files",
            initialdir=self._browse_initial_dir(),
        )
        if path:
            self._apply_backup_path(path)

    def _browse_output_dir(self) -> None:
        # Compatibility alias used by older callers.
        self._browse_backup_folder()

    def _apply_backup_path(self, path: str) -> None:
        self._local_output_dir = path
        self.output_dir_entry.delete(0, "end")
        self.output_dir_entry.insert(0, path)
        self._update_command_preview()

    def _resolve_backup_destination(self) -> tuple[str | None, str | None]:
        """Return (output_path, local_output_dir) from the Local backup path field."""
        from pathlib import Path

        raw = self.output_dir_entry.get().strip() or self._local_output_dir
        if not raw:
            return None, None

        path = Path(raw)
        lowered = raw.lower()
        file_markers = (
            ".sql.gz",
            ".tar.gz",
            ".sql",
            ".dump",
            ".tar",
            ".bak",
            ".tgz",
            ".archive",
        )
        looks_like_file = any(lowered.endswith(ext) for ext in file_markers)
        if looks_like_file or (path.suffix and not path.is_dir()):
            parent = path.parent
            if str(parent) in ("", "."):
                raise ValueError("Choose a full file path (including the destination folder).")
            return str(path), str(parent)
        return None, str(path)

    def _selected_destination(self) -> str:
        return self.destination_seg.get()

    def _backup_source_type(self) -> BackupSourceType:
        dest = self._selected_destination()
        if dest == "Linux Server":
            return BackupSourceType.CONNECTED_SERVER
        if dest == "Remote Linux Server":
            return BackupSourceType.OTHER_SERVER
        return BackupSourceType.LOCAL

    def _is_server_backup(self) -> bool:
        return self._backup_source_type() != BackupSourceType.LOCAL

    def _is_connected_server_backup(self) -> bool:
        return self._backup_source_type() == BackupSourceType.CONNECTED_SERVER

    def _is_other_server_backup(self) -> bool:
        return self._backup_source_type() == BackupSourceType.OTHER_SERVER

    def _is_remote_backup(self) -> bool:
        return self._is_server_backup()

    def _on_destination_changed(self, _value: str | None = None) -> None:
        if self._is_connected_server_backup() and not self._ssh_available():
            self.destination_seg.set("Local Server")
            self.status_label.configure(
                text="Linux Server backup needs SSH on the connection. Enable SSH Tunnel and reconnect."
            )
        self._sync_source_fields()
        if self._is_other_server_backup():
            self._prefill_other_ssh_if_empty()
        if self._is_server_backup():
            self._ensure_remote_path_default()
        self._update_command_preview()

    def _on_database_changed(self, _event=None) -> None:
        self._scope_selected_targets = []
        self._sync_destination_fields()
        self._load_scope_targets()
        self._update_command_preview()

    def _populate_database_dropdown(self) -> None:
        """Fetch databases from the server in a background thread and populate the dropdown."""
        if not self.connection:
            return

        def task():
            try:
                dbs = fetch_server_databases(self.connection)
                names = [d["name"] for d in dbs if d.get("name")]
                self.after(0, lambda: self._set_database_values(names))
            except Exception:
                pass

        threading.Thread(target=task, daemon=True).start()

    def _set_database_values(self, names: list[str]) -> None:
        current = self.database.get().strip()
        if self._is_mongodb_session():
            values = [ALL_DATABASES_LABEL, *names]
        else:
            values = names or [SELECT_DATABASE_LABEL]
        self.database.configure(values=values)
        if current and current not in _IGNORED_DATABASE_VALUES and current in values:
            self.database.set(current)
            return
        connected = (self.connection.database or "").strip() if self.connection else ""
        if connected and connected in values:
            self.database.set(connected)
        elif self._is_mongodb_session():
            self.database.set(ALL_DATABASES_LABEL)
        elif names:
            self.database.set(names[0])
        else:
            self.database.set(SELECT_DATABASE_LABEL)
        self._update_command_preview()

    def _backup_database_name(self) -> str | None:
        value = self.database.get().strip()
        if value in _IGNORED_DATABASE_VALUES:
            return None
        return value

    def _on_collection_changed(self, value: str | None = None) -> None:
        selected = value if value is not None else self.collection_combo.get()
        if not selected or selected == "All collections":
            self._selected_collection_name = None
        else:
            self._selected_collection_name = selected
        self._highlight_collection_rows()
        self._update_collection_scope_label()
        self._update_command_preview()

    def _selected_collection(self) -> str | None:
        value = self.collection_combo.get()
        if not value or value == "All collections":
            return None
        return value.strip() or None

    def _update_collection_scope_label(self) -> None:
        if not hasattr(self, "collection_scope_label"):
            return
        coll = self._selected_collection()
        db = self._backup_database_name() or "database"
        if coll:
            self.collection_scope_label.configure(
                text=f"Scope: single collection → {db}.{coll}",
                text_color=self.theme.c("success"),
            )
        else:
            self.collection_scope_label.configure(
                text=f"Scope: all collections in {db}",
                text_color=self.theme.c("primary"),
            )
        self._update_backup_size_note()

    def _update_backup_size_note(self) -> None:
        label = getattr(self, "collection_size_label", None)
        if label is None:
            return
        if self._selected_collection() or not self._is_mongodb_session():
            label.configure(text="")
            return
        count = len(getattr(self, "_mongodb_collections", []) or [])
        size_bytes = int(getattr(self, "_mongo_size_bytes", 0) or 0)
        if count <= 0 and size_bytes <= 0:
            label.configure(text="Load collections to see how large this full backup is.")
            return
        size_text = _format_byte_size(size_bytes) if size_bytes else "size unknown"
        noun = "collection" if count == 1 else "collections"
        copy_note = ""
        if self._selected_destination() == "Local Server" and self._ssh_available():
            copy_note = " mongodump runs on the Linux server, then the full dump is copied to this PC."
        elif self._selected_destination() == "Linux Server":
            copy_note = " The dump stays on the Linux server."
        label.configure(
            text=f"Full backup is about {size_text} across {count} {noun}.{copy_note}"
        )

    def _clear_collection_rows(self) -> None:
        for row in self._collection_rows:
            row.destroy()
        self._collection_rows.clear()

    def _show_collections_message(self, text: str) -> None:
        self._clear_collection_rows()
        msg = ctk.CTkLabel(
            self.collections_list,
            text=text,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
            justify="left",
        )
        msg.pack(fill="x", padx=10, pady=10)
        self._collection_rows.append(msg)

    def _select_collection(self, name: str | None) -> None:
        """Select a single collection (or None for all) for mongodump --collection."""
        if name:
            self.collection_combo.set(name)
            self._selected_collection_name = name
        else:
            self.collection_combo.set("All collections")
            self._selected_collection_name = None
        self._highlight_collection_rows()
        self._update_collection_scope_label()
        self._update_command_preview()
        self._save_backup_prefs()

    def _highlight_collection_rows(self) -> None:
        active = self._selected_collection()
        for row in self._collection_rows:
            name = getattr(row, "_collection_name", None)
            marker = getattr(row, "_marker", None)
            if name is None:
                continue
            is_all_option = name == ""
            selected = (active is None and is_all_option) or (bool(active) and name == active)
            if selected:
                row.configure(fg_color=self.theme.c("primary_muted"), border_color=self.theme.c("primary"))
                if marker is not None:
                    marker.configure(text="●", text_color=self.theme.c("primary"))
            else:
                row.configure(fg_color=self.theme.c("bg_card"), border_color=self.theme.c("border_subtle"))
                if marker is not None:
                    marker.configure(text="○", text_color=self.theme.c("text_muted"))

    def _make_collection_row(self, *, key: str, title: str, subtitle: str = "") -> ctk.CTkFrame:
        """Selectable collection row — click anywhere to choose for single-collection backup."""
        row = ctk.CTkFrame(
            self.collections_list,
            fg_color=self.theme.c("bg_card"),
            corner_radius=8,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
            height=40,
        )
        row.pack(fill="x", padx=6, pady=2)
        row.pack_propagate(False)
        row._collection_name = key  # type: ignore[attr-defined]

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=0)

        marker = ctk.CTkLabel(
            inner,
            text="○",
            width=18,
            font=ctk.CTkFont(size=14),
            text_color=self.theme.c("text_muted"),
        )
        marker.pack(side="left")
        row._marker = marker  # type: ignore[attr-defined]

        title_lbl = ctk.CTkLabel(
            inner,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        )
        title_lbl.pack(side="left", padx=(8, 8), fill="x", expand=True)

        meta = None
        if subtitle:
            meta = ctk.CTkLabel(
                inner,
                text=subtitle,
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                width=110,
                anchor="e",
            )
            meta.pack(side="right")

        target = None if key == "" else key

        def on_click(_event=None, n=target) -> None:
            self._select_collection(n)

        for widget in (row, inner, marker, title_lbl):
            widget.bind("<Button-1>", on_click)
        if meta is not None:
            meta.bind("<Button-1>", on_click)

        self._collection_rows.append(row)
        return row

    def _add_collection_row(self, name: str, count: int) -> None:
        self._make_collection_row(key=name, title=name, subtitle=f"{count:,} docs")

    def _populate_collections(
        self,
        collections: list[dict],
        error: str | None = None,
        size_bytes: int = 0,
    ) -> None:
        self._mongodb_collections = collections
        self._mongo_size_bytes = int(size_bytes or 0)
        self.collection_combo.configure(state="normal")
        self._clear_collection_rows()

        if error:
            self._show_collections_message(f"Error: {error}")
            self.collection_combo.configure(values=["All collections"])
            self._select_collection(None)
            return

        names = [c["name"] for c in collections]
        self.collection_combo.configure(values=["All collections"] + names)

        if not collections:
            self._show_collections_message("No collections found in this database.")
            self._select_collection(None)
            return

        size_text = _format_byte_size(self._mongo_size_bytes) if self._mongo_size_bytes else "Full database"
        self._make_collection_row(key="", title="All collections", subtitle=size_text)
        for item in collections:
            self._add_collection_row(item["name"], int(item.get("count", 0) or 0))

        preferred = self._selected_collection_name
        if preferred and preferred in names:
            self._select_collection(preferred)
        else:
            self._select_collection(None)

    def _load_collections(self) -> None:
        if not self._is_mongodb_session():
            return
        if not self.connection:
            self._show_collections_message("Connect to a MongoDB server first.")
            return

        db_name = self.database.get().strip()
        if not db_name:
            self._show_collections_message("Enter a database name first.")
            return

        self.collection_combo.configure(state="disabled")
        self._show_collections_message("Loading collections...")

        def task():
            try:
                collections = fetch_mongodb_collections(self.connection, db_name)
                size_bytes = fetch_mongodb_database_size_bytes(self.connection, db_name)
                self.after(0, lambda c=collections, n=size_bytes: self._populate_collections(c, size_bytes=n))
            except Exception as exc:
                self.after(0, lambda: self._populate_collections([], str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _sync_source_fields(self) -> None:
        """Show/hide Local vs Linux Server vs Remote Linux Server backup panels."""
        self._sync_destination_fields()

    def _sync_destination_fields(self) -> None:
        is_mongo = self._is_mongodb_session()
        has_db = bool(self._backup_database_name())
        dest = self._selected_destination()
        ssh_on = self._ssh_available()
        connected_on = self._is_connected_server_backup() and ssh_on
        other_remote = self._is_other_server_backup()

        if dest != "Remote Linux Server" and self._other_ssh_connected:
            self._reset_other_ssh_state()

        self.destination_frame.grid()
        self.destination_seg.configure(state="normal")

        self.output_frame.grid_remove()
        self.remote_frame.grid_remove()
        self.other_frame.grid_remove()

        if dest == "Local Server":
            self.output_frame.grid(row=10, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            self.destination_hint.configure(
                text="Save the backup on this PC (Local Server)."
            )
        elif dest == "Linux Server":
            self.remote_frame.grid(row=12, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            if ssh_on:
                self.destination_hint.configure(
                    text="Backup on the Linux server linked to this connection (SSH required)."
                )
            else:
                self.destination_hint.configure(
                    text="Enable SSH on the connection to back up on the linked Linux Server."
                )
        else:
            self.other_frame.grid(row=13, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            self.destination_hint.configure(
                text="Backup on a Remote Linux Server — enter SSH details below."
            )

        self._update_ssh_status_label()
        self._update_backup_size_note()

        if self._is_connected_server_backup():
            self._ensure_remote_path_default()
            self.remote_dir_entry.configure(state="normal" if connected_on else "disabled")
            if self._remote_browser:
                self._remote_browser.set_enabled(connected_on)
                if connected_on and not getattr(self._remote_browser, "_entries", None):
                    self.after(
                        100,
                        lambda: self._remote_browser.load_directory(
                            self.remote_dir_entry.get().strip() or self._remote_output_dir
                        ),
                    )

        for widget, enabled in (
            (self.other_ssh_host, other_remote),
            (self.other_ssh_port, other_remote),
            (self.other_ssh_user, other_remote),
            (self.other_ssh_password, other_remote),
            (self.other_ssh_password_toggle, other_remote),
        ):
            widget.configure(state="normal" if enabled else "disabled")

        browse_enabled = other_remote and self._other_ssh_connected
        self.other_dir_entry.configure(state="normal" if browse_enabled else "disabled")
        if self._other_browser:
            self._other_browser.set_enabled(browse_enabled)
            if other_remote and not self._other_ssh_connected:
                self._other_browser.status_label.configure(
                    text="Enter SSH credentials and click Connect & Browse."
                )

        self.database_hint.configure(text=self._database_hint_text())
        self.database_hint.pack(anchor="w", pady=(6, 0))
        if is_mongo:
            self.mongo_options_frame.grid()
            self.mongo_folder_label.grid()
            self.backup_folder_entry.grid()
            self.mongo_folder_hint.grid()
            self.gzip_cb.grid()
            self.remote_binary_label.grid()
            self.remote_binary.grid()
            if has_db:
                self.collections_frame.grid()
            else:
                self.collections_frame.grid_remove()
        else:
            self.collections_frame.grid_remove()
            self.mongo_options_frame.grid_remove()
            self.mongo_folder_label.grid_remove()
            self.backup_folder_entry.grid_remove()
            self.mongo_folder_hint.grid_remove()
            self.gzip_cb.grid_remove()
            self.remote_binary_label.grid_remove()
            self.remote_binary.grid_remove()

        server_on = self._is_server_backup()
        self.remote_binary.configure(
            state="normal" if (server_on and is_mongo) else "disabled"
        )

    def _sync_form_state(self) -> None:
        method_value = self._selected_method_value()
        physical = self._category == BackupCategory.PHYSICAL
        cluster_wide = method_value in (
            BackupMethod.PG_DUMPALL.value,
            BackupMethod.PG_BASEBACKUP.value,
            BackupMethod.MYSQL_PHYSICAL.value,
        )

        if cluster_wide or physical:
            self.database.configure(state="disabled")
        else:
            self.database.configure(state="normal")
        self._sync_scope_visibility()
        self._sync_format_visibility()

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

        source = self._backup_source_type()
        remote = source != BackupSourceType.LOCAL
        output_path, local_dir = self._resolve_backup_destination()
        db_name = self._backup_database_name()
        # Physical MySQL snapshots the instance; keep selected DB for naming when available.
        if method_value == BackupMethod.MYSQL_PHYSICAL.value and not db_name and self.connection:
            db_name = self.connection.database or None

        external_host: str | None = None
        external_port = 22
        external_user: str | None = None
        external_password: str | None = None
        remote_dir = ""

        if source == BackupSourceType.CONNECTED_SERVER:
            remote_dir = self.remote_dir_entry.get().strip()
            if self._remote_browser:
                browser_path = self._remote_browser.get_selected_path().strip()
                if browser_path and not remote_dir:
                    remote_dir = browser_path
            if not remote_dir:
                remote_dir = (self._remote_output_dir or "/mnt/backup").strip() or "/mnt/backup"
            if not self._ssh_options():
                raise ValueError(
                    "Linux Server backup requires SSH on the connection. "
                    "Enable SSH Tunnel on Servers, save, and Connect again."
                )
        elif source == BackupSourceType.OTHER_SERVER:
            if not self._other_ssh_connected:
                raise ValueError(
                    "Connect to the Remote Linux Server first (click Connect & Browse)."
                )
            external_host = self.other_ssh_host.get().strip() or None
            external_user = self.other_ssh_user.get().strip() or None
            external_password = self.other_ssh_password.get() or None
            try:
                external_port = int(self.other_ssh_port.get().strip() or "22")
            except ValueError:
                raise ValueError("SSH port must be a number (e.g. 22).") from None
            if not external_host or not external_user or not external_password:
                raise ValueError(
                    "Remote Linux Server backup requires SSH host, username, and password."
                )
            remote_dir = self.other_dir_entry.get().strip()
            if self._other_browser:
                browser_path = self._other_browser.get_selected_path().strip()
                if browser_path and not remote_dir:
                    remote_dir = browser_path
            if not remote_dir:
                remote_dir = (self._remote_output_dir or "/mnt/backup").strip() or "/mnt/backup"

        pg_scope = self._selected_pg_scope() if self._show_pg_scope() else None
        pg_scope_targets = self._parse_pg_scope_targets() if pg_scope else []
        pg_advanced_args = None
        if pg_scope == PgBackupScope.SINGLE_SCHEMA and len(pg_scope_targets) != 1:
            raise ValueError("Single Schema scope requires exactly one schema name.")
        if pg_scope == PgBackupScope.SELECTED_TABLES:
            if not self._selected_tables_schema():
                raise ValueError("Selected Tables requires choosing a schema first.")
            if not pg_scope_targets:
                raise ValueError("Selected Tables requires at least one table from the chosen schema.")
        if pg_scope == PgBackupScope.MULTIPLE_SCHEMAS and not pg_scope_targets:
            raise ValueError("Enter one or more comma-separated targets for the selected backup scope.")
        if pg_scope == PgBackupScope.SELECTED_OBJECTS:
            pg_advanced_args = self.scope_target_entry.get().strip() or None
            if not pg_advanced_args:
                raise ValueError("Selected Objects (Advanced) requires pg_dump selector arguments.")

        mysql_scope = self._selected_mysql_scope() if self._show_mysql_scope() else None
        mysql_scope_targets = self._parse_mysql_scope_targets() if mysql_scope else []
        if mysql_scope == MySQLBackupScope.SELECTED_TABLES and not mysql_scope_targets:
            raise ValueError("Selected Tables scope requires at least one MySQL table.")
        if (
            self._active_db_type() == DatabaseType.MYSQL
            and self._category == BackupCategory.LOGICAL
            and not db_name
        ):
            raise ValueError("Select a MySQL database to back up.")

        if not remote and not (output_path or local_dir):
            raise ValueError("Choose a Local Server backup path (file or folder) for the backup.")

        return BackupRequest(
            profile=self.get_active_profile(),
            db_type=self._active_db_type(),
            method=BackupMethod(method_value),
            database=db_name,
            compress=False,
            output_path=output_path if not remote else None,
            local_output_dir=local_dir if not remote else None,
            backup_source=source,
            use_remote_exec=remote,
            remote_binary_path=(
                self.remote_binary.get().strip() or None
                if self._is_mongodb_session()
                else None
            ),
            remote_output_dir=remote_dir or None,
            external_ssh_host=external_host,
            external_ssh_port=external_port,
            external_ssh_username=external_user,
            external_ssh_password=external_password,
            backup_folder_name=(
                self.backup_folder_entry.get().strip() or None
                if self._is_mongodb_session()
                else None
            ),
            collection=self._selected_collection() if db_name and self._is_mongodb_session() else None,
            gzip_archive=bool(self.gzip_var.get()) if self._is_mongodb_session() else False,
            pg_dump_format=self._selected_pg_dump_format(),
            pg_file_extension=self._selected_pg_file_extension(),
            pg_backup_scope=pg_scope,
            pg_scope_targets=pg_scope_targets,
            pg_advanced_args=pg_advanced_args,
            mysql_dump_format=self._selected_mysql_dump_format(),
            mysql_backup_scope=mysql_scope,
            mysql_scope_targets=mysql_scope_targets,
        )

    def _update_command_preview(self) -> None:
        if not hasattr(self, "command_preview"):
            return
        try:
            request = self._build_request()
            preview = describe_backup_command(request)
            if self._is_other_server_backup():
                dest = self.other_dir_entry.get().strip() or self._remote_output_dir
                host = self.other_ssh_host.get().strip()
                if dest:
                    preview = f"{preview}\n# Remote Linux Server ({host}) path: {dest}"
            elif self._is_connected_server_backup():
                dest = self.remote_dir_entry.get().strip() or self._remote_output_dir
                if dest:
                    preview = f"{preview}\n# Linux Server path: {dest}"
            else:
                dest = self.output_dir_entry.get().strip() or self._local_output_dir
                if dest:
                    preview = f"{preview}\n# Local Server path: {dest}"
        except ValueError as exc:
            preview = str(exc)

        self.command_preview.configure(state="normal")
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("1.0", preview)
        self.command_preview.configure(state="disabled")

    def _update_progress(self, value: float, message: str) -> None:
        import time

        now = time.monotonic()
        self._latest_progress = (value, message)

        def _apply(v: float = value, m: str = message) -> None:
            self.progress.set(v)
            self.status_label.configure(text=m)

        self.after(0, _apply)

        # Throttle Results pane rewrites so UI stays responsive during streaming.
        last = getattr(self, "_last_results_progress_at", 0.0)
        if now - last < 1.5:
            return
        self._last_results_progress_at = now

        def _refresh_results(msg: str = message) -> None:
            if not hasattr(self, "results"):
                return
            self.results.delete("1.0", "end")
            self.results.insert("end", f"Backup in progress...\n{msg}\n")

        self.after(0, _refresh_results)

    def _run_backup(self) -> None:
        self.progress.set(0.02)
        self.status_label.configure(text="Starting backup...")
        self._hide_results_empty_state()
        self.results.delete("1.0", "end")
        self.results.insert("end", "Backup in progress...\n")

        try:
            request = self._build_request()
        except ValueError as exc:
            self.status_label.configure(text="Backup failed")
            self.results.insert("end", f"Error: {exc}\n")
            return

        cached_other_ssh = dict(self._cached_other_ssh) if self._cached_other_ssh else None
        started = time.perf_counter()

        def task():
            try:
                if request.use_remote_exec:
                    self.after(
                        0,
                        lambda: self.status_label.configure(text="Connecting to Linux server via SSH..."),
                    )
                    if cached_other_ssh:
                        self._verify_ssh_connection(cached_other_ssh)
                    else:
                        self._verify_ssh_connection()
                    self.after(
                        0,
                        lambda: self.status_label.configure(text="SSH connected — starting remote backup..."),
                    )
                category = self._category.value
                self.log(f"Starting {category} backup: {request.method.value}")
                if request.collection:
                    self.log(f"Single collection: {request.database}.{request.collection}")
                result = self.service.create_backup(request, progress_callback=self._update_progress)
                self.after(0, self._save_backup_prefs)

                def show_success(path: str = result.output_path) -> None:
                    self.results.delete("1.0", "end")
                    if request.use_remote_exec:
                        self.results.insert(
                            "end",
                            f"[{category}] Success — backup saved on Linux server:\n{path}\n\n"
                            "Use List Backups to see files in the remote folder.\n",
                        )
                    else:
                        self.results.insert(
                            "end",
                            f"[{category}] Success:\n{path}\n\n"
                            "Open the backups folder or click List Backups.\n",
                        )
                    self.progress.set(1.0)
                    self.status_label.configure(text="Backup completed")

                self.after(0, show_success)
                elapsed = time.perf_counter() - started
                size_bytes = _path_size_bytes(result.output_path)
                size_text = _format_byte_size(size_bytes) if size_bytes else "size not measured"
                self.log(
                    f"Backup completed: {result.output_path} — {size_text} in {_format_duration(elapsed)}"
                )
            except Exception as exc:
                message = str(exc)

                def show_error(msg: str = message, err: Exception = exc) -> None:
                    self.results.delete("1.0", "end")
                    self.results.insert("end", f"Error: {msg}\n")
                    self.status_label.configure(text="Backup failed")
                    from desktop.ssh_host_trust import maybe_offer_host_key_trust

                    maybe_offer_host_key_trust(self, err)

                self.after(0, show_error)
                self.log(f"Backup failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()

    def _list_backups(self) -> None:
        list_remote = self._is_remote_backup()
        local_dir = self.output_dir_entry.get().strip() or self._local_output_dir
        if self._is_other_server_backup():
            remote_dir = self.other_dir_entry.get().strip() or self._remote_output_dir
            ssh_override = self._cached_other_ssh or (
                self._read_other_ssh_fields() if self._other_ssh_ready() else None
            )
        else:
            remote_dir = self.remote_dir_entry.get().strip() or self._remote_output_dir
            ssh_override = None
        if not list_remote and not local_dir:
            self.results.delete("1.0", "end")
            self.results.insert(
                "end",
                "Choose a Local Server backup path (File or Folder) first, then click List Backups.\n",
            )
            return
        backups = self.service.list_backups(
            self.get_active_profile(),
            local_dir=local_dir or None,
            remote_dir=remote_dir,
            list_remote=list_remote,
            ssh_override=ssh_override,
        )
        if list_remote:
            display_dir = remote_dir or "/mnt/backup"
        else:
            display_dir = str(self.service._resolve_local_backup_dir(local_dir))
        self.results.delete("1.0", "end")
        if list_remote:
            self.results.insert("end", f"Remote backups on Linux server ({display_dir}):\n\n")
        else:
            self.results.insert("end", f"Local backups on this PC ({display_dir}):\n\n")
        if not backups:
            self.results.insert("end", "No backups found yet.\n")
            return
        for b in backups:
            size = b["size_bytes"]
            if size >= 1024 * 1024:
                size_text = f"{size / (1024 * 1024):.1f} MB"
            elif size > 0:
                size_text = f"{size:,} bytes"
            else:
                size_text = "remote folder" if b.get("remote") else "0 bytes"
            self.results.insert(
                "end", f"{b['name']}  —  {size_text}\n  {b['path']}\n\n"
            )


def _format_byte_size(size_bytes: int) -> str:
    size = int(size_bytes or 0)
    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _path_size_bytes(path: str) -> int:
    target = Path(path)
    if not target.exists():
        return 0
    if target.is_file():
        return target.stat().st_size
    return sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
