"""Classic Object Explorer (ttk.Treeview) for registered servers."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

import customtkinter as ctk
from PIL import ImageTk

from desktop.classic_workspace.chrome import cx
from desktop.classic_workspace.tree_model import (
    NodeKind,
    TreeNodeData,
    database_children_skeleton,
    load_folder_children,
    schema_children_skeleton,
    server_children_skeleton,
)
from desktop.connection import ConnectionResult
from desktop.connection_store import SavedServer, load_saved_servers
from desktop.lucide_icons import lucide_pil_image
from desktop.theme import ThemeManager

# Text fallbacks when a Lucide asset is unavailable.
_KIND_PREFIX = {
    NodeKind.ROOT: "",
    NodeKind.SERVER: "",
    NodeKind.FOLDER: "",
    NodeKind.DATABASE: "",
    NodeKind.SCHEMA: "",
    NodeKind.TABLE: "▤ ",
    NodeKind.VIEW: "▥ ",
    NodeKind.COLLECTION: "▦ ",
    NodeKind.INDEX: "◉ ",
    NodeKind.FUNCTION: "ƒ ",
    NodeKind.PROCEDURE: "¶ ",
    NodeKind.SEQUENCE: "» ",
    NodeKind.TRIGGER: "⚡ ",
    NodeKind.USER: "",
    NodeKind.ROLE: "",
    NodeKind.PLACEHOLDER: "",
}

# Lucide stems for object rows. Saved servers stay text-only (no icon clutter).
_KIND_ICON = {
    NodeKind.DATABASE: "database",
    NodeKind.SCHEMA: "hard-drive",
    NodeKind.FOLDER: "hard-drive",
    NodeKind.TABLE: "database",
    NodeKind.VIEW: "file-chart-column",
    NodeKind.COLLECTION: "database",
    NodeKind.INDEX: "activity",
    NodeKind.FUNCTION: "cpu",
    NodeKind.PROCEDURE: "cpu",
    NodeKind.USER: "shield-check",
    NodeKind.ROLE: "shield-check",
}

_PAGE_ICON = {
    "dashboard": "layout-dashboard",
    "servers": "server",
    "databases": "database",
    "backup": "archive",
    "restore": "upload",
    "migration": "briefcase",
    "patching": "circle-check",
    "logs": "scroll-text",
    "audit": "shield-check",
    "reports": "file-chart-column",
    "settings": "settings",
}

_GROUP_ICON = {
    "Overview": "layout-dashboard",
    "Infrastructure": "server",
    "Operations": "briefcase",
    "Observability": "activity",
    "Settings": "settings",
    "Sign Out": "log-out",
    "Servers": "server",
    # Distinct from each other and from parent Servers.
    "Saved Connection": "hard-drive",
    "New Connection": "database-zap",
}


class ObjectExplorer(ctk.CTkFrame):
    """Left-hand hierarchical object browser."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        owner_username: str | None = None,
        get_active_connection: Callable[[], ConnectionResult | None] | None = None,
        on_select: Callable[[TreeNodeData], None] | None = None,
        on_connect: Callable[[SavedServer], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
        on_sign_out: Callable[[], None] | None = None,
        on_refresh_request: Callable[[], None] | None = None,
        on_new_query: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=cx(theme, "bg_card"), corner_radius=0, **kwargs)
        self.theme = theme
        self.owner_username = owner_username
        self.get_active_connection = get_active_connection or (lambda: None)
        self.on_select = on_select
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_navigate = on_navigate
        self.on_sign_out = on_sign_out
        self.on_refresh_request = on_refresh_request
        self.on_new_query = on_new_query
        self._nodes: dict[str, TreeNodeData] = {}
        self._servers: dict[str, SavedServer] = {}
        self._pending_children: list[tuple[str, list[TreeNodeData]]] = []
        self._filter_var = tk.StringVar(value="")
        self._icon_cache: dict[str, ImageTk.PhotoImage] = {}
        self._build()
        self.after(100, self._drain_pending)

    def _photo(self, name: str, *, size: int = 14) -> ImageTk.PhotoImage | None:
        color = cx(self.theme, "text_secondary")
        key = f"{name}:{color}:{size}"
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        try:
            pil = lucide_pil_image(name, color, size * 2, stroke_width=2.0)
            if pil.size != (size, size):
                pil = pil.resize((size, size))
            photo = ImageTk.PhotoImage(pil)
            self._icon_cache[key] = photo
            return photo
        except Exception:
            return None

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color=cx(self.theme, "header_bar"), corner_radius=0, height=28)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr,
            text="Object Explorer",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=cx(self.theme, "header_text"),
            anchor="w",
        ).pack(side="left", padx=10, pady=0)

        filter_row = ctk.CTkFrame(self, fg_color=cx(self.theme, "bg_card"), corner_radius=0, height=36)
        filter_row.pack(fill="x")
        filter_row.pack_propagate(False)

        search_wrap = ctk.CTkFrame(
            filter_row,
            fg_color=cx(self.theme, "bg_input"),
            corner_radius=3,
            border_width=1,
            border_color=cx(self.theme, "border"),
            height=26,
        )
        search_wrap.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=5)
        search_wrap.pack_propagate(False)

        ctk.CTkLabel(
            search_wrap,
            text="⌕",
            width=18,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=cx(self.theme, "text_muted"),
        ).pack(side="left", padx=(6, 0))

        self._filter = ctk.CTkEntry(
            search_wrap,
            height=22,
            corner_radius=0,
            border_width=0,
            fg_color=cx(self.theme, "bg_input"),
            text_color=cx(self.theme, "text_primary"),
            placeholder_text_color=cx(self.theme, "text_muted"),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            placeholder_text="Filter objects…",
            textvariable=self._filter_var,
        )
        self._filter.pack(side="left", fill="both", expand=True, padx=(2, 6), pady=2)
        self._filter.bind("<KeyRelease>", lambda _e: self._apply_filter())

        ctk.CTkButton(
            filter_row,
            text="↻",
            width=28,
            height=26,
            corner_radius=3,
            fg_color=cx(self.theme, "bg_elevated"),
            hover_color=cx(self.theme, "bg_card_hover"),
            border_width=1,
            border_color=cx(self.theme, "border"),
            text_color=cx(self.theme, "text_secondary"),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self.reload,
        ).pack(side="right", padx=(0, 8), pady=5)

        sel = cx(self.theme, "bg_selection")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Classic.Treeview",
            background=cx(self.theme, "bg_input"),
            foreground=cx(self.theme, "text_primary"),
            fieldbackground=cx(self.theme, "bg_input"),
            borderwidth=0,
            relief="flat",
            rowheight=22,
            font=("Segoe UI", 10),
            indent=16,
            focuscolor=sel,
        )
        style.configure(
            "Classic.Treeview.Heading",
            background=cx(self.theme, "bg_elevated"),
            foreground=cx(self.theme, "text_secondary"),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Classic.Treeview",
            background=[("selected", sel)],
            foreground=[("selected", cx(self.theme, "text_primary"))],
        )
        # Darker expand/collapse chevrons so hierarchy reads clearly.
        try:
            style.configure(
                "Classic.Treeview",
                indicatorforeground=cx(self.theme, "tree_guide"),
                indicatorbackground=cx(self.theme, "bg_input"),
            )
        except Exception:
            pass
        style.configure(
            "Classic.Vertical.TScrollbar",
            gripcount=0,
            background=cx(self.theme, "bg_elevated"),
            troughcolor=cx(self.theme, "bg_input"),
            bordercolor=cx(self.theme, "border_subtle"),
            arrowcolor=cx(self.theme, "text_muted"),
            arrowsize=12,
        )

        host = tk.Frame(
            self,
            bg=cx(self.theme, "bg_input"),
            highlightthickness=1,
            highlightbackground=cx(self.theme, "border_subtle"),
        )
        host.pack(fill="both", expand=True, padx=0, pady=(2, 0))
        self.tree = ttk.Treeview(
            host,
            show="tree",
            selectmode="browse",
            style="Classic.Treeview",
        )
        vsb = ttk.Scrollbar(host, orient="vertical", command=self.tree.yview, style="Classic.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(2, 0), pady=2)
        vsb.pack(side="right", fill="y", pady=2)

        self.tree.tag_configure(
            "section",
            font=("Segoe UI", 10, "bold"),
            foreground=cx(self.theme, "text_secondary"),
        )
        self.tree.tag_configure(
            "page",
            font=("Segoe UI", 10),
            foreground=cx(self.theme, "text_primary"),
        )
        self.tree.tag_configure(
            "muted",
            foreground=cx(self.theme, "text_muted"),
            font=("Segoe UI", 9),
        )

        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-3>", self._on_right_click)

        self._menu = tk.Menu(
            self,
            tearoff=0,
            bg=cx(self.theme, "bg_card"),
            fg=cx(self.theme, "text_primary"),
            activebackground=cx(self.theme, "bg_selection"),
            activeforeground=cx(self.theme, "text_primary"),
            relief="flat",
            borderwidth=1,
        )

    def _icon_for(self, node: TreeNodeData) -> ImageTk.PhotoImage | None:
        meta = node.meta or {}
        if meta.get("sign_out"):
            return self._photo("log-out")
        if meta.get("new_connection"):
            return self._photo("database-zap")
        if meta.get("saved_list"):
            return self._photo("hard-drive")
        if node.label in _GROUP_ICON:
            return self._photo(_GROUP_ICON[node.label])
        page = meta.get("page")
        if page:
            return self._photo(_PAGE_ICON.get(str(page), "hard-drive"))
        stem = _KIND_ICON.get(node.kind)
        if stem:
            return self._photo(stem)
        return None

    def _label(self, node: TreeNodeData, *, mark: str = "", has_icon: bool = False) -> str:
        """Build row text; leading spaces separate Lucide icons from the name."""
        prefix = _KIND_PREFIX.get(node.kind, "")
        # ttk.Treeview packs image flush against text — pad so labels don't touch icons.
        gap = "  " if has_icon else ""
        return f"{gap}{prefix}{node.label}{mark}"

    def _insert(
        self,
        parent: str,
        node: TreeNodeData,
        *,
        open_: bool = False,
        mark: str = "",
        tags: tuple[str, ...] = (),
    ) -> str:
        image = self._icon_for(node)
        item = self.tree.insert(
            parent,
            "end",
            text=self._label(node, mark=mark, has_icon=image is not None),
            open=open_,
            image=image or "",
            tags=tags,
        )
        self._nodes[item] = node
        return item

    def _insert_page(self, parent: str, label: str, page: str) -> None:
        self._insert(
            parent,
            TreeNodeData(kind=NodeKind.FOLDER, label=label, meta={"page": page}),
            tags=("page",),
        )

    def _insert_group(self, label: str, *, open_: bool = True) -> str:
        return self._insert(
            "",
            TreeNodeData(kind=NodeKind.FOLDER, label=label, expandable=True),
            open_=open_,
            tags=("section",),
        )

    def reload(self) -> None:
        """Sidebar groups with saved servers under Infrastructure → Servers."""
        self.tree.delete(*self.tree.get_children())
        self._nodes.clear()
        self._servers.clear()

        overview = self._insert_group("Overview")
        self._insert_page(overview, "Workspace", "dashboard")

        infrastructure = self._insert_group("Infrastructure")
        servers_node = self._insert(
            infrastructure,
            TreeNodeData(
                kind=NodeKind.FOLDER,
                label="Servers",
                expandable=True,
                meta={"page": "servers"},
            ),
            open_=True,
            tags=("section",),
        )
        self._insert(
            servers_node,
            TreeNodeData(
                kind=NodeKind.FOLDER,
                label="New Connection",
                meta={"page": "servers", "new_connection": True},
            ),
            tags=("page",),
        )
        saved_node = self._insert(
            servers_node,
            TreeNodeData(
                kind=NodeKind.FOLDER,
                label="Saved Connection",
                expandable=True,
                meta={"page": "servers", "saved_list": True},
            ),
            open_=True,
            tags=("section",),
        )
        self._insert_saved_servers(saved_node)
        self._insert_page(infrastructure, "Databases", "databases")

        operations = self._insert_group("Operations")
        for label, page in (
            ("Backup", "backup"),
            ("Restore", "restore"),
            ("Migration", "migration"),
            ("Patching", "patching"),
        ):
            self._insert_page(operations, label, page)

        observe = self._insert_group("Observability")
        for label, page in (
            ("Execution Log", "logs"),
            ("Audit Log", "audit"),
            ("Reports", "reports"),
        ):
            self._insert_page(observe, label, page)

        settings = self._insert_group("Settings")
        self._insert_page(settings, "Settings", "settings")

        self._insert(
            "",
            TreeNodeData(kind=NodeKind.FOLDER, label="Sign Out", meta={"sign_out": True}),
            tags=("page",),
        )
        if self.on_refresh_request:
            self.on_refresh_request()

    def _insert_saved_servers(self, parent: str) -> None:
        servers = load_saved_servers(self.owner_username) or load_saved_servers()
        active = self.get_active_connection()
        active_name = ""
        if active is not None:
            active_name = (active.saved_name or active.name or "").strip()
        if not servers:
            self._insert(
                parent,
                TreeNodeData(kind=NodeKind.PLACEHOLDER, label="(No saved servers)"),
                tags=("muted",),
            )
            return
        for server in sorted(servers, key=lambda s: s.name.lower()):
            self._servers[server.name] = server
            mark = " ●" if server.name == active_name else ""
            sid = self._insert(
                parent,
                TreeNodeData(
                    kind=NodeKind.SERVER,
                    label=server.name,
                    server_name=server.name,
                    db_type=server.db_type,
                    expandable=True,
                ),
                mark=mark,
                tags=("page",),
            )
            self._insert(
                sid,
                TreeNodeData(
                    kind=NodeKind.PLACEHOLDER,
                    label="…",
                    server_name=server.name,
                    db_type=server.db_type,
                ),
                tags=("muted",),
            )

    def _apply_filter(self) -> None:
        query = (self._filter_var.get() or "").strip().lower()
        saved = self._filter_var.get()
        self.reload()
        if saved:
            self._filter_var.set(saved)
        if not query:
            return
        matches = {
            item
            for item in self._all_items()
            if query in (self.tree.item(item, "text") or "").lower()
        }
        keep: set[str] = set(matches)
        for item in list(matches):
            parent = self.tree.parent(item)
            while parent:
                keep.add(parent)
                parent = self.tree.parent(parent)
        for item in self._all_items():
            if item in keep:
                try:
                    self.tree.item(item, open=True)
                except Exception:
                    pass
            else:
                try:
                    self.tree.detach(item)
                except Exception:
                    pass

    def _all_items(self, parent: str = "") -> list[str]:
        rows: list[str] = []
        for child in self.tree.get_children(parent):
            rows.append(child)
            rows.extend(self._all_items(child))
        return rows

    def selected_node(self) -> TreeNodeData | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._nodes.get(sel[0])

    def selected_server(self) -> SavedServer | None:
        node = self.selected_node()
        if node is None:
            return None
        return self._servers.get(node.server_name)

    def select_server(self, server_name: str) -> TreeNodeData | None:
        """Select a saved server row in the tree (after reload / connect)."""
        target = (server_name or "").strip()
        if not target:
            return None
        for item, node in self._nodes.items():
            if node.kind == NodeKind.SERVER and node.server_name == target:
                try:
                    self.tree.see(item)
                    self.tree.selection_set(item)
                    self.tree.focus(item)
                    parent = self.tree.parent(item)
                    while parent:
                        self.tree.item(parent, open=True)
                        parent = self.tree.parent(parent)
                except Exception:
                    pass
                return node
        return None

    def select_workspace(self) -> None:
        """Select Overview → Workspace and keep Infrastructure expanded (app landing)."""
        workspace_item = None
        for item, node in self._nodes.items():
            meta = node.meta or {}
            if meta.get("page") == "dashboard":
                workspace_item = item
                break
            if node.label == "Workspace" and meta.get("page") == "dashboard":
                workspace_item = item
                break
        # Ensure Infrastructure / Servers / Saved Connection stay open like the landing screenshot.
        for item, node in self._nodes.items():
            if node.label in {"Overview", "Infrastructure", "Servers", "Saved Connection", "Operations", "Observability", "Settings"}:
                try:
                    self.tree.item(item, open=True)
                except Exception:
                    pass
        if workspace_item is None:
            return
        try:
            self.tree.see(workspace_item)
            self.tree.selection_set(workspace_item)
            self.tree.focus(workspace_item)
        except Exception:
            pass

    def _open_servers_page(self, node: TreeNodeData) -> None:
        if not self.on_navigate:
            return
        meta = node.meta or {}
        if meta.get("new_connection"):
            self.on_navigate("servers", servers_filter="all", new_connection=True)
            return
        if node.kind == NodeKind.SERVER and node.server_name:
            self.on_navigate(
                "servers",
                servers_filter="all",
                saved_server=node.server_name,
                open_saved=True,
            )
            return
        self.on_navigate("servers", servers_filter="all")

    def _on_select(self, _event=None) -> None:
        node = self.selected_node()
        if node is None:
            return
        if (node.meta or {}).get("sign_out"):
            if self.on_sign_out:
                self.on_sign_out()
            return
        meta = node.meta or {}
        if meta.get("new_connection"):
            self._open_servers_page(node)
            return
        page = meta.get("page")
        if page and page != "servers" and node.kind != NodeKind.SERVER and self.on_navigate:
            self.on_navigate(page)
            return
        if node.kind == NodeKind.FOLDER and not node.server_name:
            return
        if self.on_select:
            self.on_select(node)

    def _on_double(self, _event=None) -> None:
        node = self.selected_node()
        if node is None:
            return
        if (node.meta or {}).get("sign_out") and self.on_sign_out:
            self.on_sign_out()
            return
        meta = node.meta or {}
        if meta.get("new_connection") or meta.get("saved_list") or meta.get("page") == "servers":
            if node.kind != NodeKind.SERVER:
                self._open_servers_page(node)
                return
        page = meta.get("page")
        if page and page != "servers" and node.kind != NodeKind.SERVER and self.on_navigate:
            self.on_navigate(page)
            return
        if node.kind == NodeKind.SERVER:
            self._open_servers_page(node)
            return
        if self.on_select:
            self.on_select(node)

    def _on_right_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        node = self.selected_node()
        if node is None:
            return
        self._menu.delete(0, "end")
        page = (node.meta or {}).get("page")

        if node.kind == NodeKind.SERVER:
            self._menu.add_command(label="Connect", command=self._connect_selected)
            self._menu.add_command(
                label="Disconnect",
                command=lambda: self.on_disconnect() if self.on_disconnect else None,
            )
            self._menu.add_separator()
            self._menu.add_command(
                label="Open connection…",
                command=lambda n=node.server_name: self.on_navigate(
                    "servers",
                    servers_filter="all",
                    saved_server=n,
                    open_saved=True,
                )
                if self.on_navigate
                else None,
            )
            self._menu.add_command(
                label="Backup",
                command=lambda: self.on_navigate("backup") if self.on_navigate else None,
            )
            self._menu.add_command(
                label="Restore",
                command=lambda: self.on_navigate("restore") if self.on_navigate else None,
            )
            self._menu.add_separator()
            self._menu.add_command(label="Refresh", command=self.reload)
            self._menu.add_command(label="Properties", command=lambda: self._fire_select(node))
        elif node.kind in {NodeKind.TABLE, NodeKind.VIEW, NodeKind.COLLECTION}:
            self._menu.add_command(label="Properties", command=lambda: self._fire_select(node))
            self._menu.add_separator()
            self._menu.add_command(label="Refresh", command=self.reload)
        elif page:
            self._menu.add_command(
                label="Open",
                command=lambda p=page: self.on_navigate(p) if self.on_navigate else None,
            )
            self._menu.add_separator()
            self._menu.add_command(label="Refresh", command=self.reload)
        else:
            self._menu.add_command(label="Refresh", command=self.reload)
            if node.kind != NodeKind.PLACEHOLDER:
                self._menu.add_command(label="Properties", command=lambda: self._fire_select(node))
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _fire_select(self, node: TreeNodeData) -> None:
        if self.on_select:
            self.on_select(node)

    def _connect_selected(self) -> None:
        server = self.selected_server()
        if server and self.on_connect:
            self.on_connect(server)

    def _on_open(self, _event=None) -> None:
        item = self.tree.focus()
        if not item:
            return
        node = self._nodes.get(item)
        if node is None or not node.expandable:
            return
        children = self.tree.get_children(item)
        if len(children) == 1 and self._nodes.get(children[0], TreeNodeData(NodeKind.PLACEHOLDER, "")).kind == NodeKind.PLACEHOLDER:
            self.tree.delete(children[0])
            self._nodes.pop(children[0], None)
            threading.Thread(target=self._load_children_async, args=(item, node), daemon=True).start()

    def _load_children_async(self, item: str, node: TreeNodeData) -> None:
        server = self._servers.get(node.server_name)
        if server is None:
            return
        connection = self.get_active_connection()
        active_name = ""
        if connection is not None:
            active_name = (connection.saved_name or connection.name or "").strip()
        live = connection if active_name == server.name else None

        children: list[TreeNodeData] = []
        try:
            if node.kind == NodeKind.SERVER:
                children = server_children_skeleton(server)
            elif node.kind == NodeKind.DATABASE:
                children = database_children_skeleton(server, node.database)
            elif node.kind == NodeKind.SCHEMA:
                children = schema_children_skeleton(server, node.database, node.schema)
            elif node.kind == NodeKind.FOLDER:
                children = load_folder_children(server, node, live)
            else:
                children = []
        except Exception as exc:
            children = [
                TreeNodeData(
                    kind=NodeKind.PLACEHOLDER,
                    label=f"(Error: {exc})",
                    server_name=server.name,
                    db_type=server.db_type,
                )
            ]
        self._pending_children.append((item, children))

    def _drain_pending(self) -> None:
        if not self.winfo_exists():
            return
        while self._pending_children:
            item, children = self._pending_children.pop(0)
            if not self.tree.exists(item):
                continue
            for child in children:
                cid = self._insert(item, child)
                if child.expandable:
                    self._insert(
                        cid,
                        TreeNodeData(
                            kind=NodeKind.PLACEHOLDER,
                            label="…",
                            server_name=child.server_name,
                            db_type=child.db_type,
                        ),
                        tags=("muted",),
                    )
        self.after(100, self._drain_pending)
