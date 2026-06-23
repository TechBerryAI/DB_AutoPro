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
        self._build_ui()
        self._init_restore_defaults()
        self._sync_mongo_fields()
        self._refresh_methods()

    def _init_restore_defaults(self) -> None:
        if not self._is_mongodb_session():
            return
        saved = self._find_saved_server()
        if saved and saved.remote_backup_dir:
            self._remote_backup_root = saved.remote_backup_dir
        self.use_remote_var.set(bool(self.connection and self.connection.ssh_enabled))
        if saved and saved.mongodump_path:
            self.remote_binary.delete(0, "end")
            self.remote_binary.insert(0, saved.mongodump_path.replace("mongodump", "mongorestore"))
        elif not self.remote_binary.get().strip():
            self.remote_binary.insert(0, "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongorestore")

    def _ssh_options(self) -> dict:
        from backend.connectors.base import resolve_connection
        from backend.services.ssh_client import ssh_options_from_connection

        conn = resolve_connection(self.get_active_profile())
        return ssh_options_from_connection(conn)

    def _on_remote_path_selected(self, path: str) -> None:
        self.remote_source_entry.delete(0, "end")
        self.remote_source_entry.insert(0, path)
        self._update_command_preview()

    def _is_mongodb_session(self) -> bool:
        return self._active_db_type() == DatabaseType.MONGODB

    def _find_saved_server(self) -> SavedServer | None:
        if not self.connection:
            return None
        lookup = self.connection.saved_name or self.connection.name
        for server in load_saved_servers():
            if server.name == lookup and server.db_type == DatabaseType.MONGODB.value:
                return server
        return None

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
            text="Restore Management",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).pack(anchor="w", pady=(0, 12))

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

        self.database = ctk.CTkEntry(
            db_col,
            placeholder_text="Target database name (e.g. db1)",
        )
        self.database.pack(fill="x")
        self.database.bind("<KeyRelease>", self._on_database_changed)

        self.database_hint = ctk.CTkLabel(
            db_col,
            text="Target database name (created automatically if it does not exist).",
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

        self.backup_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.backup_frame.grid(row=8, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.backup_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.backup_frame, text="Local restore source:").grid(row=0, column=0, sticky="w", pady=6)

        backup_path_row = ctk.CTkFrame(self.backup_frame, fg_color="transparent")
        backup_path_row.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="ew")
        backup_path_row.grid_columnconfigure(0, weight=1)

        self.backup_path = ctk.CTkEntry(
            backup_path_row,
            placeholder_text="File or folder path — type or browse any backup location",
        )
        self.backup_path.grid(row=0, column=0, sticky="ew")
        self.backup_path.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkButton(
            backup_path_row,
            text="File",
            width=60,
            command=self._browse_backup_file,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkButton(
            backup_path_row,
            text="Folder",
            width=70,
            command=self._browse_backup_folder,
        ).grid(row=0, column=2, padx=(6, 0))

        self.backup_hint = ctk.CTkLabel(
            self.backup_frame,
            text="Pick a file or folder on this PC (used when restoring locally).",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            justify="left",
            anchor="w",
        )
        self.backup_hint.grid(row=1, column=1, padx=(12, 0), sticky="w")

        self.local_source_label = ctk.CTkLabel(
            form,
            text="",
            font=ctk.CTkFont(size=1),
        )

        self.remote_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.remote_frame.grid(row=7, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
        self.remote_frame.grid_columnconfigure(1, weight=1)

        self.use_remote_var = ctk.BooleanVar(value=False)
        self.remote_cb = ctk.CTkCheckBox(
            self.remote_frame,
            text="Run mongorestore on Linux server (remote)",
            variable=self.use_remote_var,
            command=self._on_remote_toggle,
        )
        self.remote_cb.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ctk.CTkLabel(self.remote_frame, text="Mongorestore binary:").grid(row=1, column=0, sticky="w", pady=6)
        self.remote_binary = ctk.CTkEntry(self.remote_frame)
        self.remote_binary.grid(row=1, column=1, padx=(12, 0), pady=6, sticky="ew")
        self.remote_binary.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(self.remote_frame, text="Remote source:").grid(row=2, column=0, sticky="nw", pady=6)
        remote_src_col = ctk.CTkFrame(self.remote_frame, fg_color="transparent")
        remote_src_col.grid(row=2, column=1, padx=(12, 0), pady=6, sticky="ew")
        remote_src_col.grid_columnconfigure(0, weight=1)

        self.remote_source_entry = ctk.CTkEntry(
            remote_src_col,
            placeholder_text="Path on Linux server, e.g. /mnt/backup/All_backup_...",
        )
        self.remote_source_entry.grid(row=0, column=0, sticky="ew")
        self.remote_source_entry.bind("<KeyRelease>", lambda _e: self._update_command_preview())

        ctk.CTkLabel(
            self.remote_frame,
            text="Remote directories on Linux server",
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
            initial_path=self._remote_backup_root,
            on_selection_changed=self._on_remote_path_selected,
        )
        self._remote_browser.grid(row=0, column=0, sticky="ew")

        self.gzip_var = ctk.BooleanVar(value=False)
        self.gzip_cb = ctk.CTkCheckBox(
            form,
            text="Gzip archive (--gzip)",
            variable=self.gzip_var,
            command=self._update_command_preview,
        )
        self.gzip_cb.grid(row=9, column=0, columnspan=2, padx=16, pady=6, sticky="w")

        self.drop_var = ctk.BooleanVar(value=False)
        self.drop_cb = ctk.CTkCheckBox(
            form,
            text="Drop existing collections before restore (--drop)",
            variable=self.drop_var,
            command=self._update_command_preview,
        )
        self.drop_cb.grid(row=10, column=0, columnspan=2, padx=16, pady=6, sticky="w")

        self.validate_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            form,
            text="Validate before restore",
            variable=self.validate_var,
        ).grid(row=11, column=0, columnspan=2, padx=16, pady=6, sticky="w")

        ctk.CTkLabel(form, text="Preview:").grid(row=12, column=0, padx=16, pady=10, sticky="nw")
        self.command_preview = ctk.CTkTextbox(form, height=88, activate_scrollbars=True)
        self.command_preview.grid(row=12, column=1, padx=16, pady=10, sticky="ew")
        self.command_preview.configure(state="disabled")

        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=13, column=0, columnspan=2, padx=16, pady=(4, 16), sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="Run Restore",
            command=self._run_restore,
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
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

    def _browse_backup_folder(self) -> None:
        initial = self.backup_path.get().strip() or None
        path = filedialog.askdirectory(
            title="Select restore source folder",
            initialdir=initial if initial else None,
        )
        if path:
            self._set_restore_source(path)

    def _browse_backup_file(self) -> None:
        initial = self.backup_path.get().strip() or None
        path = filedialog.askopenfilename(
            title="Select restore source file",
            initialdir=initial if initial else None,
            filetypes=[
                ("Backup files", "*.bson *.archive *.gz *.sql *.dump"),
                ("MongoDB archive", "*.archive"),
                ("BSON", "*.bson"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._set_restore_source(path)

    def _set_restore_source(self, path: str) -> None:
        self.backup_path.delete(0, "end")
        self.backup_path.insert(0, path)
        self._update_command_preview()

    def _is_remote_restore(self) -> bool:
        return self.use_remote_var.get() and self._is_mongodb_session()

    def _on_remote_toggle(self) -> None:
        self._sync_mongo_fields()
        if self._is_remote_restore() and self._remote_browser:
            root = self.remote_source_entry.get().strip() or self._remote_backup_root
            self._remote_browser.set_initial_path(root)
        self._update_command_preview()

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

    def _on_database_changed(self, _event=None) -> None:
        self._sync_mongo_fields()
        self._update_command_preview()

    def _restore_database_name(self) -> str | None:
        return self.database.get().strip() or None

    def _sync_mongo_fields(self) -> None:
        self._sync_category_controls()
        is_mongo = self._is_mongodb_session()
        scope = self._selected_scope() if is_mongo else None
        remote_on = self._is_remote_restore()

        if is_mongo:
            self.scope_label.grid()
            self.scope_col.grid()
            self.restore_scope.configure(state="normal")
            self.database_hint.pack(anchor="w", pady=(6, 0))
            if remote_on:
                self.remote_frame.grid(row=7, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
                self.backup_frame.grid(row=8, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
                self.backup_hint.configure(
                    text="Optional: pick a local folder/file to upload to the server before restore."
                )
            else:
                self.remote_frame.grid_remove()
                self.backup_frame.grid(row=7, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
                self.backup_hint.configure(
                    text="Pick a file or folder on this PC to restore locally."
                )
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
                    text="Target database name (created automatically if it does not exist)."
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
        else:
            self.scope_label.grid_remove()
            self.scope_col.grid_remove()
            self.restore_scope.configure(state="disabled")
            self.remote_frame.grid_remove()
            self.backup_frame.grid(row=7, column=0, columnspan=2, padx=16, pady=10, sticky="ew")
            self.collection_frame.grid_remove()
            self.gzip_cb.grid_remove()
            self.database_hint.pack_forget()

        if is_mongo:
            self.remote_binary.configure(state="normal" if remote_on else "disabled")
            self.remote_source_entry.configure(state="normal" if remote_on else "disabled")
        if self._remote_browser:
            self._remote_browser.set_enabled(remote_on)
            if remote_on and not self._remote_browser._entries:
                self.after(100, lambda: self._remote_browser.set_initial_path(
                    self.remote_source_entry.get().strip() or self._remote_backup_root
                ))

    def _selected_method_value(self) -> str:
        label = self.method.get()
        for method in BackupMethod:
            if restore_method_label(method) == label or method.value == label:
                return method.value
        return label

    def _build_request(self) -> RestoreRequest:
        remote = self._is_remote_restore()
        remote_source = self.remote_source_entry.get().strip() or None
        if remote and self._remote_browser:
            browser_path = self._remote_browser.get_selected_path().strip()
            if browser_path and not remote_source:
                remote_source = browser_path

        local_source = self.backup_path.get().strip()
        if remote:
            if remote_source:
                backup_path = remote_source
            elif local_source:
                backup_path = local_source
            else:
                raise ValueError(
                    "Select a backup on the Linux server, or choose a local path to upload."
                )
        elif not local_source:
            raise ValueError("Select or type a restore source file/folder path.")
        else:
            backup_path = local_source

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
            use_remote_exec=remote,
            remote_binary_path=self.remote_binary.get().strip() or None,
            remote_source_dir=remote_source if remote else None,
            drop_existing=self.drop_var.get(),
        )

    def _update_command_preview(self) -> None:
        try:
            remote = self._is_remote_restore()
            remote_source = self.remote_source_entry.get().strip()
            local_source = self.backup_path.get().strip()
            browser_path = self._remote_browser.get_selected_path().strip() if self._remote_browser else ""
            if remote and not remote_source and not local_source and not browser_path:
                preview = "Browse remote directories on the server or pick a local folder to upload."
            elif not remote and not local_source:
                preview = "Select or type a restore source file/folder path."
            else:
                request = self._build_request()
                preview = describe_restore_command(request)
        except ValueError as exc:
            preview = str(exc)

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
                self.after(0, lambda: self.results.insert("end", f"Error: {exc}\n"))
                self.log(f"Restore failed: {exc}", "ERROR")

        threading.Thread(target=task, daemon=True).start()
