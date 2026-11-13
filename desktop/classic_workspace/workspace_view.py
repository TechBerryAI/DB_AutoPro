"""Classic database administration workspace (pgAdmin III / SSMS style)."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.classic_workspace.chrome import cx
from desktop.classic_workspace.object_explorer import ObjectExplorer
from desktop.classic_workspace.properties_panel import PropertiesPanel
from desktop.classic_workspace.tree_model import NodeKind, TreeNodeData
from desktop.connection import ConnectionResult
from desktop.connection_store import SavedServer, load_saved_servers
from desktop.constants import APP_NAME
from desktop.employee_store import EmployeeSession
from desktop.lucide_icons import lucide_ctk_image
from desktop.status_footer import format_footer_version
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView

# Pages that stay inside the traditional shell (Object Explorer + right pane).
_EMBEDDED_PAGES = frozenset(
    {
        "dashboard",
        "servers",
        "databases",
        "backup",
        "restore",
        "migration",
        "patching",
        "mysql_patch",
        "logs",
        "audit",
        "reports",
        "settings",
    }
)


class ClassicWorkspaceView(BaseView):
    """Three-pane classic DB admin workstation."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        employee: EmployeeSession | None = None,
        *,
        navigate_callback: Callable | None = None,
        establish_callback: Callable[[ConnectionResult], None] | None = None,
        disconnect_callback: Callable[[], None] | None = None,
        sign_out_callback: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, theme, log_callback, employee=employee, **kwargs)
        self.configure(fg_color=cx(theme, "bg_content"))
        self.navigate_callback = navigate_callback
        self.establish_callback = establish_callback
        self.disconnect_callback = disconnect_callback
        self.on_sign_out = sign_out_callback
        self._status: ctk.CTkLabel | None = None
        self.explorer: ObjectExplorer | None = None
        self.properties: PropertiesPanel | None = None
        self._menu: tk.Menu | None = None
        self._h_paned: tk.PanedWindow | None = None
        self._props_host: ctk.CTkFrame | None = None
        self._right: ctk.CTkFrame | None = None
        self._page_host: ctk.CTkFrame | None = None
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._active_content: str = "workspace"
        self._breadcrumb: ctk.CTkLabel | None = None
        self._crumb_bar: ctk.CTkFrame | None = None
        self._status_dot: ctk.CTkLabel | None = None
        self._status_detail: ctk.CTkLabel | None = None
        self._status_meta: ctk.CTkLabel | None = None
        self._title_icons: list = []
        self._toolbar_icons: list = []
        self._session_version = "—"
        self._session_uptime = "—"
        self._session_sessions = "—"
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_title_bar()
        self._build_menu_strip()
        self._build_toolbar()
        self.after(120, self._install_native_menu)

        self._h_paned = tk.PanedWindow(
            self,
            orient="horizontal",
            sashwidth=5,
            bg=cx(self.theme, "border"),
            bd=0,
            sashrelief="flat",
        )
        self._h_paned.grid(row=3, column=0, sticky="nsew")

        left = ctk.CTkFrame(self._h_paned, fg_color=cx(self.theme, "bg_card"), corner_radius=0)
        self._right = ctk.CTkFrame(self._h_paned, fg_color=cx(self.theme, "bg_content"), corner_radius=0)
        self._h_paned.add(left, minsize=200)
        self._h_paned.add(self._right, minsize=420)

        self.explorer = ObjectExplorer(
            left,
            self.theme,
            owner_username=self.owner_username,
            get_active_connection=lambda: self.connection,
            on_select=self._on_tree_select,
            on_connect=self._connect_server,
            on_disconnect=self._do_disconnect,
            on_navigate=self._nav,
            on_sign_out=self.on_sign_out,
        )
        self.explorer.pack(fill="both", expand=True)

        self._crumb_bar = ctk.CTkFrame(self._right, fg_color=cx(self.theme, "bg_elevated"), corner_radius=0, height=22)
        self._crumb_bar.pack(fill="x")
        self._crumb_bar.pack_propagate(False)
        self._breadcrumb = ctk.CTkLabel(
            self._crumb_bar,
            text="Workspace",
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "text_muted"),
        )
        self._breadcrumb.pack(side="left", padx=8)

        self._props_host = ctk.CTkFrame(self._right, fg_color=cx(self.theme, "bg_card"), corner_radius=0)
        self._props_host.pack(fill="both", expand=True)

        self.properties = PropertiesPanel(
            self._props_host,
            self.theme,
            get_active_connection=lambda: self.connection,
            owner_username=self.owner_username,
            on_action=self._property_action,
        )
        self.properties.pack(fill="both", expand=True)

        self._page_host = ctk.CTkFrame(
            self._right,
            fg_color=cx(self.theme, "bg_content"),
            corner_radius=0,
        )

        status = ctk.CTkFrame(self, fg_color=cx(self.theme, "status_bar"), corner_radius=0, height=26)
        status.grid(row=4, column=0, sticky="ew")
        status.grid_propagate(False)
        self._status_dot = ctk.CTkLabel(
            status,
            text="●",
            width=16,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "status_muted"),
        )
        self._status_dot.pack(side="left", padx=(10, 2))
        self._status = ctk.CTkLabel(
            status,
            text="Disconnected",
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=cx(self.theme, "status_text"),
        )
        self._status.pack(side="left", padx=(0, 12))
        self._status_detail = ctk.CTkLabel(
            status,
            text="Workspace",
            anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "status_muted"),
        )
        self._status_detail.pack(side="left", padx=(0, 8))
        self._status_meta = ctk.CTkLabel(
            status,
            text="Version: —   ·   Uptime: —   ·   Sessions: —",
            anchor="e",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "status_muted"),
        )
        self._status_meta.pack(side="right", padx=(8, 12))

        self.after(60, self._open_landing)
        self.after(250, self.restore_default_layout)

    def _build_title_bar(self) -> None:
        bar = ctk.CTkFrame(
            self,
            fg_color=cx(self.theme, "title_bar"),
            corner_radius=0,
            height=42,
        )
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", fill="y", padx=(12, 0))
        try:
            logo = lucide_ctk_image("database", "#5BA3F5", size=20)
            self._title_icons.append(logo)
            ctk.CTkLabel(left, text="", image=logo, width=24, height=24).pack(
                side="left", padx=(0, 8), pady=10
            )
        except Exception:
            ctk.CTkLabel(
                left,
                text="▣",
                font=ctk.CTkFont(family="Segoe UI", size=16),
                text_color="#5BA3F5",
            ).pack(side="left", padx=(0, 8), pady=8)

        titles = ctk.CTkFrame(left, fg_color="transparent")
        titles.pack(side="left", pady=6)
        ctk.CTkLabel(
            titles,
            text=APP_NAME,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=cx(self.theme, "title_text"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles,
            text="Enterprise Database Administration & Automation",
            font=ctk.CTkFont(family="Segoe UI", size=9),
            text_color=cx(self.theme, "title_muted"),
            anchor="w",
        ).pack(anchor="w")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=(0, 12))
        for icon_name, tip in (
            ("bell", "Notifications"),
            ("settings", "Settings"),
        ):
            try:
                img = lucide_ctk_image(icon_name, cx(self.theme, "title_muted"), size=16)
                self._title_icons.append(img)
                btn = ctk.CTkButton(
                    right,
                    text="",
                    image=img,
                    width=30,
                    height=28,
                    corner_radius=6,
                    fg_color="transparent",
                    hover_color="#243A66",
                    command=(lambda: self._nav("settings"))
                    if icon_name == "settings"
                    else (lambda: self._set_status("No new notifications")),
                )
                btn.pack(side="left", padx=2, pady=7)
                btn.bind("<Enter>", lambda _e, t=tip: self._set_status(t))
            except Exception:
                pass
        # User chip
        uname = "User"
        if self.employee:
            uname = (self.employee.full_name or self.employee.username or "User").split()[0]
        ctk.CTkLabel(
            right,
            text=f"  {uname}  ",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=cx(self.theme, "title_text"),
            fg_color="#243A66",
            corner_radius=10,
            height=24,
        ).pack(side="left", padx=(6, 0), pady=9)

    def _open_landing(self) -> None:
        """App start: Workspace dashboard + Object Explorer matching the launch screenshot."""
        if self.explorer:
            self.explorer.reload()
            self.explorer.apply_landing_tree()
        self.show_embedded_page("dashboard")
        self._set_status("Workspace")
        self._refresh_connection_status()
        # Re-apply after layout so a later refresh cannot leave Servers expanded.
        self.after(120, self._ensure_landing_tree)

    def _ensure_landing_tree(self) -> None:
        if self._active_content != "dashboard":
            return
        if self.explorer:
            self.explorer.apply_landing_tree()

    def restore_default_layout(self) -> None:
        """Object Explorer ~23%, Properties fills the right pane."""
        if self._h_paned is None:
            return
        try:
            self.update_idletasks()
            width = max(self._h_paned.winfo_width(), 900)
            self._h_paned.sash_place(0, int(width * 0.23), 1)
        except Exception:
            pass

    def show_admin_panes(self) -> None:
        """Show the Properties inspector (default object detail view)."""
        if self._page_host is not None:
            self._page_host.pack_forget()
        for page in self._pages.values():
            try:
                page.pack_forget()
            except Exception:
                pass
        if self._crumb_bar is not None and not self._crumb_bar.winfo_ismapped():
            self._crumb_bar.pack(fill="x")
        if self._props_host is not None and not self._props_host.winfo_ismapped():
            self._props_host.pack(fill="both", expand=True)
        self._active_content = "workspace"

    def show_embedded_page(self, key: str, **extra) -> bool:
        """Load a full page into the right pane without leaving classic chrome."""
        if key == "workspace":
            # "Workspace" in the product is the dashboard landing page.
            return self.show_embedded_page("dashboard", **extra)
        if key not in _EMBEDDED_PAGES:
            return False

        app = self.winfo_toplevel()
        required = getattr(app, "CONNECTION_REQUIRED_KEYS", None)
        if required is None:
            from desktop.app import CONNECTION_REQUIRED_KEYS as required
        if key in required and self.connection is None:
            if hasattr(app, "_prompt_connect_required"):
                app._prompt_connect_required(key)
            return True

        if self._crumb_bar is not None:
            self._crumb_bar.pack_forget()
        if self._props_host is not None:
            self._props_host.pack_forget()
        if self._page_host is None:
            return False
        self._page_host.pack(fill="both", expand=True)

        for other_key, page in self._pages.items():
            if other_key != key:
                try:
                    page.pack_forget()
                except Exception:
                    pass

        page = self._pages.get(key)
        if page is None:
            if not hasattr(app, "create_embedded_page"):
                return False
            page = app.create_embedded_page(
                self._page_host,
                key,
                navigate_callback=self._nav,
                on_object_properties=self._show_server_properties,
                view_extra=extra,
            )
            if page is None:
                return False
            self._pages[key] = page
        else:
            if hasattr(page, "set_connection"):
                page.set_connection(self.connection)
            self._apply_page_extra(page, key, extra)

        page.pack(fill="both", expand=True)
        self._active_content = key
        label = "Workspace" if key == "dashboard" else key.replace("_", " ").title()
        self._set_status(label)
        if getattr(self, "_breadcrumb", None) is not None and key == "dashboard":
            self._breadcrumb.configure(text="Workspace")
        if key == "dashboard" and hasattr(page, "resume"):
            page.resume()
        elif hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception:
                pass
        return True

    def _apply_page_extra(self, page, key: str, extra: dict) -> None:
        if key == "servers":
            if extra.get("new_connection") and hasattr(page, "start_new_connection"):
                page.start_new_connection()
            elif extra.get("saved_server") and hasattr(page, "load_saved_server"):
                page.load_saved_server(extra["saved_server"])
            elif hasattr(page, "set_servers_filter") and "servers_filter" in extra:
                page.set_servers_filter(extra["servers_filter"])
        elif key == "logs" and hasattr(page, "apply_log_filter"):
            page.apply_log_filter(extra.get("log_filter"))

    def _show_server_properties(self, name: str) -> None:
        """Show a registered database in the in-workspace Properties panel."""
        self.show_admin_panes()
        servers = load_saved_servers(self.owner_username) or load_saved_servers()
        server = next((s for s in servers if s.name == name), None)
        node = TreeNodeData(
            kind=NodeKind.SERVER,
            label=name,
            server_name=name,
            db_type=(server.db_type if server else ""),
            expandable=True,
        )
        if self.properties:
            self.properties.show_node(node)
        self._set_status(f"Properties · {name}")

    def _build_menu_strip(self) -> None:
        strip = ctk.CTkFrame(
            self,
            fg_color=cx(self.theme, "bg_elevated"),
            corner_radius=0,
            height=26,
        )
        strip.grid(row=1, column=0, sticky="ew")
        strip.grid_propagate(False)
        self._menu_strip = strip
        self._popup_menus: dict[str, tk.Menu] = {}

        def _popup(name: str, event) -> None:
            menu = self._popup_menus.get(name)
            if menu is None:
                return
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        for label in (
            "File",
            "View",
            "Database",
            "Operations",
            "Tools",
            "Reports",
            "Help",
        ):
            btn = ctk.CTkButton(
                strip,
                text=label,
                width=64,
                height=22,
                corner_radius=0,
                fg_color="transparent",
                hover_color=cx(self.theme, "bg_card_hover"),
                text_color=cx(self.theme, "text_primary"),
                font=ctk.CTkFont(family="Segoe UI", size=10),
                anchor="w",
            )
            btn.pack(side="left", padx=(6 if label == "File" else 0, 0), pady=2)
            btn.bind("<Button-1>", lambda e, n=label: _popup(n, e))

    def _install_native_menu(self) -> None:
        """Populate dropdown menus for the light menu strip (no OS menubar)."""
        root = self.winfo_toplevel()
        try:
            # Prefer in-app strip so title → menu → toolbar matches the mockup.
            try:
                root.configure(menu="")
            except Exception:
                pass

            def _menu() -> tk.Menu:
                return tk.Menu(
                    root,
                    tearoff=0,
                    bg=cx(self.theme, "bg_card"),
                    fg=cx(self.theme, "text_primary"),
                    activebackground=cx(self.theme, "bg_selection"),
                    activeforeground=cx(self.theme, "text_primary"),
                )

            file_m = _menu()
            file_m.add_command(label="Refresh Explorer", command=self.refresh)
            file_m.add_separator()
            file_m.add_command(
                label="Sign Out",
                command=lambda: self.on_sign_out() if self.on_sign_out else None,
            )

            view_m = _menu()
            view_m.add_command(label="Restore Default Layout", command=self.restore_default_layout)
            view_m.add_separator()
            view_m.add_command(label="Workspace", command=lambda: self._nav("dashboard"))
            view_m.add_command(label="Servers", command=lambda: self._nav("servers"))
            view_m.add_command(label="Databases", command=lambda: self._nav("databases"))

            db_m = _menu()
            db_m.add_command(label="Connect", command=self._connect_selected_or_servers)
            db_m.add_command(label="Disconnect", command=self._do_disconnect)
            db_m.add_separator()
            db_m.add_command(label="Backup", command=lambda: self._nav("backup"))
            db_m.add_command(label="Restore", command=lambda: self._nav("restore"))

            ops_m = _menu()
            ops_m.add_command(label="Backup", command=lambda: self._nav("backup"))
            ops_m.add_command(label="Restore", command=lambda: self._nav("restore"))
            ops_m.add_command(label="Migration", command=lambda: self._nav("migration"))
            ops_m.add_command(label="Patching", command=lambda: self._nav("patching"))

            tools_m = _menu()
            tools_m.add_command(label="SQL Console", command=lambda: self._nav("databases"))
            tools_m.add_command(label="Monitor", command=lambda: self._nav("audit"))
            tools_m.add_command(label="Execution Log", command=lambda: self._nav("logs"))
            tools_m.add_command(label="Audit Log", command=lambda: self._nav("audit"))
            tools_m.add_command(label="Settings", command=lambda: self._nav("settings"))

            reports_m = _menu()
            reports_m.add_command(label="Open Reports", command=lambda: self._nav("reports"))

            help_m = _menu()
            help_m.add_command(
                label="About Database Automation Pro",
                command=lambda: self._set_status(
                    "Database Automation Pro — classic administration workspace"
                ),
            )

            self._popup_menus = {
                "File": file_m,
                "View": view_m,
                "Database": db_m,
                "Operations": ops_m,
                "Tools": tools_m,
                "Reports": reports_m,
                "Help": help_m,
            }
            self._menu = file_m
        except Exception:
            self._menu = None

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=cx(self.theme, "toolbar"), corner_radius=0, height=36)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)

        def _sep() -> None:
            ctk.CTkFrame(bar, width=1, height=20, fg_color=cx(self.theme, "border"), corner_radius=0).pack(
                side="left", padx=6, pady=7
            )

        def _icon(name: str, color: str, size: int = 14):
            try:
                img = lucide_ctk_image(name, color, size=size)
                self._toolbar_icons.append(img)
                return img
            except Exception:
                return None

        def _btn(
            label: str,
            command,
            tip: str,
            *,
            width: int = 86,
            icon: str | None = None,
            success: bool = False,
            muted: bool = False,
        ) -> ctk.CTkButton:
            if success:
                fg, hover, tc, bw = (
                    cx(self.theme, "connect_ok"),
                    cx(self.theme, "connect_ok_hover"),
                    "#FFFFFF",
                    0,
                )
                icon_color = "#FFFFFF"
            else:
                fg, hover, tc, bw = (
                    cx(self.theme, "bg_card"),
                    cx(self.theme, "bg_card_hover"),
                    cx(self.theme, "text_muted") if muted else cx(self.theme, "text_primary"),
                    1,
                )
                icon_color = cx(self.theme, "primary") if not muted else cx(self.theme, "text_muted")
            img = _icon(icon, icon_color) if icon else None
            btn = ctk.CTkButton(
                bar,
                text=f"  {label}" if img is not None else label,
                image=img,
                compound="left",
                width=width,
                height=26,
                corner_radius=5,
                fg_color=fg,
                hover_color=hover,
                border_width=bw,
                border_color=cx(self.theme, "border"),
                text_color=tc,
                font=ctk.CTkFont(
                    family="Segoe UI",
                    size=10,
                    weight="bold" if success else "normal",
                ),
                command=command,
            )
            btn.pack(side="left", padx=2, pady=5)
            btn.bind("<Enter>", lambda _e, text=tip: self._set_status(text))
            return btn

        _btn(
            "Connect",
            self._connect_selected_or_servers,
            "Connect the selected saved server",
            width=92,
            icon="database-zap",
            success=True,
        )
        self._disconnect_btn = _btn(
            "Disconnect",
            self._do_disconnect,
            "Disconnect the active session",
            width=100,
            icon="log-out",
            muted=True,
        )
        self._disconnect_btn.configure(state="disabled")
        _btn("Refresh", self.refresh, "Reload Object Explorer", width=86, icon="refresh-cw")
        _sep()
        _btn("Backup", lambda: self._nav("backup"), "Open Backup", icon="archive")
        _btn("Restore", lambda: self._nav("restore"), "Open Restore", icon="upload")
        _btn("Migration", lambda: self._nav("migration"), "Open Migration", width=96, icon="briefcase")
        _btn("Patching", lambda: self._nav("patching"), "Open Patching", width=88, icon="circle-check")
        ctk.CTkFrame(self, height=1, fg_color=cx(self.theme, "border"), corner_radius=0).grid(
            row=2, column=0, sticky="sew"
        )

    def _nav(self, key: str, **extra) -> None:
        if key == "workspace" or key in _EMBEDDED_PAGES:
            self.show_embedded_page(key, **extra)
            return
        if not self.navigate_callback:
            return
        if extra:
            self.navigate_callback(key, view_extra=extra)
        else:
            self.navigate_callback(key)

    def _property_action(self, key: str) -> None:
        self._nav(key)

    def _set_status(self, text: str) -> None:
        if self._status_detail and self._status_detail.winfo_exists():
            self._status_detail.configure(text=text)
        self._refresh_connection_status()

    def _refresh_connection_status(self) -> None:
        if not self._status or not self._status.winfo_exists():
            return
        disc = getattr(self, "_disconnect_btn", None)
        if self.connection:
            name = self.connection.saved_name or self.connection.name
            engine = str(getattr(self.connection.db_type, "value", self.connection.db_type)).upper()
            self._status.configure(text=f"Connected  ·  {engine}  ·  {name}")
            if self._status_dot:
                self._status_dot.configure(text_color=cx(self.theme, "active_dot"))
            if disc is not None and disc.winfo_exists():
                disc.configure(
                    state="normal",
                    text_color=cx(self.theme, "text_primary"),
                )
        else:
            self._status.configure(text="Disconnected")
            if self._status_dot:
                self._status_dot.configure(text_color=cx(self.theme, "status_muted"))
            if disc is not None and disc.winfo_exists():
                disc.configure(
                    state="disabled",
                    text_color=cx(self.theme, "text_muted"),
                )
            self._session_version = "—"
            self._session_uptime = "—"
            self._session_sessions = "—"
        self._paint_status_meta()

    def _paint_status_meta(self) -> None:
        if self._status_meta is None or not self._status_meta.winfo_exists():
            return
        self._status_meta.configure(
            text=(
                f"Version: {self._session_version}   ·   "
                f"Uptime: {self._session_uptime}   ·   "
                f"Sessions: {self._session_sessions}"
            )
        )

    def update_session_status(
        self,
        *,
        version: str = "—",
        uptime: str = "—",
        sessions: str = "—",
        **_kwargs,
    ) -> None:
        """Keep the navy status strip in sync with live dashboard metrics."""
        self._session_version = format_footer_version(version) if version else "—"
        self._session_uptime = uptime or "—"
        self._session_sessions = sessions or "—"
        self._paint_status_meta()
        self._refresh_connection_status()

    def _set_breadcrumb(self, node: TreeNodeData) -> None:
        if not getattr(self, "_breadcrumb", None):
            return
        parts = ["Workspace"]
        if node.server_name:
            parts.append(node.server_name)
        if node.database:
            parts.append(node.database)
        if node.schema:
            parts.append(node.schema)
        if node.label and node.label not in parts:
            parts.append(node.label)
        self._breadcrumb.configure(text="  ›  ".join(parts))

    def _on_tree_select(self, node: TreeNodeData) -> None:
        # Selecting a registered server always opens the Properties inspector.
        if node.kind == NodeKind.SERVER or self._active_content != "workspace":
            self.show_admin_panes()
        if self.properties:
            if node.kind == NodeKind.SERVER:
                server = None
                if self.explorer:
                    server = self.explorer.selected_server()
                if server is None and node.server_name:
                    servers = load_saved_servers(self.owner_username) or load_saved_servers()
                    server = next((s for s in servers if s.name == node.server_name), None)
                if server is not None:
                    live = self.connection if self._connection_matches(server.name) else None
                    self.properties.show_saved_server(server, live=live)
                else:
                    self.properties.show_node(node)
            else:
                self.properties.show_node(node)
        self._set_breadcrumb(node)
        self._set_status(f"Selected: {node.label} ({node.kind.value})")

    def _connection_matches(self, server_name: str) -> bool:
        if self.connection is None:
            return False
        active = (self.connection.saved_name or self.connection.name or "").strip()
        return active == (server_name or "").strip()

    def _show_connected_properties(self, server_name: str | None = None) -> None:
        """Refresh Properties for the active (or named) saved connection."""
        name = (server_name or "").strip()
        if not name and self.connection is not None:
            name = (self.connection.saved_name or self.connection.name or "").strip()
        if not name or self.properties is None:
            return
        servers = load_saved_servers(self.owner_username) or load_saved_servers()
        server = next((s for s in servers if s.name == name), None)
        if server is None:
            return
        self.show_admin_panes()
        live = self.connection if self._connection_matches(server.name) else None
        self.properties.show_saved_server(server, live=live)
        if self.explorer:
            self.explorer.select_server(server.name)

    def _connect_selected_or_servers(self) -> None:
        """Connect the Object Explorer selection, or open Servers if none selected."""
        server = self.explorer.selected_server() if self.explorer else None
        if server is None:
            self._nav("servers")
            return
        self._connect_server(server)

    def _connect_server(self, server: SavedServer) -> None:
        if not self.establish_callback:
            self._nav("servers", saved_server=server.name, open_saved=True)
            return
        try:
            self.show_admin_panes()
            if self.properties:
                self.properties.show_saved_server(server, live=None)
            conn = server.to_connection_result()
            self.establish_callback(conn)
            self._set_status(f"Connecting {server.name}…")
            self.after(400, lambda: self.refresh(keep_server=server.name))
        except Exception as exc:
            self._set_status(f"Connect failed: {exc}")
            self.log(f"Connect failed: {exc}", "ERROR")

    def _do_disconnect(self) -> None:
        previous = ""
        if self.connection is not None:
            previous = (self.connection.saved_name or self.connection.name or "").strip()
        if self.disconnect_callback:
            self.disconnect_callback()
        self._set_status("Disconnected")
        self.after(200, lambda: self.refresh(keep_server=previous or None))

    def on_connection_changed(self) -> None:
        for page in self._pages.values():
            if hasattr(page, "set_connection"):
                try:
                    page.set_connection(self.connection)
                except Exception:
                    pass
        keep = None
        if self.connection is not None:
            keep = self.connection.saved_name or self.connection.name
        self.refresh(keep_server=keep)

    def refresh(self, *, force: bool = False, keep_server: str | None = None) -> None:
        del force
        was_dashboard = self._active_content == "dashboard"
        if self.explorer:
            self.explorer.reload()
            if was_dashboard and not keep_server:
                self.explorer.apply_landing_tree()
        active = self._pages.get(self._active_content)
        if active is not None and hasattr(active, "refresh"):
            try:
                active.refresh()
            except Exception:
                pass
        if keep_server:
            # Only leave the dashboard when a specific server was just connected/selected.
            self._show_connected_properties(keep_server)
        elif self.connection:
            name = self.connection.saved_name or self.connection.name
            self._set_status(f"{name} · {self.connection.endpoint_label}")
        else:
            self._set_status("No active session — select a server and Connect")
        if was_dashboard and not keep_server:
            self.show_embedded_page("dashboard")
            if self.explorer:
                self.explorer.apply_landing_tree()
        self._refresh_connection_status()
        self.log("Classic workspace refreshed", "INFO")
