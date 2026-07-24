"""Database browser view for the connected server."""

from __future__ import annotations

import threading

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.db_catalog import fetch_mongodb_collections, fetch_server_databases
from desktop.theme import ThemeManager
from desktop.ui_components import panel_card, section_header
from desktop.views.base_view import BaseView


class DatabasesView(BaseView):
    """List databases on the active server; expand a row to inspect collections/tables."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self._list_frame: ctk.CTkFrame | None = None
        self._expanded_db: str | None = None
        self._detail_hosts: dict[str, ctk.CTkFrame] = {}
        self._chevron_labels: dict[str, ctk.CTkLabel] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))
        section_header(
            header,
            self.theme,
            "Databases",
            "Click a database to view collections (MongoDB) or objects inside it",
        )

        card = panel_card(scroll, self.theme, "Database Inventory")
        card.pack(fill="x", pady=(0, 12))
        self._list_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._list_frame.pack(fill="x", padx=20, pady=(0, 20))

    def refresh(self) -> None:
        if not self._list_frame:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()
        self._detail_hosts.clear()
        self._chevron_labels.clear()
        previous = self._expanded_db
        self._expanded_db = None

        if not self.connection:
            ctk.CTkLabel(
                self._list_frame,
                text="Connect to a server first (Servers → Connect).",
                font=ctk.CTkFont(size=13),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", pady=8)
            return

        try:
            databases = fetch_server_databases(self.connection)
        except Exception as exc:
            ctk.CTkLabel(
                self._list_frame,
                text=f"Could not load databases: {exc}",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("error"),
                anchor="w",
                wraplength=640,
                justify="left",
            ).pack(anchor="w", pady=8)
            return

        if not databases:
            ctk.CTkLabel(
                self._list_frame,
                text="No databases found on this server.",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", pady=8)
            return

        hint = "Click a row to expand and see collections inside."
        if self.connection.db_type != DatabaseType.MONGODB:
            hint = "Click a row for database details."
        ctk.CTkLabel(
            self._list_frame,
            text=hint,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        for db in databases:
            name = str(db.get("name", "—"))
            size = db.get("size_mb") or db.get("size")
            if isinstance(size, (int, float)):
                size_text = f"{size:.1f} MB"
            elif size:
                size_text = str(size)
            else:
                size_text = "—"
            self._add_database_block(name, size_text)

        if previous and previous in self._detail_hosts:
            self._toggle_database(previous)

    def _add_database_block(self, name: str, size_text: str) -> None:
        assert self._list_frame is not None
        block = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        block.pack(fill="x", pady=4)

        row = ctk.CTkFrame(
            block,
            fg_color=self.theme.c("bg_elevated"),
            corner_radius=8,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
            cursor="hand2",
        )
        row.pack(fill="x")

        chevron = ctk.CTkLabel(
            row,
            text="▶",
            width=24,
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
        )
        chevron.pack(side="left", padx=(12, 0), pady=12)
        self._chevron_labels[name] = chevron

        title = ctk.CTkLabel(
            row,
            text=name,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        )
        title.pack(side="left", padx=(8, 14), pady=12, fill="x", expand=True)

        size_lbl = ctk.CTkLabel(
            row,
            text=size_text,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="e",
        )
        size_lbl.pack(side="right", padx=14, pady=12)

        detail = ctk.CTkFrame(
            block,
            fg_color=self.theme.c("bg_card"),
            corner_radius=8,
            border_width=1,
            border_color=self.theme.c("border_subtle"),
        )
        # Hidden until expanded
        self._detail_hosts[name] = detail

        def on_click(_event=None, db_name=name) -> None:
            self._toggle_database(db_name)

        for widget in (row, chevron, title, size_lbl):
            widget.bind("<Button-1>", on_click)

    def _toggle_database(self, name: str) -> None:
        detail = self._detail_hosts.get(name)
        if detail is None:
            return

        # Collapse currently expanded
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
        detail.pack(fill="x", padx=(28, 0), pady=(4, 2))
        self._load_database_details(name, detail)

    def _load_database_details(self, name: str, host: ctk.CTkFrame) -> None:
        for child in host.winfo_children():
            child.destroy()

        loading = ctk.CTkLabel(
            host,
            text="Loading…",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        )
        loading.pack(anchor="w", padx=14, pady=12)

        def task() -> None:
            try:
                items, kind = self._fetch_objects(name)
                self.after(0, lambda: self._render_objects(host, name, items, kind, error=None))
            except Exception as exc:
                self.after(0, lambda: self._render_objects(host, name, [], "objects", error=str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _fetch_objects(self, database_name: str) -> tuple[list[dict], str]:
        if not self.connection:
            return [], "objects"
        if self.connection.db_type == DatabaseType.MONGODB:
            collections = fetch_mongodb_collections(self.connection, database_name)
            return collections, "collections"
        return [], "objects"

    def _render_objects(
        self,
        host: ctk.CTkFrame,
        database_name: str,
        items: list[dict],
        kind: str,
        *,
        error: str | None,
    ) -> None:
        # Ignore stale responses if user collapsed / switched
        if self._expanded_db != database_name:
            return

        for child in host.winfo_children():
            child.destroy()

        header = ctk.CTkFrame(host, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            header,
            text=f"Collections in {database_name}" if kind == "collections" else f"Objects in {database_name}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text=f"{len(items)} found" if not error else "",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
        ).pack(side="right")

        if error:
            ctk.CTkLabel(
                host,
                text=error,
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("error"),
                anchor="w",
                wraplength=560,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 12))
            return

        if not items:
            msg = (
                "No collections in this database."
                if kind == "collections"
                else "Object listing is available for MongoDB collections. Connect to MongoDB to inspect collections."
            )
            if self.connection and self.connection.db_type != DatabaseType.MONGODB:
                msg = (
                    f"{self.connection.db_type.value.upper()} table browser is not available here yet. "
                    "Use Backup / SQL tools for object details."
                )
            ctk.CTkLabel(
                host,
                text=msg,
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                anchor="w",
                wraplength=560,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 12))
            return

        for item in items:
            name = str(item.get("name", "—"))
            count = item.get("count")
            count_text = f"{int(count):,} docs" if isinstance(count, (int, float)) else ""

            line = ctk.CTkFrame(host, fg_color="transparent")
            line.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(
                line,
                text="●",
                width=16,
                font=ctk.CTkFont(size=10),
                text_color=self.theme.c("primary"),
            ).pack(side="left")
            ctk.CTkLabel(
                line,
                text=name,
                font=ctk.CTkFont(size=13),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=(4, 8))
            if count_text:
                ctk.CTkLabel(
                    line,
                    text=count_text,
                    font=ctk.CTkFont(size=12),
                    text_color=self.theme.c("text_muted"),
                    anchor="e",
                ).pack(side="right")

        ctk.CTkFrame(host, height=8, fg_color="transparent").pack()
