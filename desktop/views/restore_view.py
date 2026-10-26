"""Restore management view."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    DatabaseType,
    MongoRestoreScope,
    RestoreRequest,
    RestoreSourceType,
    RestoreTargetType,
)
from backend.services.backup_catalog import category_description
from backend.services.mongo_restore import (
    mongo_restore_scope_hint,
    mongo_restore_scope_label,
    mongo_restore_scopes,
)
from backend.services.restore_catalog import (
    describe_restore_command,
    restore_method_label,
    restore_methods_for,
)
from backend.services.restore_service import RestoreService
from desktop.connection_store import SavedServer, load_saved_servers
from desktop.db_catalog import fetch_server_databases
from desktop.design_system import primary_button
from desktop.remote_directory_browser import RemoteDirectoryBrowser
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class RestoreView(BaseView):
    """Database restore operations with logical / physical segregation."""

    DB_TYPES = [t.value for t in DatabaseType]

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = RestoreService()
        self._category = BackupCategory.LOGICAL
        self._mongo_scope = MongoRestoreScope.SPECIFIC_DATABASE
        self._remote_backup_root = "/mnt/backup"
        self._other_ssh_connected = False
        self._other_connected_host = ""
        self._cached_other_ssh: dict | None = None
        self._other_ssh_password_visible = False
        self._build_ui()
        self._init_restore_defaults()
        self._sync_mongo_fields()
        self._refresh_methods()
        self._populate_database_dropdown()

    def refresh(self, *, force: bool = False) -> None:
        """Reload restore form data from the active connection / server."""
        del force
        if self.connection and hasattr(self, "db_type"):
            try:
                self.db_type.set(self.connection.db_type.value)
            except Exception:
                pass
        self._init_restore_defaults()
        self._sync_mongo_fields()
        self._refresh_methods()
        self._populate_database_dropdown()
        self._sync_source_fields()
        try:
            self._update_command_preview()
        except Exception:
            pass
        self.log("Restore page refreshed")

    def _init_restore_defaults(self) -> None:
        saved = self._find_saved_server()
        if saved and saved.remote_backup_dir:
            self._remote_backup_root = saved.remote_backup_dir
        if self._is_mongodb_session():
            from backend.services.mongo_tools import sibling_mongo_tool_path

            saved_path = sibling_mongo_tool_path(
                saved.mongodump_path if saved else None,
                "mongorestore",
            )
            current = self.remote_binary.get().strip()
            if saved_path and not current:
                self.remote_binary.delete(0, "end")
                self.remote_binary.insert(0, saved_path)
        self._sync_source_fields()

    def _ssh_options(self) -> dict:
        from backend.connectors.base import resolve_connection
        from backend.services.ssh_client import ssh_options_from_connection

        conn = resolve_connection(self.get_active_profile())
        return ssh_options_from_connection(conn)

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
        }

    def _other_ssh_options(self) -> dict:
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

    def _on_connected_path_selected(self, path: str) -> None:
        try:
            state = str(self.connected_source_entry.cget("state") or "normal")
            self.connected_source_entry.configure(state="normal")
            self.connected_source_entry.delete(0, "end")
            self.connected_source_entry.insert(0, path)
            if state == "disabled":
                self.connected_source_entry.configure(state="disabled")
            self._update_command_preview()
        except Exception:
            pass

    def _on_other_path_selected(self, path: str) -> None:
        self.other_source_entry.delete(0, "end")
        self.other_source_entry.insert(0, path)
        self._sync_restore_target_visibility()
        self._update_command_preview()

    def _is_mongodb_session(self) -> bool:
        return self._active_db_type() == DatabaseType.MONGODB

    def _ssh_available(self) -> bool:
        return bool(
            self.connection
            and self.connection.ssh_enabled
            and self.connection.linux_username
        )

    def _find_saved_server(self) -> SavedServer | None:
        if not self.connection:
            return None
        lookup = self.connection.saved_name or self.connection.name
        active_type = self._active_db_type().value
        for server in load_saved_servers(self.owner_username):
            if server.name == lookup and server.db_type == active_type:
                return server
        return None

    def _merged_ssh_settings(self) -> dict[str, str | int | bool]:
        saved = self._find_saved_server()
        c = self.connection
        ssh_enabled = bool(c and c.ssh_enabled) or bool(saved and saved.ssh_enabled)
        if not ssh_enabled:
            return {"ssh_enabled": False}

        def pick(conn_val, saved_val, default=""):
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
            "ssh_port": int(pick(c.ssh_port if c else None, saved.ssh_port if saved else None, 22)),
            "linux_username": str(
                pick(c.linux_username if c else None, saved.linux_username if saved else None, "")
            ),
            "linux_password": str(
                pick(c.linux_password if c else None, saved.linux_password if saved else None, "")
            ),
            "remote_backup_dir": str(
                pick(
                    getattr(c, "remote_backup_dir", "") if c else "",
                    saved.remote_backup_dir if saved else "",
                    "/mnt/backup",
                )
            ),
        }

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
        if settings.get("remote_backup_dir"):
            self._remote_backup_root = str(settings["remote_backup_dir"])
        host = self.other_ssh_host.get().strip()
        self.other_connect_hint.configure(
            text=f"Filled from connection SSH ({host}) — click Connect & Browse."
        )
        self.status_label.configure(
            text=f"SSH fields filled from connection — {host}",
            text_color=self.theme.c("text_muted"),
        )
        self._update_command_preview()

    def _prefill_other_ssh_if_empty(self) -> None:
        if self.other_ssh_host.get().strip():
            return
        self._apply_connection_ssh_to_other_form()

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
            "Restore",
            subtitle="Restore databases from logical or physical backups.",
            pady=(0, 12),
        )

        form = self.card_frame(scroll)
        form.pack(fill="x", pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)

        active_type = self.get_active_db_type()
        ctk.CTkLabel(form, text="Database Type:").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        self.db_type = ctk.CTkComboBox(
            form, values=self.DB_TYPES, command=self._on_db_type_changed
        )
        self.db_type.set(active_type.value)
        self.db_type.grid(row=0, column=1, padx=16, pady=10, sticky="ew")
        if self.connection:
            self.db_type.configure(state="disabled")

        ctk.CTkLabel(form, text="Connection:").grid(row=1, column=0, padx=16, pady=10, sticky="w")
        conn_label = self.connection.name if self.connection else "default"
        ctk.CTkLabel(form, text=conn_label).grid(row=1, column=1, padx=16, pady=10, sticky="w")

        ctk.CTkLabel(form, text="Restore Type:").grid(row=2, column=0, padx=16, pady=10, sticky="nw")
        type_frame = ctk.CTkFrame(form, fg_color="transparent")
        type_frame.grid(row=2, column=1, padx=16, pady=10, sticky="ew")
        type_frame.grid_columnconfigure(0, weight=1)

        self.category_seg = ctk.CTkSegmentedButton(
            type_frame,
            values=["Logical", "Physical"],
            command=self._on_category_changed,
        )
        self.category_seg.set("Logical")
        self.category_seg.grid(row=0, column=0, sticky="w")

        self.category_logical_label = ctk.CTkLabel(
            type_frame,
            text="Logical",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
        )

        self.category_hint = ctk.CTkLabel(
            type_frame,
            text=category_description(BackupCategory.LOGICAL),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        )
        self.category_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        ctk.CTkLabel(form, text="Command:").grid(row=3, column=0, padx=16, pady=10, sticky="nw")
        self.method = ctk.CTkComboBox(
            form, values=["mongorestore"], command=self._on_method_changed
        )
        self.method.set("mongorestore")
        self.method.grid(row=3, column=1, padx=16, pady=10, sticky="ew")

        self.scope_label = ctk.CTkLabel(form, text="Restore scope:")
        self.scope_label.grid(row=4, column=0, padx=16, pady=10, sticky="nw")
        scope_col = ctk.CTkFrame(form, fg_color="transparent")
        scope_col.grid(row=4, column=1, padx=16, pady=10, sticky="ew")
        scope_col.grid_columnconfigure(0, weight=1)
        self.scope_col = scope_col

        scope_labels = [mongo_restore_scope_label(s) for s in mongo_restore_scopes()]
        self.restore_scope = ctk.CTkComboBox(
            scope_col,
            values=scope_labels,
            command=self._on_scope_changed,
        )
        self.restore_scope.set(mongo_restore_scope_label(MongoRestoreScope.SPECIFIC_DATABASE))
        self.restore_scope.grid(row=0, column=0, sticky="ew")

        self.scope_hint = ctk.CTkLabel(
            scope_col,
            text=mongo_restore_scope_hint(MongoRestoreScope.SPECIFIC_DATABASE),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.scope_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        ctk.CTkLabel(form, text="Database:").grid(row=5, column=0, padx=16, pady=10, sticky="nw")
        db_col = ctk.CTkFrame(form, fg_color="transparent")
        db_col.grid(row=5, column=1, padx=16, pady=10, sticky="ew")
        db_col.grid_columnconfigure(0, weight=1)

        initial_db = ""
        if self.connection and self.connection.database:
            initial_db = self.connection.database
        self.database = ctk.CTkComboBox(
            db_col,
            values=[initial_db] if initial_db else [""],
            command=self._on_database_changed,
        )
        if initial_db:
            self.database.set(initial_db)
        else:
            self.database.set("")
        self.database.pack(fill="x")

        self.database_hint = ctk.CTkLabel(
            db_col,
            text="Select the target database from the list on this server.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.database_hint.pack(anchor="w", pady=(6, 0))

        self.collection_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.collection_frame.grid(row=6, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.collection_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.collection_frame, text="Collection:").grid(row=0, column=0, sticky="w", pady=6)
        self.collection = ctk.CTkEntry(
            self.collection_frame,
            placeholder_text="Collection name (e.g. users)",
        )
        self.collection.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.collection.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(
            self.collection_frame,
            text="Target collection name (created automatically if it does not exist).",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        ).grid(row=1, column=1, padx=(12, 0), sticky="w")

        self.destination_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.destination_frame.grid(row=7, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.destination_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.destination_frame, text="Restore source:").grid(
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
            text="Pick a backup file or folder on this PC (Local Server).",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.destination_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.local_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.local_frame.grid(row=8, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.local_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.local_frame, text="Local backup path:").grid(row=0, column=0, sticky="w", pady=6)
        local_row = ctk.CTkFrame(self.local_frame, fg_color="transparent")
        local_row.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        local_row.grid_columnconfigure(0, weight=1)

        self.local_source_entry = ctk.CTkEntry(
            local_row,
            placeholder_text="File or folder path on this PC",
        )
        self.local_source_entry.grid(row=0, column=0, sticky="ew")
        self.local_source_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkButton(local_row, text="File", width=60, command=self._browse_local_file).grid(
            row=0, column=1, padx=(8, 0)
        )
        ctk.CTkButton(local_row, text="Folder", width=70, command=self._browse_local_folder).grid(
            row=0, column=2, padx=(6, 0)
        )

        self.local_hint = ctk.CTkLabel(
            self.local_frame,
            text="Browse or type a backup file/folder on this PC.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        )
        self.local_hint.grid(row=1, column=1, padx=(12, 0), sticky="w")

        self.connected_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.connected_frame.grid(row=9, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.connected_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.connected_frame, text="Server backup path:").grid(
            row=0, column=0, sticky="nw", pady=6
        )
        connected_col = ctk.CTkFrame(self.connected_frame, fg_color="transparent")
        connected_col.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        connected_col.grid_columnconfigure(0, weight=1)

        self.connected_source_entry = ctk.CTkEntry(
            connected_col,
            placeholder_text="Path on connected Linux server, e.g. /mnt/backup/...",
        )
        self.connected_source_entry.grid(row=0, column=0, sticky="ew")
        self.connected_source_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(
            self.connected_frame,
            text="Browse the Linux server linked to this connection (SSH required).",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        ).grid(row=1, column=1, padx=(12, 0), sticky="w")

        ctk.CTkLabel(
            self.connected_frame,
            text="Browse connected server",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 4))

        connected_browser_host = ctk.CTkFrame(self.connected_frame, fg_color="transparent")
        connected_browser_host.grid(row=3, column=0, columnspan=2, sticky="ew")
        connected_browser_host.grid_columnconfigure(0, weight=1)
        self._connected_browser = RemoteDirectoryBrowser(
            connected_browser_host,
            self.theme,
            get_ssh=self._ssh_options,
            initial_path=self._remote_backup_root,
            on_selection_changed=self._on_connected_path_selected,
        )
        self._connected_browser.grid(row=0, column=0, sticky="ew")

        self.other_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.other_frame.grid(row=10, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.other_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.other_frame, text="SSH host:").grid(row=0, column=0, sticky="w", pady=6)
        self.other_ssh_host = ctk.CTkEntry(self.other_frame, placeholder_text="e.g. 192.168.1.50")
        self.other_ssh_host.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.other_ssh_host.bind("<KeyRelease>", lambda _e: self._on_other_ssh_changed())

        ctk.CTkLabel(self.other_frame, text="SSH port:").grid(row=1, column=0, sticky="w", pady=6)
        self.other_ssh_port = ctk.CTkEntry(self.other_frame, placeholder_text="22")
        self.other_ssh_port.insert(0, "22")
        self.other_ssh_port.grid(row=1, column=1, padx=(12, 0), pady=6, sticky="ew")

        ctk.CTkLabel(self.other_frame, text="SSH username:").grid(row=2, column=0, sticky="w", pady=6)
        self.other_ssh_user = ctk.CTkEntry(self.other_frame, placeholder_text="linux user")
        self.other_ssh_user.grid(row=2, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.other_ssh_user.bind("<KeyRelease>", lambda _e: self._on_other_ssh_changed())

        ctk.CTkLabel(self.other_frame, text="SSH password:").grid(row=3, column=0, sticky="w", pady=6)
        other_pw_row = ctk.CTkFrame(self.other_frame, fg_color="transparent")
        other_pw_row.grid(row=3, column=1, padx=(12, 0), pady=6, sticky="ew")
        other_pw_row.grid_columnconfigure(0, weight=1)
        self.other_ssh_password = ctk.CTkEntry(other_pw_row, placeholder_text="password", show="*")
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

        connect_row = ctk.CTkFrame(self.other_frame, fg_color="transparent")
        connect_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        ctk.CTkButton(
            connect_row,
            text="Connect & Browse",
            width=150,
            command=self._connect_other_server,
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
        ).pack(side="left")
        ctk.CTkButton(
            connect_row,
            text="Use connection SSH",
            width=150,
            command=self._apply_connection_ssh_to_other_form,
        ).pack(side="left", padx=(8, 0))
        self.other_connect_hint = ctk.CTkLabel(
            connect_row,
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

        ctk.CTkLabel(self.other_browse_section, text="Backup path:").grid(
            row=0, column=0, sticky="nw", pady=6
        )
        other_path_col = ctk.CTkFrame(self.other_browse_section, fg_color="transparent")
        other_path_col.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        other_path_col.grid_columnconfigure(0, weight=1)

        self.other_source_entry = ctk.CTkEntry(
            other_path_col,
            placeholder_text="Path on the Remote Linux Server",
        )
        self.other_source_entry.grid(row=0, column=0, sticky="ew")
        self.other_source_entry.bind("<KeyRelease>", lambda _e: self._on_other_path_typed())

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
            initial_path=self._remote_backup_root,
            on_selection_changed=self._on_other_path_selected,
        )
        self._other_browser.grid(row=0, column=0, sticky="ew")
        self._other_browser.set_enabled(False)

        self.restore_target_frame = ctk.CTkFrame(self.other_frame, fg_color="transparent")
        self.restore_target_frame.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.restore_target_frame.grid_columnconfigure(1, weight=1)
        self.restore_target_frame.grid_remove()

        ctk.CTkLabel(self.restore_target_frame, text="Restore destination:").grid(
            row=0, column=0, sticky="nw", pady=6
        )
        restore_target_col = ctk.CTkFrame(self.restore_target_frame, fg_color="transparent")
        restore_target_col.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        restore_target_col.grid_columnconfigure(0, weight=1)

        self.restore_target_seg = ctk.CTkSegmentedButton(
            restore_target_col,
            values=["Local Server", "Linux Server"],
            command=self._on_restore_target_changed,
        )
        self.restore_target_seg.set("Local Server")
        self.restore_target_seg.grid(row=0, column=0, sticky="w")

        self.restore_target_hint = ctk.CTkLabel(
            restore_target_col,
            text="Restore on this PC using the downloaded backup.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.restore_target_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.remote_binary_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.remote_binary_frame.grid(row=11, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.remote_binary_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.remote_binary_frame, text="Mongorestore binary:").grid(
            row=0, column=0, sticky="w", pady=6
        )
        self.remote_binary = ctk.CTkEntry(
            self.remote_binary_frame,
            placeholder_text="Leave blank to auto-detect on the Linux server",
        )
        self.remote_binary.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.remote_binary.bind("<KeyRelease>", lambda _e: self._update_command_preview())
        ctk.CTkLabel(
            self.remote_binary_frame,
            text="Leave blank unless mongorestore is outside PATH and not under a usual install folder.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        ).grid(row=1, column=1, padx=(12, 0), sticky="w")

        self.gzip_var = ctk.BooleanVar(value=False)
        self.gzip_cb = ctk.CTkCheckBox(
            form,
            text="Gzip archive (--gzip)",
            variable=self.gzip_var,
            command=self._update_command_preview,
        )
        self.gzip_cb.grid(row=12, column=0, columnspan=2, padx=16, pady=6, sticky="w")

        self.drop_var = ctk.BooleanVar(value=False)
        self.drop_cb = ctk.CTkCheckBox(
            form,
            text="Drop existing collections before restore (--drop)",
            variable=self.drop_var,
            command=self._update_command_preview,
        )
        self.drop_cb.grid(row=13, column=0, columnspan=2, padx=16, pady=6, sticky="w")

        self.validate_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            form,
            text="Validate before restore",
            variable=self.validate_var,
        ).grid(row=14, column=0, columnspan=2, padx=16, pady=6, sticky="w")

        ctk.CTkLabel(form, text="Preview:").grid(row=15, column=0, padx=16, pady=10, sticky="nw")
        self.command_preview = ctk.CTkTextbox(form, height=88, activate_scrollbars=True)
        self.command_preview.grid(row=15, column=1, padx=16, pady=10, sticky="ew")
        self.command_preview.configure(state="disabled")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=16, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="ew")

        # Same charcoal/primary CTA as Migration "Start Migration".
        primary_button(
            btn_frame,
            self.theme,
            text="Run Restore",
            command=self._run_restore,
            width=140,
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

        self.results = ctk.CTkTextbox(results_card, height=160, activate_scrollbars=True)
        self.results.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")

    def _active_db_type(self) -> DatabaseType:
        if self.connection:
            return self.get_active_db_type()
        return DatabaseType(self.db_type.get())

    def _on_db_type_changed(self, _value: str | None = None) -> None:
        self._sync_category_controls()
        self._sync_mongo_fields()
        self._refresh_methods()

    def _sync_category_controls(self) -> None:
        if self._is_mongodb_session():
            self._category = BackupCategory.LOGICAL
            self.category_seg.grid_remove()
            self.category_logical_label.grid(row=0, column=0, sticky="w")
            self.category_hint.configure(text=category_description(BackupCategory.LOGICAL))
        else:
            self.category_logical_label.grid_remove()
            self.category_seg.grid(row=0, column=0, sticky="w")

    def _on_category_changed(self, value: str) -> None:
        if self._is_mongodb_session():
            return
        self._category = BackupCategory.PHYSICAL if value == "Physical" else BackupCategory.LOGICAL
        self.category_hint.configure(text=category_description(self._category))
        self._refresh_methods()

    def _on_method_changed(self, _value: str | None = None) -> None:
        self._sync_mongo_fields()
        self._update_command_preview()

    def _refresh_methods(self) -> None:
        db_type = self._active_db_type()
        methods = restore_methods_for(db_type, self._category)
        labels = [restore_method_label(m) for m in methods] if methods else ["(not available)"]

        self.method.configure(values=labels if methods else ["(not available for this type)"])
        if methods:
            self.method.set(labels[0])
        else:
            self.method.set("(not available for this type)")
        self._sync_mongo_fields()
        self._update_command_preview()

    def _selected_destination(self) -> str:
        return self.destination_seg.get()

    def _restore_source_type(self) -> RestoreSourceType:
        dest = self._selected_destination()
        if dest == "Linux Server":
            return RestoreSourceType.CONNECTED_SERVER
        if dest == "Remote Linux Server":
            return RestoreSourceType.OTHER_SERVER
        return RestoreSourceType.LOCAL

    def _restore_target_type(self) -> RestoreTargetType:
        if self._restore_source_type() != RestoreSourceType.OTHER_SERVER:
            return RestoreTargetType.LINUX_SERVER
        if self.restore_target_seg.get() == "Linux Server":
            return RestoreTargetType.LINUX_SERVER
        return RestoreTargetType.LOCAL

    def _is_server_restore(self) -> bool:
        source = self._restore_source_type()
        if source == RestoreSourceType.OTHER_SERVER:
            return self._restore_target_type() == RestoreTargetType.LINUX_SERVER
        return source != RestoreSourceType.LOCAL

    def _reset_other_ssh_state(self) -> None:
        self._other_ssh_connected = False
        self._other_connected_host = ""
        self._cached_other_ssh = None
        if hasattr(self, "other_ssh_status"):
            self.other_ssh_status.grid_remove()
        if hasattr(self, "other_browse_section"):
            self.other_browse_section.grid_remove()
        if hasattr(self, "restore_target_frame"):
            self.restore_target_frame.grid_remove()
        if self._other_browser:
            self._other_browser.set_enabled(False)

    def _on_other_path_typed(self) -> None:
        self._sync_restore_target_visibility()
        self._update_command_preview()

    def _sync_restore_target_visibility(self) -> None:
        if not hasattr(self, "restore_target_frame"):
            return
        if self._selected_destination() != "Remote Linux Server":
            self.restore_target_frame.grid_remove()
            return
        has_path = bool(self._resolve_server_path(self.other_source_entry, self._other_browser))
        if self._other_ssh_connected and has_path:
            self.restore_target_frame.grid()
        else:
            self.restore_target_frame.grid_remove()

    def _on_restore_target_changed(self, _value: str | None = None) -> None:
        target = self.restore_target_seg.get()
        if target == "Linux Server":
            self.restore_target_hint.configure(
                text="Run the restore on the remote Linux server over SSH (backup stays on that server)."
            )
        else:
            self.restore_target_hint.configure(
                text="Download the backup to this PC, then restore into the connected database."
            )
        self._sync_mongo_fields()
        self._update_command_preview()

    def _on_destination_changed(self, _value: str | None = None) -> None:
        self._sync_source_fields()
        if self._selected_destination() == "Remote Linux Server":
            self._prefill_other_ssh_if_empty()
        self._sync_mongo_fields()
        self._update_command_preview()

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
        self.restore_target_frame.grid_remove()
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
        self.other_ssh_status.configure(fg_color=self.theme.c("success_muted"))
        self.other_ssh_status_label.configure(
            text=f"Connected to the server — {host}",
            text_color=self.theme.c("success"),
        )
        self.other_ssh_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.other_connect_hint.configure(text="Connected — browse and select a backup below.")
        self.other_browse_section.grid(row=6, column=0, columnspan=2, sticky="ew")
        self.other_source_entry.configure(state="normal")
        self.status_label.configure(
            text=f"Connected to the server — {host}",
            text_color=self.theme.c("success"),
        )
        if self._other_browser:
            self._other_browser.set_enabled(True)
            path = (
                self.other_source_entry.get().strip()
                or self._remote_backup_root
                or "/"
            )
            self._other_browser.load_directory(path)
        self._sync_restore_target_visibility()
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
        self.restore_target_frame.grid_remove()
        if self._other_browser:
            self._other_browser.set_enabled(False)
        self.status_label.configure(text=short, text_color=self.theme.c("error"))
        if hasattr(self, "results"):
            self.results.delete("1.0", "end")
            self.results.insert("end", f"SSH connection failed:\n{message}\n")
        self._update_command_preview()
        if host_key_error is not None:
            from desktop.ssh_host_trust import maybe_offer_host_key_trust

            maybe_offer_host_key_trust(self, host_key_error)

    def _browse_local_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select restore source file",
            filetypes=[
                ("Backup files", "*.dump *.sql *.gz *.archive *.bson *.bak"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._set_local_source(path)

    def _browse_local_folder(self) -> None:
        path = filedialog.askdirectory(title="Select restore source folder")
        if path:
            self._set_local_source(path)

    def _set_local_source(self, path: str) -> None:
        self.local_source_entry.delete(0, "end")
        self.local_source_entry.insert(0, path)
        self._update_command_preview()

    def _resolve_server_path(self, entry: ctk.CTkEntry, browser: RemoteDirectoryBrowser | None) -> str:
        path = entry.get().strip()
        if browser:
            browser_path = browser.get_selected_path().strip()
            if browser_path:
                return browser_path
        return path

    def _is_remote_restore(self) -> bool:
        return self._is_server_restore()

    def _on_scope_changed(self, _value: str | None = None) -> None:
        self._mongo_scope = self._selected_scope()
        self.scope_hint.configure(text=mongo_restore_scope_hint(self._mongo_scope))
        self._sync_mongo_fields()
        self._update_command_preview()

    def _selected_scope(self) -> MongoRestoreScope:
        label = self.restore_scope.get()
        for scope in mongo_restore_scopes():
            if mongo_restore_scope_label(scope) == label:
                return scope
        return MongoRestoreScope.SPECIFIC_DATABASE

    def _on_database_changed(self, _value: str | None = None) -> None:
        self._sync_mongo_fields()
        self._update_command_preview()

    def _restore_database_name(self) -> str | None:
        return self.database.get().strip() or None

    def _populate_database_dropdown(self) -> None:
        """Fetch databases from the connected server and fill the Database dropdown."""
        if not self.connection:
            return

        def task() -> None:
            try:
                dbs = fetch_server_databases(self.connection)
                names = [d["name"] for d in dbs if d.get("name")]
                self.after(0, lambda: self._set_database_values(names))
            except Exception:
                pass

        threading.Thread(target=task, daemon=True).start()

    def _set_database_values(self, names: list[str]) -> None:
        if not hasattr(self, "database"):
            return
        current = self.database.get().strip()
        values = names or ([current] if current else [""])
        self.database.configure(values=values)
        if current and current in values:
            self.database.set(current)
        elif self.connection and self.connection.database and self.connection.database in values:
            self.database.set(self.connection.database)
        elif names:
            self.database.set(names[0])
        self._update_command_preview()

    def _sync_source_fields(self) -> None:
        dest = self._selected_destination()
        ssh_on = self._ssh_available()

        if dest != "Remote Linux Server" and self._other_ssh_connected:
            self._reset_other_ssh_state()

        self.local_frame.grid_remove()
        self.connected_frame.grid_remove()
        self.other_frame.grid_remove()

        if dest == "Local Server":
            self.local_frame.grid(row=8, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            self.destination_hint.configure(
                text="Pick a backup file or folder on this PC (Local Server)."
            )
        elif dest == "Linux Server":
            self.connected_frame.grid(row=9, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            if ssh_on:
                self.destination_hint.configure(
                    text="Restore from a backup on the Linux server linked to this connection."
                )
            else:
                self.destination_hint.configure(
                    text="Enable SSH on the connection to browse the linked Linux server."
                )
        else:
            self.other_frame.grid(row=10, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            self.destination_hint.configure(
                text="Restore from a backup on a remote Linux server (enter SSH details below)."
            )

        connected_on = dest == "Linux Server" and ssh_on
        other_remote = dest == "Remote Linux Server"

        self.connected_source_entry.configure(state="normal" if connected_on else "disabled")
        if self._connected_browser:
            self._connected_browser.set_enabled(connected_on)
            if connected_on and not self._connected_browser._entries:
                self.after(
                    100,
                    lambda: self._connected_browser.set_initial_path(
                        self.connected_source_entry.get().strip() or self._remote_backup_root
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
        self.other_source_entry.configure(state="normal" if browse_enabled else "disabled")
        if self._other_browser:
            self._other_browser.set_enabled(browse_enabled)
            if other_remote and not self._other_ssh_connected:
                self._other_browser.status_label.configure(
                    text="Enter SSH credentials and click Connect & Browse."
                )
        self._sync_restore_target_visibility()

    def _sync_mongo_fields(self) -> None:
        self._sync_category_controls()
        self._sync_source_fields()
        is_mongo = self._is_mongodb_session()
        scope = self._selected_scope() if is_mongo else None
        server_on = self._is_server_restore()

        if is_mongo:
            self.scope_label.grid()
            self.scope_col.grid()
            self.restore_scope.configure(state="normal")
            self.database_hint.pack(anchor="w", pady=(6, 0))
            self.remote_binary_frame.grid(row=11, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            if scope == MongoRestoreScope.ALL_DATABASES:
                self.database.configure(state="disabled")
                self.database_hint.configure(
                    text="Not used for all-databases restore — every database in the backup folder is restored."
                )
                self.drop_cb.grid_remove()
                self.drop_var.set(False)
            else:
                self.database.configure(state="normal")
                self.database_hint.configure(
                    text="Select the target database from the list on this server."
                )
                if scope in (MongoRestoreScope.SPECIFIC_DATABASE, MongoRestoreScope.SINGLE_COLLECTION):
                    self.drop_cb.grid()
                else:
                    self.drop_cb.grid_remove()
                    self.drop_var.set(False)
            if scope == MongoRestoreScope.SINGLE_COLLECTION:
                self.collection_frame.grid()
            else:
                self.collection_frame.grid_remove()
            if scope == MongoRestoreScope.ARCHIVE:
                self.gzip_cb.grid()
            else:
                self.gzip_cb.grid_remove()
            self.remote_binary.configure(state="normal" if server_on else "disabled")
        else:
            self.scope_label.grid_remove()
            self.scope_col.grid_remove()
            self.restore_scope.configure(state="disabled")
            self.remote_binary_frame.grid_remove()
            self.collection_frame.grid_remove()
            self.gzip_cb.grid_remove()
            self.database.configure(state="normal")
            self.database_hint.configure(
                text="Select the target database from the list on this server."
            )
            self.database_hint.pack(anchor="w", pady=(6, 0))

    def _selected_method_value(self) -> str:
        label = self.method.get()
        for method in BackupMethod:
            if restore_method_label(method) == label or method.value == label:
                return method.value
        return label

    def _build_request(self) -> RestoreRequest:
        source_type = self._restore_source_type()
        restore_target = self._restore_target_type()
        server_restore = (
            source_type == RestoreSourceType.CONNECTED_SERVER
            or (
                source_type == RestoreSourceType.OTHER_SERVER
                and restore_target == RestoreTargetType.LINUX_SERVER
            )
        )
        backup_path = ""
        remote_source: str | None = None
        external_host: str | None = None
        external_port = 22
        external_user: str | None = None
        external_password: str | None = None

        if source_type == RestoreSourceType.LOCAL:
            backup_path = self.local_source_entry.get().strip()
            if not backup_path:
                raise ValueError("Select or type a local backup file or folder path.")
        elif source_type == RestoreSourceType.CONNECTED_SERVER:
            if not self._ssh_available():
                raise ValueError(
                    "Linux Server restore requires SSH on the connection. Enable SSH in connection settings."
                )
            backup_path = self._resolve_server_path(self.connected_source_entry, self._connected_browser)
            remote_source = backup_path
            if not backup_path:
                raise ValueError("Select a backup file or folder on the Linux Server.")
        else:
            if not self._other_ssh_connected:
                raise ValueError(
                    "Connect to the Remote Linux Server first (click Connect & Browse)."
                )
            external_host = self.other_ssh_host.get().strip() or None
            external_user = self.other_ssh_user.get().strip() or None
            external_password = self.other_ssh_password.get().strip() or None
            try:
                external_port = int(self.other_ssh_port.get().strip() or "22")
            except ValueError as exc:
                raise ValueError("SSH port must be a number.") from exc
            if not external_host or not external_user:
                raise ValueError("Remote Linux Server restore requires SSH host and username.")
            backup_path = self._resolve_server_path(self.other_source_entry, self._other_browser)
            remote_source = backup_path
            if not backup_path:
                raise ValueError("Select a backup file or folder on the Remote Linux Server.")

        db_name = self._restore_database_name()
        scope = self._selected_scope() if self._is_mongodb_session() else None
        collection = self.collection.get().strip() or None
        if scope != MongoRestoreScope.SINGLE_COLLECTION:
            collection = None
        return RestoreRequest(
            profile=self.get_active_profile(),
            db_type=self._active_db_type(),
            backup_path=backup_path,
            database=db_name if scope != MongoRestoreScope.ALL_DATABASES else None,
            collection=collection,
            mongo_restore_scope=scope,
            gzip_archive=self.gzip_var.get(),
            validate_backup=self.validate_var.get(),
            restore_source=source_type,
            restore_target=restore_target,
            use_remote_exec=server_restore,
            remote_binary_path=self.remote_binary.get().strip() or None,
            remote_source_dir=remote_source,
            external_ssh_host=external_host,
            external_ssh_port=external_port,
            external_ssh_username=external_user,
            external_ssh_password=external_password,
            drop_existing=self.drop_var.get(),
        )

    def _update_command_preview(self) -> None:
        try:
            dest = self._selected_destination()
            if dest == "Local Server" and not self.local_source_entry.get().strip():
                preview = "Select or type a Local Server backup file or folder path."
            elif dest == "Linux Server":
                if not self._ssh_available():
                    preview = "Enable SSH on the connection to restore from the Linux Server."
                elif not self._resolve_server_path(self.connected_source_entry, self._connected_browser):
                    preview = "Browse the Linux Server and select a backup file or folder."
                else:
                    request = self._build_request()
                    preview = describe_restore_command(request)
            elif dest == "Remote Linux Server":
                if not self.other_ssh_host.get().strip() or not self.other_ssh_user.get().strip():
                    preview = "Enter SSH host and username for the Remote Linux Server."
                elif not self._other_ssh_connected:
                    preview = "Click Connect & Browse to connect to the Remote Linux Server."
                elif not self._resolve_server_path(self.other_source_entry, self._other_browser):
                    preview = "Browse the Remote Linux Server and select a backup file or folder."
                else:
                    request = self._build_request()
                    preview = describe_restore_command(request)
            else:
                request = self._build_request()
                preview = describe_restore_command(request)
        except Exception as exc:
            preview = str(exc)

        if not hasattr(self, "command_preview"):
            return
        self.command_preview.configure(state="normal")
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("1.0", preview)
        self.command_preview.configure(state="disabled")

    def _update_progress(self, value: float, message: str) -> None:
        self.after(0, lambda: self.progress.set(value))
        self.after(0, lambda: self.status_label.configure(text=message))

    def _run_restore(self) -> None:
        def task():
            try:
                request = self._build_request()
                self.log(f"Starting restore from {request.backup_path}")
                result = self.service.restore(request, progress_callback=self._update_progress)
                self.after(
                    0,
                    lambda: self.results.insert("end", f"Restore completed: {result.message}\n"),
                )
                self.log("Restore completed")
            except Exception as exc:
                def show_error(err: Exception = exc) -> None:
                    self.results.insert("end", f"Error: {err}\n")
                    from desktop.ssh_host_trust import maybe_offer_host_key_trust

                    maybe_offer_host_key_trust(self, err)

                self.after(0, show_error)
                self.log(f"Restore failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()
