"""Backup management view."""

from __future__ import annotations

import threading
from tkinter import filedialog

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
from desktop.connection_store import SavedServer, load_saved_servers, upsert_server
from desktop.db_catalog import fetch_mongodb_collections
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class BackupView(BaseView):
    """Backup operations with logical / physical segregation."""

    DB_TYPES = [t.value for t in DatabaseType]

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.service = BackupService()
        self._category = BackupCategory.LOGICAL
        self._local_output_dir = str(self.service.output_dir)
        self._remote_output_dir = "/mnt/backup"
        self._mongodb_collections: list[dict] = []
        self._collection_rows: list[ctk.CTkFrame] = []
        self._selected_collection_name: str | None = None
        self._build_ui()
        self._init_remote_defaults()
        self._refresh_methods()

    def _init_remote_defaults(self) -> None:
        if not self._is_mongodb_session():
            self._sync_mongo_fields()
            return

        default_dump = "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump"
        self._remote_output_dir = "/mnt/backup"
        self.remote_binary.insert(0, default_dump)
        self.remote_dir_entry.insert(0, self._remote_output_dir)
        self.use_remote_var.set(bool(self.connection and self.connection.ssh_enabled))

        saved = self._find_saved_server()
        if saved:
            if saved.local_output_dir:
                self._local_output_dir = saved.local_output_dir
                self.output_dir_entry.delete(0, "end")
                self.output_dir_entry.insert(0, saved.local_output_dir)
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
            if saved.backup_collection:
                self._selected_collection_name = saved.backup_collection
                self.collection_combo.configure(
                    values=["All collections", saved.backup_collection]
                )
                self.collection_combo.set(saved.backup_collection)

        self._sync_mongo_fields()
        self._update_collection_scope_label()

    def _is_mongodb_session(self) -> bool:
        return self._active_db_type() == DatabaseType.MONGODB

    def _find_saved_server(self) -> SavedServer | None:
        if not self.connection:
            return None
        lookup = self.connection.saved_name or self.connection.name
        for server in load_saved_servers(self.owner_username):
            if server.name == lookup and server.db_type == DatabaseType.MONGODB.value:
                return server
        return None

    def _save_mongo_prefs(self) -> None:
        if not self._is_mongodb_session() or not self.connection:
            return
        server = self._find_saved_server()
        if not server:
            return
        server.local_output_dir = self.output_dir_entry.get().strip()
        server.remote_backup_dir = self.remote_dir_entry.get().strip()
        server.mongodump_path = self.remote_binary.get().strip()
        server.backup_folder_name = self.backup_folder_entry.get().strip()
        coll = self._selected_collection()
        server.backup_collection = coll or ""
        upsert_server(server, self.owner_username)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)

        context = self.create_connection_context(scroll)
        if context:
            context.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            scroll,
            text="Backup Management",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).pack(anchor="w", pady=(0, 12))

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
        ctk.CTkLabel(form, text=conn_label).grid(row=1, column=1, padx=16, pady=10, sticky="w")

        ctk.CTkLabel(form, text="Backup Type:").grid(row=2, column=0, padx=16, pady=10, sticky="nw")
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
            form, values=["pg_dump"], command=self._on_method_changed
        )
        self.method.set("pg_dump")
        self.method.grid(row=3, column=1, padx=16, pady=10, sticky="ew")

        ctk.CTkLabel(form, text="Database:").grid(row=4, column=0, padx=16, pady=10, sticky="nw")
        db_col = ctk.CTkFrame(form, fg_color="transparent")
        db_col.grid(row=4, column=1, padx=16, pady=10, sticky="ew")
        db_col.grid_columnconfigure(0, weight=1)

        self.database = ctk.CTkEntry(
            db_col,
            placeholder_text="Leave empty to backup all databases",
        )
        if self.connection and self.get_active_db_type() != DatabaseType.MONGODB:
            self.database.insert(0, self.connection.database)
        self.database.pack(fill="x")
        self.database.bind("<KeyRelease>", self._on_database_changed)

        self.database_hint = ctk.CTkLabel(
            db_col,
            text="MongoDB: leave empty for a full server backup (all databases). Type a name to backup one database only.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self.database_hint.pack(anchor="w", pady=(6, 0))

        self.collections_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.collections_frame.grid(row=5, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
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
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.output_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.output_frame.grid(row=6, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.output_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.output_frame, text="Output dir:").grid(row=0, column=0, sticky="w", pady=6)

        output_path_row = ctk.CTkFrame(self.output_frame, fg_color="transparent")
        output_path_row.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        output_path_row.grid_columnconfigure(0, weight=1)

        self.output_dir_entry = ctk.CTkEntry(
            output_path_row,
            placeholder_text="Folder on this PC where backup files are saved",
        )
        self.output_dir_entry.insert(0, self._local_output_dir)
        self.output_dir_entry.grid(row=0, column=0, sticky="ew")
        self.output_dir_entry.bind("<KeyRelease>", lambda _e: self._on_output_dir_changed())

        ctk.CTkButton(
            output_path_row,
            text="Browse",
            width=90,
            command=self._browse_output_dir,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(
            self.output_frame,
            text=(
                "Folder on this PC. With remote unchecked, mongodump runs on the Linux server "
                "and files are downloaded here. Click Browse to choose the folder."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        ).grid(row=1, column=1, padx=(12, 0), sticky="w")

        ctk.CTkLabel(self.output_frame, text="Backup folder:").grid(row=2, column=0, sticky="w", pady=6)
        self.backup_folder_entry = ctk.CTkEntry(
            self.output_frame,
            placeholder_text="e.g. db_backup or prod_db1 (timestamp added automatically)",
        )
        self.backup_folder_entry.grid(row=2, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.backup_folder_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(
            self.output_frame,
            text="Optional. Use <timestamp> in the name to control where the date is added.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        ).grid(row=3, column=1, padx=(12, 0), sticky="w")

        self.gzip_var = ctk.BooleanVar(value=False)
        self.gzip_cb = ctk.CTkCheckBox(
            self.output_frame,
            text="Gzip archive (--gzip)",
            variable=self.gzip_var,
            command=self._update_command_preview,
        )
        self.gzip_cb.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.remote_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.remote_frame.grid(row=7, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.remote_frame.grid_columnconfigure(1, weight=1)

        self.use_remote_var = ctk.BooleanVar(value=False)
        self.remote_cb = ctk.CTkCheckBox(
            self.remote_frame,
            text="Run mongodump on Linux server (remote)",
            variable=self.use_remote_var,
            command=self._on_remote_toggle,
        )
        self.remote_cb.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ctk.CTkLabel(self.remote_frame, text="Mongodump binary:").grid(row=1, column=0, sticky="w", pady=6)
        self.remote_binary = ctk.CTkEntry(self.remote_frame)
        self.remote_binary.grid(row=1, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.remote_binary.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(self.remote_frame, text="Remote dir:").grid(row=2, column=0, sticky="w", pady=6)
        self.remote_dir_entry = ctk.CTkEntry(
            self.remote_frame,
            placeholder_text="Linux folder on the server, e.g. /mnt/backup",
        )
        self.remote_dir_entry.grid(row=2, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.remote_dir_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(form, text="Preview:").grid(row=8, column=0, padx=16, pady=10, sticky="nw")
        self.command_preview = ctk.CTkTextbox(form, height=88, activate_scrollbars=True)
        self.command_preview.grid(row=8, column=1, padx=16, pady=10, sticky="ew")
        self.command_preview.configure(state="disabled")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=9, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="Run Backup",
            command=self._run_backup,
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
        ).pack(side="left", padx=(0, 10))

        # Apply Retention — disabled in UI for now; backend apply_retention_policy() kept for future use.

        ctk.CTkButton(
            btn_frame,
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

        self.results = ctk.CTkTextbox(results_card, height=160, activate_scrollbars=True)
        self.results.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")

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
        self._sync_mongo_fields()
        self._update_command_preview()

    def _on_output_dir_changed(self) -> None:
        self._local_output_dir = self.output_dir_entry.get().strip() or self._local_output_dir
        self._update_command_preview()

    def _browse_output_dir(self) -> None:
        initial = self.output_dir_entry.get().strip() or self._local_output_dir
        path = filedialog.askdirectory(
            title="Select folder on this PC to save backup files",
            initialdir=initial if initial else None,
        )
        if path:
            self._local_output_dir = path
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, path)
            self._update_command_preview()

    def _is_remote_backup(self) -> bool:
        return self.use_remote_var.get() and self._is_mongodb_session()

    def _on_database_changed(self, _event=None) -> None:
        self._sync_mongo_fields()
        self._update_command_preview()

    def _backup_database_name(self) -> str | None:
        return self.database.get().strip() or None

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
        self._save_mongo_prefs()

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

    def _populate_collections(self, collections: list[dict], error: str | None = None) -> None:
        self._mongodb_collections = collections
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

        self._make_collection_row(key="", title="All collections", subtitle="Full database")
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
                self.after(0, lambda: self._populate_collections(collections))
            except Exception as exc:
                self.after(0, lambda: self._populate_collections([], str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _on_remote_toggle(self) -> None:
        self._sync_mongo_fields()
        self._update_command_preview()

    def _sync_mongo_fields(self) -> None:
        is_mongo = self._is_mongodb_session()
        has_db = bool(self._backup_database_name())
        if is_mongo:
            self.output_frame.grid()
            self.remote_frame.grid()
            self.database_hint.pack(anchor="w", pady=(6, 0))
            if has_db:
                self.collections_frame.grid()
            else:
                self.collections_frame.grid_remove()
        else:
            self.output_frame.grid_remove()
            self.remote_frame.grid_remove()
            self.collections_frame.grid_remove()
            self.database_hint.pack_forget()

        remote_on = self._is_remote_backup()
        self.remote_binary.configure(state="normal")
        self.remote_dir_entry.configure(state="normal" if remote_on else "disabled")

    def _sync_form_state(self) -> None:
        method_value = self._selected_method_value()
        physical = self._category == BackupCategory.PHYSICAL
        cluster_wide = method_value in (BackupMethod.PG_DUMPALL.value, BackupMethod.PG_BASEBACKUP.value)

        if cluster_wide or physical:
            self.database.configure(state="disabled")
        else:
            self.database.configure(state="normal")

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

        remote = self._is_remote_backup()
        local_dir = self.output_dir_entry.get().strip() or None
        db_name = self._backup_database_name()
        return BackupRequest(
            profile=self.get_active_profile(),
            db_type=self._active_db_type(),
            method=BackupMethod(method_value),
            database=db_name,
            compress=False,
            local_output_dir=local_dir if self._is_mongodb_session() and not remote else None,
            use_remote_exec=remote,
            remote_binary_path=self.remote_binary.get().strip() or None,
            remote_output_dir=self.remote_dir_entry.get().strip() or None,
            backup_folder_name=self.backup_folder_entry.get().strip() or None,
            collection=self._selected_collection() if db_name else None,
            gzip_archive=bool(self.gzip_var.get()) if self._is_mongodb_session() else False,
        )

    def _update_command_preview(self) -> None:
        try:
            request = self._build_request()
            preview = describe_backup_command(request)
            # if request.compress and self._category == BackupCategory.LOGICAL:
            #     preview += "  →  gzip compression after export"
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
                if request.collection:
                    self.log(f"Single collection: {request.database}.{request.collection}")
                result = self.service.create_backup(request, progress_callback=self._update_progress)
                self.after(0, self._save_mongo_prefs)
                self.after(
                    0,
                    lambda p=result.output_path: self.results.insert(
                        "end", f"[{category}] Success:\n{p}\n\nOpen the backups folder or click List Backups.\n"
                    ),
                )
                self.log(f"Backup completed: {result.output_path}")
            except Exception as exc:
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"Backup failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()

    def _list_backups(self) -> None:
        list_remote = self._is_remote_backup()
        local_dir = self.output_dir_entry.get().strip() or self._local_output_dir
        remote_dir = self.remote_dir_entry.get().strip() or self._remote_output_dir
        backups = self.service.list_backups(
            self.get_active_profile(),
            local_dir=local_dir,
            remote_dir=remote_dir,
            list_remote=list_remote,
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
