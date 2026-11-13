"""Right-hand traditional property grid for selected explorer objects."""

from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.classic_workspace.chrome import cx
from desktop.classic_workspace.tree_model import NodeKind, TreeNodeData
from desktop.connection import ConnectionResult
from desktop.connection_store import SavedServer, load_saved_servers
from desktop.db_catalog import fetch_mongodb_database_overview
from desktop.db_performance import fetch_database_performance
from desktop.explorer.factory import get_explorer_provider
from desktop.theme import ThemeManager

_TABS = ("General", "Statistics", "Configuration", "Dependencies", "Permissions")


class PropertiesPanel(ctk.CTkFrame):
    """Classic Property | Value inspector with engine-aware tabs."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        get_active_connection: Callable[[], ConnectionResult | None] | None = None,
        owner_username: str | None = None,
        on_action: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=cx(theme, "bg_card"), corner_radius=0, **kwargs)
        self.theme = theme
        self.get_active_connection = get_active_connection or (lambda: None)
        self.owner_username = owner_username
        self.on_action = on_action
        self._tab = "General"
        self._tab_btns: dict[str, ctk.CTkButton] = {}
        self._rows_host: ctk.CTkScrollableFrame | None = None
        self._title: ctk.CTkLabel | None = None
        self._current: TreeNodeData | None = None
        self._pending_pairs: list[tuple[str, str]] | None = None
        self._row_index = 0
        self._build()
        self.after(100, self._drain_pending)

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color=cx(self.theme, "header_bar"), corner_radius=0, height=28)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        self._title = ctk.CTkLabel(
            hdr,
            text="Properties",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=cx(self.theme, "header_text"),
            anchor="w",
        )
        self._title.pack(side="left", padx=8)

        strip = ctk.CTkFrame(self, fg_color=cx(self.theme, "bg_elevated"), corner_radius=0, height=28)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        for name in _TABS:
            btn = ctk.CTkButton(
                strip,
                text=name,
                width=100,
                height=26,
                corner_radius=0,
                fg_color=cx(self.theme, "bg_card") if name == self._tab else cx(self.theme, "bg_elevated"),
                hover_color=cx(self.theme, "bg_card"),
                text_color=cx(self.theme, "text_primary") if name == self._tab else cx(self.theme, "text_secondary"),
                font=ctk.CTkFont(family="Segoe UI", size=10),
                command=lambda n=name: self._show_tab(n),
            )
            btn.pack(side="left", padx=(0, 1))
            self._tab_btns[name] = btn

        cols = ctk.CTkFrame(self, fg_color=cx(self.theme, "bg_elevated"), corner_radius=0, height=24)
        cols.pack(fill="x")
        cols.pack_propagate(False)
        ctk.CTkLabel(
            cols, text="Property", width=180, anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=cx(self.theme, "text_secondary"),
        ).pack(side="left", padx=8)
        ctk.CTkLabel(
            cols, text="Value", anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=cx(self.theme, "text_secondary"),
        ).pack(side="left", fill="x", expand=True)

        self._rows_host = ctk.CTkScrollableFrame(self, fg_color=cx(self.theme, "bg_input"), corner_radius=0)
        self._rows_host.pack(fill="both", expand=True)
        self.clear(empty_actions=True)

    def _show_tab(self, name: str) -> None:
        self._tab = name
        for key, btn in self._tab_btns.items():
            active = key == name
            btn.configure(
                fg_color=cx(self.theme, "bg_card") if active else cx(self.theme, "bg_elevated"),
                text_color=cx(self.theme, "text_primary") if active else cx(self.theme, "text_secondary"),
            )
        if self._current is not None:
            self.show_node(self._current)

    def clear(self, message: str = "", *, empty_actions: bool = False) -> None:
        if self._rows_host is None:
            return
        for child in self._rows_host.winfo_children():
            child.destroy()
        self._row_index = 0
        if empty_actions:
            self._render_empty_state()
            return
        if message:
            ctk.CTkLabel(
                self._rows_host,
                text=message,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color=cx(self.theme, "text_muted"),
                anchor="w",
            ).pack(fill="x", padx=10, pady=12)

    def _render_empty_state(self) -> None:
        assert self._rows_host is not None
        host = self._rows_host
        ctk.CTkLabel(
            host,
            text="Database Automation Pro",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=cx(self.theme, "primary"),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(18, 4))
        ctk.CTkLabel(
            host,
            text="Select an object from Object Explorer\nor choose an action below.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=cx(self.theme, "text_muted"),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 12))
        actions = ctk.CTkFrame(host, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(0, 8))
        for label, key in (
            ("Connect Database", "servers"),
            ("Backup", "backup"),
            ("Restore", "restore"),
        ):
            ctk.CTkButton(
                actions,
                text=label,
                width=140,
                height=26,
                corner_radius=2,
                fg_color=cx(self.theme, "bg_card"),
                hover_color=cx(self.theme, "bg_card_hover"),
                border_width=1,
                border_color=cx(self.theme, "border"),
                text_color=cx(self.theme, "text_primary"),
                font=ctk.CTkFont(family="Segoe UI", size=10),
                command=lambda k=key: self.on_action(k) if self.on_action else None,
            ).pack(side="left", padx=(0, 8), pady=4)

    def _add_row(self, prop: str, value: str) -> None:
        assert self._rows_host is not None
        bg = cx(self.theme, "bg_zebra") if self._row_index % 2 else cx(self.theme, "bg_input")
        self._row_index += 1
        row = ctk.CTkFrame(self._rows_host, fg_color=bg, corner_radius=0, height=24)
        row.pack(fill="x")
        row.pack_propagate(False)
        row.grid_columnconfigure(0, minsize=168, weight=0)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            row,
            text=prop,
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "text_muted"),
        ).grid(row=0, column=0, sticky="w", padx=(10, 8), pady=2)
        ctk.CTkLabel(
            row,
            text=str(value if value not in (None, "") else "—"),
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=cx(self.theme, "text_primary"),
        ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=2)

    def show_empty(self) -> None:
        self._current = None
        if self._title is not None:
            self._title.configure(text="Properties")
        self.clear(empty_actions=True)

    def show_saved_server(self, server: SavedServer, *, live: ConnectionResult | None = None) -> None:
        """Show a registered connection profile (and live metrics when connected)."""
        node = TreeNodeData(
            kind=NodeKind.SERVER,
            label=server.name,
            server_name=server.name,
            db_type=server.db_type,
            expandable=True,
        )
        self._current = node
        if self._title is not None:
            self._title.configure(text=f"Properties — {server.name}")
        # Saved-profile fields first so the grid is never blank while metrics load.
        self._render(self._server_props(server, live, self._tab, metrics={}))
        if live is not None:
            threading.Thread(
                target=self._enrich_server_props,
                args=(server, live, self._tab),
                daemon=True,
            ).start()

    def show_node(self, node: TreeNodeData) -> None:
        self._current = node
        if self._title is not None:
            self._title.configure(text=f"Properties — {node.label}")
        # Instant saved-profile render for servers; live metrics fill in after.
        if node.kind == NodeKind.SERVER and node.server_name:
            server = self._server(node.server_name)
            if server is not None:
                self.show_saved_server(server, live=self._live_for(node.server_name))
                return
        self.clear("Loading…")
        threading.Thread(target=self._load_props, args=(node, self._tab), daemon=True).start()

    def _enrich_server_props(self, server: SavedServer, live: ConnectionResult, tab: str) -> None:
        metrics: dict = {}
        try:
            metrics = fetch_database_performance(live) or {}
        except Exception:
            metrics = {}
        if self._current is None or self._current.server_name != server.name:
            return
        self._pending_pairs = self._server_props(server, live, tab, metrics=metrics)

    def _load_props(self, node: TreeNodeData, tab: str) -> None:
        pairs: list[tuple[str, str]] = []
        try:
            pairs = self._collect(node, tab)
        except Exception as exc:
            pairs = [("Error", str(exc))]
        self._pending_pairs = pairs

    def _drain_pending(self) -> None:
        if not self.winfo_exists():
            return
        pending = self._pending_pairs
        if pending is not None:
            self._pending_pairs = None
            self._render(pending)
        self.after(100, self._drain_pending)

    def _render(self, pairs: list[tuple[str, str]]) -> None:
        if not self.winfo_exists() or self._rows_host is None:
            return
        self.clear()
        if not pairs:
            self.clear("No properties available for this object / tab.")
            return
        for prop, value in pairs:
            self._add_row(prop, value)

    def _server(self, name: str) -> SavedServer | None:
        servers = load_saved_servers(self.owner_username) or load_saved_servers()
        return next((s for s in servers if s.name == name), None)

    def _live_for(self, server_name: str) -> ConnectionResult | None:
        conn = self.get_active_connection()
        if conn is None:
            return None
        active = (conn.saved_name or conn.name or "").strip()
        return conn if active == server_name else None

    def _collect(self, node: TreeNodeData, tab: str) -> list[tuple[str, str]]:
        server = self._server(node.server_name)
        live = self._live_for(node.server_name) if node.server_name else None

        if tab == "Permissions":
            return [("Permissions", "Inspect via engine security tools / GRANT statements.")]
        if tab == "Dependencies":
            if node.kind in {NodeKind.TABLE, NodeKind.VIEW, NodeKind.COLLECTION}:
                return [("Dependencies", "Use catalog queries in the SQL / command editor.")]
            return [("Dependencies", "—")]

        if node.kind == NodeKind.SERVER and server is not None:
            return self._server_props(server, live, tab)
        if node.kind == NodeKind.DATABASE and server is not None:
            return self._database_props(server, node, live, tab)
        if node.kind == NodeKind.COLLECTION and server is not None:
            return self._collection_props(server, node, live, tab)
        if node.kind in {NodeKind.TABLE, NodeKind.VIEW} and server is not None:
            return self._table_props(server, node, live, tab)
        if node.kind == NodeKind.SCHEMA:
            return [
                ("Schema", node.schema or node.label),
                ("Database", node.database or "—"),
                ("Engine", (node.db_type or "").upper()),
            ]
        if node.kind == NodeKind.FOLDER:
            return [
                ("Folder", node.label),
                ("Server", node.server_name),
                ("Database", node.database or "—"),
            ]
        return [
            ("Name", node.label),
            ("Type", node.kind.value),
            ("Server", node.server_name or "—"),
            ("Database", node.database or "—"),
        ]

    def _server_props(
        self,
        server: SavedServer,
        live: ConnectionResult | None,
        tab: str,
        *,
        metrics: dict | None = None,
    ) -> list[tuple[str, str]]:
        if metrics is None:
            metrics = {}
            if live is not None:
                try:
                    metrics = fetch_database_performance(live) or {}
                except Exception:
                    metrics = {}
        if tab == "Statistics":
            return [
                ("Connected", "Yes" if live and metrics.get("connected") else "No"),
                ("Active connections", str(metrics.get("connections") or metrics.get("sessions") or "—")),
                ("Latency", str(metrics.get("latency") or "—")),
                ("Slow queries", str(metrics.get("slow_queries") or "—")),
            ]
        if tab == "Configuration":
            return [
                ("SSH tunnel", "Enabled" if server.ssh_enabled else "Disabled"),
                ("Auth database", server.auth_database or "—"),
                ("Default database", server.database or "—"),
                ("SSH host", server.ssh_host or server.host or "—"),
                ("SSH port", str(server.ssh_port or 22)),
            ]
        if live is None:
            status = "Registered (not connected)"
            health = "Offline"
        elif metrics.get("connected"):
            status = "Connected"
            health = "Healthy"
        else:
            status = "Connecting…" if not metrics else "Session degraded"
            health = "Unknown"
        return [
            ("Name", server.name or "—"),
            ("Database Engine", (server.db_type or "").upper() or "—"),
            ("Version", str(metrics.get("version") or "—")),
            ("Host", server.host or "—"),
            ("Port", str(server.port) if server.port else "—"),
            ("Default database", server.database or "—"),
            ("Connection Status", status),
            ("Health", health),
            ("Username", server.username or "(none)"),
            ("Uptime", str(metrics.get("uptime") or "—")),
        ]

    def _database_props(
        self, server: SavedServer, node: TreeNodeData, live: ConnectionResult | None, tab: str
    ) -> list[tuple[str, str]]:
        size = str((node.meta or {}).get("size") or "—")
        count = "—"
        version = "—"
        if live is not None and (server.db_type or "").lower() == DatabaseType.MONGODB.value:
            try:
                overview = fetch_mongodb_database_overview(live, node.database)
                version = str(overview.get("version") or "—")
                collections = overview.get("collections") or []
                count = str(len(collections))
                stats = overview.get("stats") or {}
                if stats.get("dataSize") is not None:
                    size = f"{int(stats.get('dataSize') or 0):,} bytes"
            except Exception as exc:
                count = f"(error: {exc})"
        elif live is not None:
            try:
                provider = get_explorer_provider(live)
                info = provider.get_general_info(node.database)
                version = info.get("Version") or info.get("Server Version") or version
                stats = provider.get_statistics(node.database)
                count = str(stats.tables)
                storage = provider.get_storage(node.database)
                size = storage.used_space or size
            except Exception:
                pass
        if tab == "Statistics":
            return [
                ("Object count", count),
                ("Size", size),
                ("Status", str((node.meta or {}).get("status") or "—")),
            ]
        if tab == "Configuration":
            return [("Database", node.database), ("Engine", (server.db_type or "").upper())]
        return [
            ("Name", node.database),
            ("Database Engine", (server.db_type or "").upper()),
            ("Version", version),
            ("Host", server.host),
            ("Port", str(server.port)),
            ("Connection Status", "Connected" if live else "Not connected"),
            ("Health", "Healthy" if live else "Offline"),
            ("Database Size", size),
            ("Number of Tables or Collections", count),
            ("Created Date", "—"),
        ]

    def _collection_props(
        self, server: SavedServer, node: TreeNodeData, live: ConnectionResult | None, tab: str
    ) -> list[tuple[str, str]]:
        meta = node.meta or {}
        if tab == "Statistics":
            return [
                ("Documents", str(meta.get("count", "—"))),
                ("Storage size", str(meta.get("size", "—"))),
                ("Indexes", str(meta.get("indexes", "—"))),
            ]
        return [
            ("Collection Name", node.object_name or node.label),
            ("Database", node.database),
            ("Engine", "MONGODB"),
            ("Document Count", str(meta.get("count", "—"))),
            ("Storage Size", str(meta.get("size", "—"))),
            ("Indexes", str(meta.get("indexes", "—"))),
            ("Connected", "Yes" if live else "No"),
        ]

    def _table_props(
        self, server: SavedServer, node: TreeNodeData, live: ConnectionResult | None, tab: str
    ) -> list[tuple[str, str]]:
        details = None
        if live is not None:
            try:
                provider = get_explorer_provider(live)
                details = provider.get_table_details(node.database, node.schema, node.object_name)
            except Exception:
                details = None
        if tab == "Statistics" and details is not None:
            return [
                ("Estimated rows", details.estimated_rows),
                ("Table size", details.table_size),
                ("Indexes", str(len(details.indexes))),
                ("Columns", str(len(details.columns))),
            ]
        if details is None:
            return [
                ("Table Name", node.object_name or node.label),
                ("Schema", node.schema or "—"),
                ("Database", node.database),
                ("Row Count", str((node.meta or {}).get("row_count") or "—")),
                ("Storage Size", str((node.meta or {}).get("size") or "—")),
            ]
        return [
            ("Table Name", details.name),
            ("Schema", details.schema),
            ("Row Count", details.estimated_rows),
            ("Columns", str(len(details.columns))),
            ("Indexes", ", ".join(details.indexes[:8]) or "—"),
            ("Storage Size", details.table_size),
            ("Primary Key", details.primary_key or "—"),
            ("Creation Information", "—"),
        ]
