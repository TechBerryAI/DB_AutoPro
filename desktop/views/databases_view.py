"""Professional Database Explorer view (pgAdmin / DBeaver style)."""

from __future__ import annotations

import threading
from typing import Any

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.explorer.factory import get_explorer_provider
from desktop.explorer.provider import (
    BackupInfo,
    ConfigInfo,
    DatabaseSummary,
    DbStatistics,
    ExtensionInfo,
    MaintenanceInfo,
    ObjectRef,
    SchemaNode,
    StorageSummary,
    TableDetails,
)
from desktop.explorer.widgets import kv_grid, section_card, simple_table, tree_row
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView

_SCHEMA_OBJECT_TYPES: list[tuple[str, str, str]] = [
    ("Tables", "tables", "table"),
    ("Views", "views", "eye"),
    ("Materialized Views", "materialized_views", "layers"),
    ("Functions", "functions", "function-square"),
    ("Procedures", "procedures", "play"),
    ("Sequences", "sequences", "hash"),
    ("Triggers", "triggers", "zap"),
    ("Indexes", "indexes", "list"),
    ("Foreign Keys", "foreign_keys", "link"),
]

_TOP_SECTIONS: list[tuple[str, str, str]] = [
    ("general", "General Information", "info"),
    ("schemas", "Schemas", "folder-tree"),
    ("storage", "Storage", "hard-drive"),
    ("statistics", "Statistics", "chart-bar"),
    ("configuration", "Configuration", "settings"),
    ("extensions", "Extensions", "puzzle"),
    ("maintenance", "Maintenance", "wrench"),
    ("backup", "Backup Information", "archive"),
]


class DatabasesView(BaseView):
    """Enterprise database metadata explorer for the connected server."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self._provider = None
        self._list_frame: ctk.CTkFrame | None = None
        self._expanded_db: str | None = None
        self._detail_hosts: dict[str, ctk.CTkFrame] = {}
        self._chevron_labels: dict[str, ctk.CTkLabel] = {}
        self._summaries: dict[str, DatabaseSummary] = {}
        self._schema_cache: dict[str, list[SchemaNode]] = {}
        self._expanded_schemas: set[str] = set()
        self._expanded_object_kinds: set[str] = set()
        self._selected_node: tuple[str, ...] | None = None
        self._tree_host: ctk.CTkScrollableFrame | None = None
        self._detail_host: ctk.CTkScrollableFrame | None = None
        self._object_cache: dict[str, list[ObjectRef]] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        # Fixed page chrome — stays put while inventory scrolls.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(12, 8))
        header.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew")
        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkFrame(
            title_row,
            width=4,
            height=22,
            corner_radius=2,
            fg_color=self.theme.c("primary"),
        ).pack(side="left", padx=(0, 10))
        text_col = ctk.CTkFrame(title_row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            text_col,
            text="Databases",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            text_col,
            text="Database Explorer — metadata, schemas, and object structure",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        ctk.CTkButton(
            header,
            text="Refresh",
            width=96,
            height=30,
            command=self.refresh,
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 8))
        self._scroll.grid_columnconfigure(0, weight=1)

        self._list_frame = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._list_frame.pack(fill="both", expand=True)

    def refresh(self) -> None:
        if not self._list_frame:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()
        self._detail_hosts.clear()
        self._chevron_labels.clear()
        self._summaries.clear()
        self._schema_cache.clear()
        self._expanded_schemas.clear()
        self._expanded_object_kinds.clear()
        self._object_cache.clear()
        self._selected_node = None
        self._tree_host = None
        self._detail_host = None
        previous = self._expanded_db
        self._expanded_db = None
        self._provider = None

        if not self.connection:
            ctk.CTkLabel(
                self._list_frame,
                text="Connect to a server first (Servers → Connect).",
                font=ctk.CTkFont(size=13),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", pady=8)
            return

        self._provider = get_explorer_provider(self.connection)
        loading = ctk.CTkLabel(
            self._list_frame,
            text="Loading databases…",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
        )
        loading.pack(anchor="w", pady=8)

        def task() -> None:
            try:
                databases = self._provider.list_databases()
                self.after(0, lambda: self._render_database_list(databases, previous))
            except Exception as exc:
                self.after(0, lambda: self._render_list_error(str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _render_list_error(self, message: str) -> None:
        if not self._list_frame:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self._list_frame,
            text=f"Could not load databases: {message}",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("error"),
            anchor="w",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=8)

    def _render_database_list(
        self, databases: list[DatabaseSummary], previous: str | None
    ) -> None:
        if not self._list_frame:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()

        if not databases:
            ctk.CTkLabel(
                self._list_frame,
                text="No databases found on this server.",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
            ).pack(anchor="w", pady=8)
            return

        ctk.CTkLabel(
            self._list_frame,
            text="DATABASE INVENTORY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self.theme.c("primary"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 2))
        inventory_hint = (
            "Expand a container (CDB$ROOT / PDB$SEED / ORCLPDB1) to list users with status."
            if self.connection and self.connection.db_type == DatabaseType.ORACLE
            else "Expand a database to open the explorer (schemas, storage, configuration, backups)."
        )
        ctk.CTkLabel(
            self._list_frame,
            text=inventory_hint,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        for db in databases:
            self._summaries[db.name] = db
            self._add_database_card(db)

        # Keep last cards clear of the status footer while scrolling.
        ctk.CTkFrame(self._list_frame, fg_color="transparent", height=28).pack(fill="x")

        if previous and previous in self._detail_hosts:
            self._toggle_database(previous)

    def _add_database_card(self, db: DatabaseSummary) -> None:
        assert self._list_frame is not None
        block = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        block.pack(fill="x", pady=3)

        card = ctk.CTkFrame(
            block,
            fg_color=self.theme.c("bg_card"),
            corner_radius=10,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
            cursor="hand2",
        )
        card.pack(fill="x")

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=8)

        chevron = ctk.CTkLabel(
            top,
            text="▶",
            width=22,
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
        )
        chevron.pack(side="left")
        self._chevron_labels[db.name] = chevron

        ctk.CTkLabel(
            top,
            text=db.name,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(side="left", padx=(6, 12))

        status_color = (
            self.theme.c("success")
            if db.status in {"online", "read_write", "open"}
            else self.theme.c("warning")
        )
        status_text = db.status.replace("_", " ").upper()
        ctk.CTkLabel(
            top,
            text=status_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=status_color,
            fg_color=self.theme.c("bg_elevated"),
            corner_radius=8,
            width=70,
            height=20,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            top,
            text=db.size,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).pack(side="right")

        detail = ctk.CTkFrame(
            block,
            fg_color=self.theme.c("bg_elevated"),
            corner_radius=10,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
        )
        self._detail_hosts[db.name] = detail

        def on_click(_event=None, name=db.name) -> None:
            self._toggle_database(name)

        for widget in (card, top, chevron):
            widget.bind("<Button-1>", on_click)
            for child in widget.winfo_children():
                child.bind("<Button-1>", on_click)

    def _toggle_database(self, name: str) -> None:
        detail = self._detail_hosts.get(name)
        if detail is None:
            return

        if self._expanded_db and self._expanded_db != name:
            prev = self._detail_hosts.get(self._expanded_db)
            if prev is not None:
                prev.pack_forget()
            prev_chev = self._chevron_labels.get(self._expanded_db)
            if prev_chev is not None:
                prev_chev.configure(text="▶")

        if self._expanded_db == name:
            detail.pack_forget()
            self._expanded_db = None
            chev = self._chevron_labels.get(name)
            if chev is not None:
                chev.configure(text="▶")
            return

        self._expanded_db = name
        chev = self._chevron_labels.get(name)
        if chev is not None:
            chev.configure(text="▼")
        detail.pack(fill="x", expand=False, padx=(16, 0), pady=(4, 2))
        if self.connection and self.connection.db_type == DatabaseType.ORACLE:
            self._build_oracle_container_shell(name, detail)
        else:
            self._build_explorer_shell(name, detail)

    def _oracle_status_colors(self, status: str) -> tuple[str, str]:
        key = (status or "").strip().upper()
        if key in {"OPEN", "ONLINE", "READ WRITE", "READWRITE", "READ_WRITE"}:
            return self.theme.c("success"), self.theme.c("bg_elevated")
        if "LOCKED" in key or "EXPIRED" in key:
            return self.theme.c("warning"), self.theme.c("bg_elevated")
        return self.theme.c("text_muted"), self.theme.c("bg_elevated")

    def _build_oracle_container_shell(self, database: str, host: ctk.CTkFrame) -> None:
        for child in host.winfo_children():
            child.destroy()

        header = ctk.CTkFrame(host, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(
            header,
            text=f"Container · {database}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Users / schemas in this container (status from DBA_USERS / inventory).",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        loading = ctk.CTkLabel(
            host,
            text="Loading users…",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        )
        loading.pack(anchor="w", padx=12, pady=6)

        def task() -> None:
            try:
                users = self._provider.list_schemas(database) if self._provider else []
                info = self._provider.get_general_info(database) if self._provider else {}
                self.after(0, lambda: self._render_oracle_users(host, database, users, info))
            except Exception as exc:
                self.after(
                    0,
                    lambda: self._render_oracle_users(host, database, [], {"Error": str(exc)}),
                )

        threading.Thread(target=task, daemon=True).start()

    def _render_oracle_users(
        self,
        host: ctk.CTkFrame,
        database: str,
        users: list[SchemaNode],
        info: dict[str, str],
    ) -> None:
        for child in host.winfo_children():
            child.destroy()

        meta = ctk.CTkFrame(
            host,
            fg_color=self.theme.c("bg_card"),
            corner_radius=8,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
        )
        meta.pack(fill="x", padx=10, pady=(6, 6))
        ctk.CTkLabel(
            meta,
            text="General Information",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(8, 2))
        for key, value in info.items():
            if key == "Note":
                continue
            row = ctk.CTkFrame(meta, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(
                row,
                text=f"{key}:",
                width=130,
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=self.theme.c("text_muted"),
            ).pack(side="left")
            ctk.CTkLabel(
                row,
                text=str(value),
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=self.theme.c("text_primary"),
            ).pack(side="left", fill="x", expand=True)
        note = info.get("Note")
        if note:
            ctk.CTkLabel(
                meta,
                text=note,
                font=ctk.CTkFont(size=11),
                text_color=self.theme.c("text_muted"),
                anchor="w",
                justify="left",
                wraplength=620,
            ).pack(anchor="w", padx=12, pady=(4, 8))
        else:
            ctk.CTkFrame(meta, fg_color="transparent", height=6).pack()

        # Use the page scroll only — avoid nested scrollable frames.
        list_host = ctk.CTkFrame(host, fg_color="transparent")
        list_host.pack(fill="x", padx=6, pady=(0, 8))

        if not users:
            ctk.CTkLabel(
                list_host,
                text=(
                    "No users visible for this container with the current login. "
                    "Enable SSH (sqlplus / as sysdba) or connect as SYS/SYSTEM to the CDB "
                    "to load CDB$ROOT users. ORCLPDB1 users load from the PDB connection."
                    if database.upper() != "ORCLPDB1"
                    else "No users found in this container."
                ),
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                justify="left",
                anchor="w",
                wraplength=640,
            ).pack(anchor="w", padx=8, pady=6)
            return

        ctk.CTkLabel(
            list_host,
            text=f"USERS ({len(users)})",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self.theme.c("primary"),
            anchor="w",
        ).pack(anchor="w", padx=8, pady=(2, 4))

        for user in users:
            card = ctk.CTkFrame(
                list_host,
                fg_color=self.theme.c("bg_card"),
                corner_radius=8,
                border_width=1,
                border_color=self.theme.c("border_subtle"),
            )
            card.pack(fill="x", padx=4, pady=2)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=6)
            ctk.CTkLabel(
                row,
                text=user.name,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left")
            status = (user.status or "OPEN").strip()
            color, bg = self._oracle_status_colors(status)
            ctk.CTkLabel(
                row,
                text=status.upper(),
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=color,
                fg_color=bg,
                corner_radius=8,
                height=20,
            ).pack(side="right")

    def _build_explorer_shell(self, database: str, host: ctk.CTkFrame) -> None:
        for child in host.winfo_children():
            child.destroy()

        if not self._provider or not self._provider.supports_object_explorer:
            ctk.CTkLabel(
                host,
                text=(
                    f"Object explorer is not available for "
                    f"{self.connection.db_type.value.upper() if self.connection else 'this engine'} yet.\n"
                    "Database inventory is listed above; use Backup / SQL tools for deeper work."
                ),
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                justify="left",
                anchor="w",
            ).pack(anchor="w", padx=16, pady=16)
            # Still show general info from stub
            self._selected_node = ("general",)
            body = ctk.CTkScrollableFrame(host, fg_color="transparent", height=220)
            body.pack(fill="both", expand=True, padx=8, pady=8)
            self._detail_host = body
            self._load_section(database, ("general",))
            return

        shell = ctk.CTkFrame(host, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=8, pady=8)
        shell.grid_columnconfigure(0, weight=0, minsize=280)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(
            shell,
            fg_color=self.theme.c("bg_card"),
            corner_radius=8,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
            width=280,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_propagate(False)
        ctk.CTkLabel(
            left,
            text=f"Explorer · {database}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(10, 4))

        self._tree_host = ctk.CTkScrollableFrame(left, fg_color="transparent", height=420)
        self._tree_host.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        right = ctk.CTkFrame(
            shell,
            fg_color=self.theme.c("bg_card"),
            corner_radius=8,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
        )
        right.grid(row=0, column=1, sticky="nsew")
        self._detail_host = ctk.CTkScrollableFrame(right, fg_color="transparent", height=420)
        self._detail_host.pack(fill="both", expand=True, padx=4, pady=4)

        self._selected_node = ("general",)
        self._expanded_schemas.add(database)
        self._render_tree(database)
        self._load_section(database, ("general",))

        # Prefetch schemas for counts
        def task() -> None:
            try:
                schemas = self._provider.list_schemas(database)
                self._schema_cache[database] = schemas
                self._expanded_schemas.add(database)
                self.after(0, lambda: self._render_tree(database))
            except Exception:
                pass

        threading.Thread(target=task, daemon=True).start()

    def _render_tree(self, database: str) -> None:
        host = self._tree_host
        if host is None or self._expanded_db != database:
            return
        for child in host.winfo_children():
            child.destroy()

        schemas = self._schema_cache.get(database, [])
        schema_count = len(schemas)

        for key, label, icon in _TOP_SECTIONS:
            if key == "schemas":
                schemas_expanded = database in self._expanded_schemas
                tree_row(
                    host,
                    self.theme,
                    label=f"{label} ({schema_count})" if schema_count else label,
                    count=schema_count if schema_count else None,
                    expandable=True,
                    expanded=schemas_expanded,
                    selected=self._selected_node == ("schemas",),
                    icon_name=icon,
                    on_click=lambda: self._select_node(database, ("schemas",)),
                    on_toggle=lambda: self._toggle_schemas_root(database),
                )
                if schemas_expanded:
                    for schema in schemas:
                        self._render_schema_node(database, schema)
            else:
                tree_row(
                    host,
                    self.theme,
                    label=label,
                    selected=self._selected_node == (key,),
                    icon_name=icon,
                    on_click=lambda k=key: self._select_node(database, (k,)),
                )

    def _toggle_schemas_root(self, database: str) -> None:
        if database in self._expanded_schemas:
            self._expanded_schemas.discard(database)
        else:
            self._expanded_schemas.add(database)
            if database not in self._schema_cache and self._provider:
                def task() -> None:
                    try:
                        schemas = self._provider.list_schemas(database)
                        self._schema_cache[database] = schemas
                        self.after(0, lambda: self._render_tree(database))
                    except Exception as exc:
                        self.after(
                            0,
                            lambda: self._show_detail_error(f"Could not load schemas: {exc}"),
                        )

                threading.Thread(target=task, daemon=True).start()
                return
        self._render_tree(database)

    def _schema_total(self, schema: SchemaNode) -> int:
        return (
            schema.table_count
            + schema.view_count
            + schema.matview_count
            + schema.function_count
            + schema.procedure_count
            + schema.sequence_count
            + schema.trigger_count
            + schema.index_count
            + schema.foreign_key_count
        )

    def _schema_kind_count(self, schema: SchemaNode, kind: str) -> int:
        return {
            "tables": schema.table_count,
            "views": schema.view_count,
            "materialized_views": schema.matview_count,
            "functions": schema.function_count,
            "procedures": schema.procedure_count,
            "sequences": schema.sequence_count,
            "triggers": schema.trigger_count,
            "indexes": schema.index_count,
            "foreign_keys": schema.foreign_key_count,
        }.get(kind, 0)

    def _render_schema_node(self, database: str, schema: SchemaNode) -> None:
        host = self._tree_host
        if host is None:
            return
        key = f"{database}:{schema.name}"
        expanded = key in self._expanded_schemas
        total = self._schema_total(schema)
        tree_row(
            host,
            self.theme,
            label=schema.name,
            indent=1,
            count=total,
            expandable=True,
            expanded=expanded,
            selected=self._selected_node == ("schema", schema.name),
            icon_name="folder",
            on_click=lambda s=schema.name: self._select_node(database, ("schema", s)),
            on_toggle=lambda s=schema.name: self._toggle_schema(database, s),
        )
        if not expanded:
            return
        for label, kind, icon in _SCHEMA_OBJECT_TYPES:
            count = self._schema_kind_count(schema, kind)
            kind_key = f"{key}:{kind}"
            kind_expanded = kind_key in self._expanded_object_kinds
            tree_row(
                host,
                self.theme,
                label=f"{label} ({count})",
                indent=2,
                count=count,
                expandable=count > 0,
                expanded=kind_expanded,
                selected=self._selected_node == ("objects", schema.name, kind),
                icon_name=icon,
                on_click=lambda s=schema.name, k=kind: self._select_node(
                    database, ("objects", s, k)
                ),
                on_toggle=lambda s=schema.name, k=kind: self._toggle_object_kind(
                    database, s, k
                ),
            )
            if kind_expanded and count > 0:
                cached = self._object_cache.get(kind_key)
                if cached is None:
                    self._ensure_objects_loaded(database, schema.name, kind)
                    tree_row(
                        host,
                        self.theme,
                        label="Loading…",
                        indent=3,
                        icon_name=None,
                    )
                else:
                    for obj in cached:
                        tree_row(
                            host,
                            self.theme,
                            label=obj.name,
                            indent=3,
                            selected=self._selected_node
                            == ("table", schema.name, obj.name)
                            and kind == "tables",
                            icon_name="table" if kind == "tables" else "file",
                            on_click=(
                                (
                                    lambda s=schema.name, t=obj.name: self._select_node(
                                        database, ("table", s, t)
                                    )
                                )
                                if kind == "tables"
                                else (
                                    lambda s=schema.name, k=kind, n=obj.name: self._select_node(
                                        database, ("object", s, k, n)
                                    )
                                )
                            ),
                        )

    def _ensure_objects_loaded(self, database: str, schema: str, kind: str) -> None:
        kind_key = f"{database}:{schema}:{kind}"
        if kind_key in self._object_cache:
            return
        if not self._provider:
            return

        def task() -> None:
            try:
                items = self._provider.list_objects(database, schema, kind)
                self._object_cache[kind_key] = items
                self.after(0, lambda: self._render_tree(database))
            except Exception:
                self._object_cache[kind_key] = []
                self.after(0, lambda: self._render_tree(database))

        threading.Thread(target=task, daemon=True).start()

    def _toggle_schema(self, database: str, schema: str) -> None:
        key = f"{database}:{schema}"
        if key in self._expanded_schemas:
            self._expanded_schemas.discard(key)
        else:
            self._expanded_schemas.add(key)
            self._expanded_schemas.add(database)
        self._render_tree(database)

    def _toggle_object_kind(self, database: str, schema: str, kind: str) -> None:
        kind_key = f"{database}:{schema}:{kind}"
        if kind_key in self._expanded_object_kinds:
            self._expanded_object_kinds.discard(kind_key)
        else:
            self._expanded_object_kinds.add(kind_key)
            self._expanded_schemas.add(f"{database}:{schema}")
            self._expanded_schemas.add(database)
            self._ensure_objects_loaded(database, schema, kind)
        self._render_tree(database)

    def _select_node(self, database: str, node: tuple[str, ...]) -> None:
        self._selected_node = node
        self._render_tree(database)
        self._load_section(database, node)

    def _show_detail_error(self, message: str) -> None:
        host = self._detail_host
        if host is None:
            return
        for child in host.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            host,
            text=message,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("error"),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=12, pady=12)

    def _show_loading(self) -> None:
        host = self._detail_host
        if host is None:
            return
        for child in host.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            host,
            text="Loading…",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
        ).pack(anchor="w", padx=12, pady=12)

    def _load_section(self, database: str, node: tuple[str, ...]) -> None:
        if not self._provider:
            return
        self._show_loading()
        token = (database, node)

        def task() -> None:
            try:
                payload = self._fetch_payload(database, node)
                self.after(0, lambda: self._render_payload(token, payload))
            except Exception as exc:
                self.after(
                    0,
                    lambda: self._render_payload(token, {"error": str(exc)}),
                )

        threading.Thread(target=task, daemon=True).start()

    def _fetch_payload(self, database: str, node: tuple[str, ...]) -> dict[str, Any]:
        assert self._provider is not None
        kind = node[0]
        if kind == "general":
            return {"kind": "general", "data": self._provider.get_general_info(database)}
        if kind == "schemas":
            schemas = self._schema_cache.get(database) or self._provider.list_schemas(database)
            self._schema_cache[database] = schemas
            return {"kind": "schemas", "data": schemas}
        if kind == "schema":
            schemas = self._schema_cache.get(database) or self._provider.list_schemas(database)
            self._schema_cache[database] = schemas
            match = next((s for s in schemas if s.name == node[1]), None)
            return {"kind": "schema", "data": match}
        if kind == "objects":
            items = self._provider.list_objects(database, node[1], node[2])
            return {"kind": "objects", "schema": node[1], "object_type": node[2], "data": items}
        if kind == "table":
            details = self._provider.get_table_details(database, node[1], node[2])
            return {"kind": "table", "data": details}
        if kind == "object":
            return {
                "kind": "object",
                "schema": node[1],
                "object_type": node[2],
                "name": node[3],
            }
        if kind == "storage":
            return {"kind": "storage", "data": self._provider.get_storage(database)}
        if kind == "statistics":
            return {"kind": "statistics", "data": self._provider.get_statistics(database)}
        if kind == "configuration":
            return {"kind": "configuration", "data": self._provider.get_configuration(database)}
        if kind == "extensions":
            return {"kind": "extensions", "data": self._provider.list_extensions(database)}
        if kind == "maintenance":
            return {"kind": "maintenance", "data": self._provider.get_maintenance(database)}
        if kind == "backup":
            return {"kind": "backup", "data": self._provider.get_backup_info(database)}
        return {"kind": "unknown"}

    def _render_payload(self, token: tuple[str, tuple[str, ...]], payload: dict[str, Any]) -> None:
        database, node = token
        if self._expanded_db != database or self._selected_node != node:
            return
        host = self._detail_host
        if host is None:
            return
        for child in host.winfo_children():
            child.destroy()

        if payload.get("error"):
            self._show_detail_error(str(payload["error"]))
            return

        kind = payload.get("kind")
        if kind == "general":
            body = section_card(host, self.theme, "General Information")
            items = [(k, v) for k, v in payload["data"].items()]
            kv_grid(body, self.theme, items, columns=2)
            return

        if kind == "schemas":
            body = section_card(host, self.theme, "Schemas")
            schemas: list[SchemaNode] = payload["data"]
            rows = [
                [
                    s.name,
                    str(s.table_count),
                    str(s.view_count),
                    str(s.function_count),
                    str(self._schema_total(s)),
                ]
                for s in schemas
            ]
            simple_table(
                body,
                self.theme,
                ["Schema", "Tables", "Views", "Functions", "Objects"],
                rows or [["—", "0", "0", "0", "0"]],
            )
            return

        if kind == "schema" and payload.get("data"):
            s: SchemaNode = payload["data"]
            body = section_card(host, self.theme, f"Schema · {s.name}")
            kv_grid(
                body,
                self.theme,
                [
                    ("Tables", str(s.table_count)),
                    ("Views", str(s.view_count)),
                    ("Materialized Views", str(s.matview_count)),
                    ("Functions", str(s.function_count)),
                    ("Procedures", str(s.procedure_count)),
                    ("Sequences", str(s.sequence_count)),
                    ("Triggers", str(s.trigger_count)),
                    ("Indexes", str(s.index_count)),
                    ("Foreign Keys", str(s.foreign_key_count)),
                ],
            )
            return

        if kind == "objects":
            items: list[ObjectRef] = payload["data"]
            body = section_card(
                host,
                self.theme,
                f"{payload['object_type'].replace('_', ' ').title()} · {payload['schema']}",
            )
            rows = [
                [o.name, o.object_type, o.size, o.row_count, o.last_modified]
                for o in items
            ]
            simple_table(
                body,
                self.theme,
                ["Name", "Type", "Size", "Rows", "Last Modified"],
                rows or [["—", "—", "—", "—", "—"]],
            )
            return

        if kind == "table":
            self._render_table_details(host, payload["data"])
            return

        if kind == "object":
            body = section_card(host, self.theme, "Object")
            kv_grid(
                body,
                self.theme,
                [
                    ("Name", payload["name"]),
                    ("Schema", payload["schema"]),
                    ("Type", payload["object_type"].replace("_", " ").title()),
                ],
            )
            return

        if kind == "storage":
            self._render_storage(host, payload["data"])
            return

        if kind == "statistics":
            stats: DbStatistics = payload["data"]
            body = section_card(host, self.theme, "Statistics")
            kv_grid(
                body,
                self.theme,
                [
                    ("Schemas", str(stats.schemas)),
                    ("Tables", str(stats.tables)),
                    ("Views", str(stats.views)),
                    ("Functions", str(stats.functions)),
                    ("Procedures", str(stats.procedures)),
                    ("Indexes", str(stats.indexes)),
                    ("Sequences", str(stats.sequences)),
                    ("Triggers", str(stats.triggers)),
                    ("Materialized Views", str(stats.materialized_views)),
                ],
            )
            return

        if kind == "configuration":
            cfg: ConfigInfo = payload["data"]
            body = section_card(host, self.theme, "Configuration")
            kv_grid(body, self.theme, cfg.items or [("Note", "—")], columns=1)
            return

        if kind == "extensions":
            exts: list[ExtensionInfo] = payload["data"]
            body = section_card(host, self.theme, "Extensions")
            rows = [[e.name, e.version, e.description] for e in exts]
            simple_table(
                body,
                self.theme,
                ["Extension", "Version", "Description"],
                rows or [["—", "—", "No extensions installed"]],
            )
            return

        if kind == "maintenance":
            maint: MaintenanceInfo = payload["data"]
            body = section_card(host, self.theme, "Maintenance")
            kv_grid(
                body,
                self.theme,
                [
                    ("Last Vacuum", maint.last_vacuum),
                    ("Last Analyze", maint.last_analyze),
                    ("Last Autovacuum", maint.last_autovacuum),
                    ("Last Reindex", maint.last_reindex),
                    ("Autovacuum Status", maint.autovacuum_status),
                ],
            )
            if maint.details:
                detail_body = section_card(host, self.theme, "Recent Table Activity")
                rows = [
                    [
                        d.get("name", "—"),
                        d.get("last_vacuum", "—"),
                        d.get("last_autovacuum", "—"),
                        d.get("last_analyze", "—"),
                    ]
                    for d in maint.details[:15]
                ]
                simple_table(
                    detail_body,
                    self.theme,
                    ["Table", "Vacuum", "Autovacuum", "Analyze"],
                    rows,
                )
            return

        if kind == "backup":
            info: BackupInfo = payload["data"]
            body = section_card(host, self.theme, "Backup Information")
            kv_grid(
                body,
                self.theme,
                [
                    ("Last Backup", info.last_backup),
                    ("Backup Type", info.backup_type),
                    ("Logical / Physical", info.logical_physical),
                    ("Backup Method", info.backup_method),
                    ("Backup Scope", info.backup_scope),
                    ("Backup Format", info.backup_format),
                    ("Backup Size", info.backup_size),
                    ("Duration", info.duration),
                    ("Compression", info.compression),
                    ("Status", info.status),
                    ("Last Restore", info.last_restore),
                ],
            )
            return

        ctk.CTkLabel(
            host,
            text="Select an item in the explorer tree.",
            text_color=self.theme.c("text_muted"),
        ).pack(anchor="w", padx=12, pady=12)

    def _render_table_details(self, host: ctk.CTkFrame, details: TableDetails) -> None:
        body = section_card(host, self.theme, f"Table · {details.schema}.{details.name}")
        kv_grid(
            body,
            self.theme,
            [
                ("Table Name", details.name),
                ("Schema", details.schema),
                ("Estimated Rows", details.estimated_rows),
                ("Table Size", details.table_size),
                ("Primary Key", details.primary_key),
            ],
        )
        if details.foreign_keys:
            fk_body = section_card(host, self.theme, "Foreign Keys")
            simple_table(
                fk_body,
                self.theme,
                ["Definition"],
                [[fk] for fk in details.foreign_keys],
            )
        if details.indexes:
            idx_body = section_card(host, self.theme, "Indexes")
            simple_table(
                idx_body,
                self.theme,
                ["Index"],
                [[idx] for idx in details.indexes],
            )
        col_body = section_card(host, self.theme, "Columns")
        rows = [
            [
                c.name,
                c.data_type,
                c.nullable,
                c.default_value,
                c.identity,
                "PK" if c.is_primary_key else "",
            ]
            for c in details.columns
        ]
        simple_table(
            col_body,
            self.theme,
            ["Column", "Type", "Nullable", "Default", "Identity", "Key"],
            rows or [["—", "—", "—", "—", "—", ""]],
        )

    def _render_storage(self, host: ctk.CTkFrame, storage: StorageSummary) -> None:
        body = section_card(host, self.theme, "Storage")
        kv_grid(
            body,
            self.theme,
            [
                ("Used Space", storage.used_space),
                ("Free Space", storage.free_space),
                ("Index Size", storage.index_size),
                ("Toast Size", storage.toast_size),
                ("Database Growth", storage.growth_note),
            ],
        )
        tables_body = section_card(host, self.theme, "Largest Tables")
        simple_table(
            tables_body,
            self.theme,
            ["Table", "Size"],
            [[t["name"], t["size"]] for t in storage.largest_tables]
            or [["—", "—"]],
        )
        top_body = section_card(host, self.theme, "Top 10 Largest Objects")
        simple_table(
            top_body,
            self.theme,
            ["Object", "Size"],
            [[t["name"], t["size"]] for t in storage.top_objects] or [["—", "—"]],
        )
