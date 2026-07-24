"""Main desktop application with sidebar navigation."""

from __future__ import annotations

import sys

import customtkinter as ctk

from desktop.connection import ConnectionResult, apply_connection
from backend.core.logging import setup_logging
from desktop.ssh_tunnel import close_ssh_tunnel, open_ssh_tunnel
from desktop.employee_store import EmployeeSession
from desktop.constants import APP_NAME
from desktop.execution_log import ExecutionLogPanel, ExecutionLogStore
from desktop.nav_icons import NAV_ICONS
from desktop.status_footer import StatusFooterBar
from desktop.theme import ThemeManager
from desktop.views.backup_view import BackupView
from desktop.views.dashboard import DashboardView
from desktop.views.databases_view import DatabasesView
from desktop.views.logs_view import LogsView
from desktop.views.reports_view import ReportsView
from desktop.views.restore_view import RestoreView
from backend.models.schemas import DatabaseType
from desktop.connection_panel import SERVER_SUBCATEGORIES
from desktop.connection_store import load_saved_servers
from desktop.db_icons import DB_ICON_META
from desktop.views.servers_view import ServersView
from desktop.views.settings_view import SettingsView

NAV_ITEMS = [
    ("dashboard", "Dashboard", DashboardView),
    ("servers", "Servers", ServersView),
    ("databases", "Databases", DatabasesView),
    ("backup", "Backup", BackupView),
    ("restore", "Restore", RestoreView),
    ("logs", "Logs", LogsView),
    ("reports", "Reports", ReportsView),
    ("settings", "Settings", SettingsView),
]

SIDEBAR_WIDTH = 300

SERVERS_ENGINE_SUBNAV: list[tuple[str, str]] = [
    (db_type.value, label) for db_type, label in SERVER_SUBCATEGORIES
]

# Disconnect always uses strong red (same in light and dark mode).
DISCONNECT_BTN_FG = "#7f1d1d"
DISCONNECT_BTN_HOVER = "#ef4444"
DISCONNECT_BTN_TEXT = "#fecaca"


class DatabaseAutomationApp(ctk.CTk):
    """Database Automation Pro desktop application."""

    def __init__(self, connection: ConnectionResult | None = None, employee: EmployeeSession | None = None) -> None:
        super().__init__()
        setup_logging()
        self.connection = connection
        self.employee = employee
        self.disconnect_requested = False
        self.theme_manager = ThemeManager()
        self.views: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_indicators: dict[str, ctk.CTkFrame] = {}
        self._nav_rows: dict[str, ctk.CTkFrame] = {}
        self._servers_sub_buttons: dict[str, ctk.CTkButton] = {}
        self._servers_saved_buttons: dict[str, ctk.CTkButton] = {}
        self._servers_subnav_frame: ctk.CTkFrame | None = None
        self._servers_saved_frame: ctk.CTkFrame | None = None
        self._servers_nav_block: ctk.CTkFrame | None = None
        self._nav_scroll: ctk.CTkScrollableFrame | None = None
        self._cached_saved_server_names: tuple[str, ...] = ()
        self._servers_filter = "all"
        self._active_saved_connection: str | None = None
        self._t = self.theme_manager
        self._active_nav = "dashboard"
        self._brand_title: ctk.CTkLabel | None = None
        self._brand_subtitle: ctk.CTkLabel | None = None
        self._nav_header: ctk.CTkLabel | None = None
        self._theme_btn: ctk.CTkButton | None = None
        self._employee_name_label: ctk.CTkLabel | None = None
        self._employee_role_label: ctk.CTkLabel | None = None
        self._brand_card: ctk.CTkFrame | None = None
        self._profile_card: ctk.CTkFrame | None = None
        self._theme_switching = False
        self.log_store = ExecutionLogStore()
        self._active_log_panel: ExecutionLogPanel | None = None

        self.title(APP_NAME)
        self.configure(fg_color=self._t.c("bg_app"))
        self.minsize(1024, 680)
        self._maximize_window()

        self._build_layout()
        self.navigate("dashboard")
        self.after(400, self._refresh_dashboard_on_login)
        if self.employee:
            self.append_log(f"Signed in as {self.employee.full_name} ({self.employee.username})")
        if connection:
            self.append_log(f"Connected: {connection.name} ({connection.host})")

    def _maximize_window(self) -> None:
        self.update_idletasks()
        if sys.platform == "win32":
            self.state("zoomed")
        elif sys.platform == "darwin":
            self.attributes("-zoomed", True)
        else:
            try:
                self.attributes("-zoomed", True)
            except Exception:
                w, h = self.winfo_screenwidth(), self.winfo_screenheight()
                self.geometry(f"{w}x{h}+0+0")

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(
            self,
            width=SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=self._t.sc("bg_sidebar") if self._t.is_light else self._t.c("bg_sidebar"),
            border_width=1,
            border_color=self._t.sc("border") if self._t.is_light else self._t.c("border_subtle"),
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(2, weight=1)
        self.sidebar.grid_columnconfigure(0, weight=1)

        accent_bar = ctk.CTkFrame(self.sidebar, height=4, corner_radius=0, fg_color=self._t.c("gradient_start"))
        accent_bar.place(relx=0, rely=0, relwidth=1, anchor="nw")
        ctk.CTkFrame(self.sidebar, height=4, corner_radius=0, fg_color=self._t.c("gradient_end")).place(
            relx=0.55, rely=0, relwidth=0.45, anchor="nw"
        )

        # Brand card
        brand_outer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_outer.grid(row=0, column=0, sticky="ew", padx=16, pady=(20, 10))

        brand_card = ctk.CTkFrame(
            brand_outer,
            corner_radius=16,
            fg_color=self._t.sc("bg_card"),
            border_width=1,
            border_color=self._t.sc("border"),
        )
        brand_card.pack(fill="x", padx=1, pady=1)
        self._brand_card = brand_card

        brand_inner = ctk.CTkFrame(brand_card, fg_color="transparent")
        brand_inner.pack(fill="x", padx=14, pady=14)

        logo = ctk.CTkFrame(
            brand_inner,
            width=44,
            height=44,
            corner_radius=14,
            fg_color=self._t.c("primary"),
            border_width=2,
            border_color=self._t.c("gradient_end"),
        )
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo,
            text="DB",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=self._t.c("text_on_primary"),
        ).place(relx=0.5, rely=0.5, anchor="center")

        brand_text = ctk.CTkFrame(brand_inner, fg_color="transparent")
        brand_text.pack(side="left", fill="x", expand=True, padx=(12, 0))

        self._brand_title = ctk.CTkLabel(
            brand_text,
            text="DB Automation Pro",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=self._t.sc("text_primary"),
            anchor="w",
        )
        self._brand_title.pack(anchor="w")

        self._brand_subtitle = ctk.CTkLabel(
            brand_text,
            text="Enterprise DBA Platform",
            font=ctk.CTkFont(size=10),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._brand_subtitle.pack(anchor="w", pady=(3, 0))

        # Row 1: employee profile
        profile_outer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        profile_outer.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        profile_card = ctk.CTkFrame(
            profile_outer,
            corner_radius=16,
            fg_color=self._t.sc("bg_card"),
            border_width=1,
            border_color=self._t.sc("border"),
        )
        profile_card.pack(fill="x")
        self._profile_card = profile_card

        profile_row = ctk.CTkFrame(profile_card, fg_color="transparent")
        profile_row.pack(fill="x", padx=12, pady=12)

        initials = self._employee_initials()
        avatar = ctk.CTkFrame(
            profile_row,
            width=44,
            height=44,
            corner_radius=22,
            fg_color=self._t.c("primary"),
            border_width=2,
            border_color=self._t.c("accent"),
        )
        avatar.pack(side="left")
        avatar.pack_propagate(False)
        ctk.CTkLabel(
            avatar,
            text=initials,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self._t.c("text_on_primary"),
        ).place(relx=0.5, rely=0.5, anchor="center")

        info = ctk.CTkFrame(profile_row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=(10, 0))

        emp_name = self.employee.full_name if self.employee else "User"
        emp_role = self._employee_role_text()
        self._employee_name_label = ctk.CTkLabel(
            info,
            text=emp_name,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self._t.sc("text_primary"),
            anchor="w",
        )
        self._employee_name_label.pack(anchor="w")
        self._employee_role_label = ctk.CTkLabel(
            info,
            text=emp_role,
            font=ctk.CTkFont(size=11),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._employee_role_label.pack(anchor="w")

        # Row 2: navigation
        nav_scroll = ctk.CTkScrollableFrame(self.sidebar, fg_color="transparent")
        nav_scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self._nav_scroll = nav_scroll

        self._nav_header = ctk.CTkLabel(
            nav_scroll,
            text="NAVIGATION",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._nav_header.pack(anchor="w", padx=10, pady=(0, 6))

        for key, label, _ in NAV_ITEMS:
            icon = NAV_ICONS.get(key, "•")
            if key == "servers":
                row = ctk.CTkFrame(nav_scroll, fg_color="transparent")
                row.pack(fill="x", pady=3, padx=2)
                row.grid_columnconfigure(0, weight=1)
                self._nav_rows[key] = row
                self._servers_nav_block = row

                nav_line = ctk.CTkFrame(row, fg_color="transparent")
                nav_line.grid(row=0, column=0, sticky="ew")
                nav_line.grid_columnconfigure(1, weight=1)

                indicator = ctk.CTkFrame(nav_line, width=4, height=36, corner_radius=3, fg_color="transparent")
                indicator.grid(row=0, column=0, padx=(0, 8), sticky="ns")
                self._nav_indicators[key] = indicator

                btn = ctk.CTkButton(
                    nav_line,
                    text=f"  {icon}   {label}",
                    anchor="w",
                    height=42,
                    corner_radius=12,
                    fg_color="transparent",
                    text_color=self._t.sc("text_secondary") if self._t.is_light else self._t.c("nav_idle_text"),
                    hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                    font=ctk.CTkFont(size=13, weight="bold"),
                    command=lambda: self._navigate_servers("all"),
                )
                btn.grid(row=0, column=1, sticky="ew")
                self.nav_buttons[key] = btn

                self._servers_subnav_frame = ctk.CTkFrame(row, fg_color="transparent")
                self._servers_subnav_frame.grid(row=1, column=0, sticky="ew", pady=(2, 4))
                self._servers_subnav_frame.grid_remove()

                all_btn = ctk.CTkButton(
                    self._servers_subnav_frame,
                    text="All Saved",
                    anchor="w",
                    height=32,
                    corner_radius=8,
                    fg_color="transparent",
                    text_color=self._t.sc("text_muted") if self._t.is_light else self._t.c("text_muted"),
                    hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                    font=ctk.CTkFont(size=12),
                    command=lambda: self._navigate_servers("all"),
                )
                all_btn.pack(fill="x", pady=(2, 2), padx=(22, 8))
                self._servers_sub_buttons["all"] = all_btn

                self._servers_saved_frame = ctk.CTkFrame(self._servers_subnav_frame, fg_color="transparent")
                self._servers_saved_frame.pack(fill="x", padx=(18, 8), pady=(0, 4))

                ctk.CTkLabel(
                    self._servers_subnav_frame,
                    text="FILTER BY TYPE",
                    font=ctk.CTkFont(size=9, weight="bold"),
                    text_color=self._t.sc("text_muted") if self._t.is_light else self._t.c("text_muted"),
                    anchor="w",
                ).pack(anchor="w", padx=(22, 8), pady=(6, 2))

                for sub_key, sub_label in SERVERS_ENGINE_SUBNAV:
                    sub_btn = ctk.CTkButton(
                        self._servers_subnav_frame,
                        text=sub_label,
                        anchor="w",
                        height=32,
                        corner_radius=8,
                        fg_color="transparent",
                        text_color=self._t.sc("text_muted") if self._t.is_light else self._t.c("text_muted"),
                        hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                        font=ctk.CTkFont(size=12),
                        command=lambda sk=sub_key: self._navigate_servers(sk),
                    )
                    sub_btn.pack(fill="x", pady=1, padx=(22, 8))
                    self._servers_sub_buttons[sub_key] = sub_btn

                self._refresh_servers_saved_nav()
                continue

            row = ctk.CTkFrame(nav_scroll, fg_color="transparent")
            row.pack(fill="x", pady=3, padx=2)
            self._nav_rows[key] = row

            indicator = ctk.CTkFrame(row, width=4, height=36, corner_radius=3, fg_color="transparent")
            indicator.pack(side="left", padx=(0, 8))
            self._nav_indicators[key] = indicator

            btn = ctk.CTkButton(
                row,
                text=f"  {icon}   {label}",
                anchor="w",
                height=42,
                corner_radius=12,
                fg_color="transparent",
                text_color=self._t.sc("text_secondary") if self._t.is_light else self._t.c("nav_idle_text"),
                hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                font=ctk.CTkFont(size=13, weight="bold"),
                command=(lambda k=key: self.navigate(k)),
            )
            btn.pack(side="left", fill="x", expand=True)
            self.nav_buttons[key] = btn

        # Row 3: theme toggle
        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))

        self._theme_btn = ctk.CTkButton(
            footer,
            text=f"{self._theme_button_label()}  Theme",
            height=40,
            corner_radius=12,
            fg_color=self._t.sc("bg_card"),
            hover_color=self._t.sc("bg_hover"),
            border_width=1,
            border_color=self._t.sc("border"),
            text_color=self._t.sc("text_primary"),
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._toggle_theme,
        )
        self._theme_btn.pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            footer,
            text="Sign Out",
            height=40,
            corner_radius=12,
            fg_color=DISCONNECT_BTN_FG,
            hover_color=DISCONNECT_BTN_HOVER,
            text_color=DISCONNECT_BTN_TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._sign_out,
        ).pack(fill="x")

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color=self._t.c("bg_content"))
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=0)

        self.view_container = ctk.CTkFrame(
            self.content,
            fg_color=self._t.c("bg_mesh") if self._t.is_light else self._t.c("bg_content"),
            corner_radius=0,
        )
        self.view_container.grid(row=0, column=0, sticky="nsew")
        self.view_container.grid_columnconfigure(0, weight=1)
        self.view_container.grid_rowconfigure(0, weight=1)

        self.status_footer = StatusFooterBar(self.content, self._t)
        self.status_footer.grid(row=1, column=0, sticky="ew")

    def _employee_initials(self) -> str:
        if not self.employee:
            return "U"
        parts = [p for p in self.employee.full_name.split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return (self.employee.full_name[:2] or "U").upper()

    def _employee_role_text(self) -> str:
        if not self.employee:
            return "Operator"
        roles = {"admin": "Administrator", "operator": "Operator", "viewer": "Viewer"}
        return roles.get(self.employee.role.value, self.employee.role.value.title())

    def _register_log_panel(self, panel: ExecutionLogPanel | None) -> None:
        self._active_log_panel = panel
        if panel is not None:
            panel._render()

    def refresh_server_databases(self) -> None:
        db_view = self.views.get("databases")
        if db_view and hasattr(db_view, "refresh"):
            db_view.refresh()
        self._refresh_dashboard_on_login()

    def _refresh_dashboard_on_login(self) -> None:
        if self._active_nav != "dashboard":
            return
        dash = self.views.get("dashboard")
        if dash and hasattr(dash, "refresh"):
            dash.refresh()

    def manage_connections(self) -> None:
        """Open the inline server connection workspace."""
        self.navigate("servers", log=False)

    def _establish_connection(
        self,
        connection: ConnectionResult,
        *,
        navigate_to: str | None = "databases",
    ) -> None:
        close_ssh_tunnel()
        effective = open_ssh_tunnel(connection)
        apply_connection(effective)
        self.set_connection(effective, navigate_to=navigate_to)

    def set_connection(
        self,
        connection: ConnectionResult,
        *,
        navigate_to: str | None = None,
    ) -> None:
        from desktop.ssh_tunnel import resolve_active_connection

        self.connection = resolve_active_connection(connection)
        endpoint = self.connection.endpoint_label
        self.append_log(f"Connected: {self.connection.name} ({endpoint})")
        self.update_status_footer(
            connection_status=f"● Connected · {self.connection.name}",
        )
        self._reload_views()
        if navigate_to:
            self.navigate(navigate_to)

    def clear_connection(self) -> None:
        if self.connection:
            self.append_log(f"Disconnected from {self.connection.name}")
        close_ssh_tunnel()
        self.connection = None
        self.update_status_footer(connection_status="● Disconnected")
        self._reload_views()

    def _reload_views(self) -> None:
        current = self._active_nav
        for view in list(self.views.values()):
            view.destroy()
        self.views.clear()
        self.navigate(current, log=False)

    def append_log(self, message: str, level: str = "INFO") -> None:
        self.log_store.add(message, level)
        panel = self._active_log_panel
        if panel is not None and panel.winfo_exists():
            panel._render()

    def update_status_footer(self, **kwargs) -> None:
        self.status_footer.update(**kwargs)

    def _on_saved_connection_changed(self, name: str | None = None) -> None:
        if name:
            self._active_saved_connection = name
            self._servers_filter = "all"
        self._cached_saved_server_names = ()
        self._refresh_servers_saved_nav()

    def _navigate_servers(self, servers_filter: str) -> None:
        self._active_saved_connection = None
        self.navigate("servers", view_extra={"servers_filter": servers_filter})

    def _navigate_saved_server(self, server_name: str) -> None:
        self._active_saved_connection = server_name
        self.navigate(
            "servers",
            view_extra={"servers_filter": "all", "saved_server": server_name},
        )

    def _refresh_nav_scroll(self) -> None:
        nav_scroll = self._nav_scroll
        if nav_scroll is None or not nav_scroll.winfo_exists():
            return
        nav_scroll.update_idletasks()
        try:
            canvas = nav_scroll._parent_canvas
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass

    def _refresh_servers_saved_nav(self) -> None:
        if self._servers_saved_frame is None:
            return

        servers = sorted(load_saved_servers(), key=lambda s: s.name.lower())
        names = tuple(server.name for server in servers)
        if names == self._cached_saved_server_names and self._servers_saved_buttons:
            self._style_servers_subnav()
            return

        self._cached_saved_server_names = names
        for widget in self._servers_saved_frame.winfo_children():
            widget.destroy()
        self._servers_saved_buttons.clear()

        t = self._t

        if not servers:
            ctk.CTkLabel(
                self._servers_saved_frame,
                text="No saved connections",
                font=ctk.CTkFont(size=11),
                text_color=t.sc("text_muted") if t.is_light else t.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", padx=18, pady=(2, 4))
            self._style_servers_subnav()
            return

        for server in servers:
            db_type = DatabaseType(server.db_type)
            meta = DB_ICON_META.get(db_type, {"abbrev": "DB"})
            btn = ctk.CTkButton(
                self._servers_saved_frame,
                text=f"{meta['abbrev']}  {server.name}",
                anchor="w",
                height=32,
                corner_radius=8,
                fg_color="transparent",
                text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                hover_color=t.sc("nav_hover") if t.is_light else t.c("bg_card_hover"),
                font=ctk.CTkFont(size=11),
                command=lambda name=server.name: self._navigate_saved_server(name),
            )
            btn.pack(fill="x", pady=1, padx=(4, 0))
            self._servers_saved_buttons[server.name] = btn

        self._style_servers_subnav()

    def _style_servers_subnav(self) -> None:
        t = self._t
        on_servers = self._active_nav == "servers"
        subnav_changed = False
        if self._servers_subnav_frame is not None:
            if on_servers:
                if not self._servers_subnav_frame.winfo_ismapped():
                    self._servers_subnav_frame.grid(row=1, column=0, sticky="ew", pady=(2, 4))
                    subnav_changed = True
            elif self._servers_subnav_frame.winfo_ismapped():
                self._servers_subnav_frame.grid_remove()
                subnav_changed = True

        for sub_key, btn in self._servers_sub_buttons.items():
            if sub_key == "all":
                active = on_servers and self._servers_filter == "all" and not self._active_saved_connection
            else:
                active = (
                    on_servers
                    and self._servers_filter == sub_key
                    and not self._active_saved_connection
                )
            if active:
                btn.configure(
                    fg_color=t.sc("nav_active") if t.is_light else t.c("primary_muted"),
                    text_color=t.c("nav_active_text"),
                    border_width=1,
                    border_color=t.c("primary"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=t.sc("text_muted") if t.is_light else t.c("text_muted"),
                    border_width=0,
                )

        for name, btn in self._servers_saved_buttons.items():
            active = on_servers and self._active_saved_connection == name
            if active:
                btn.configure(
                    fg_color=t.sc("nav_active") if t.is_light else t.c("primary_muted"),
                    text_color=t.c("nav_active_text"),
                    border_width=1,
                    border_color=t.c("primary"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                    border_width=0,
                )

        if subnav_changed:
            self._refresh_nav_scroll()

    def _style_navigation(self, key: str) -> None:
        t = self._t
        for k, btn in self.nav_buttons.items():
            indicator = self._nav_indicators.get(k)
            active = k == key
            if active:
                btn.configure(
                    fg_color=t.sc("nav_active") if t.is_light else t.c("primary"),
                    text_color=t.c("nav_active_text"),
                    border_width=0,
                )
                if indicator and indicator.winfo_exists():
                    indicator.configure(fg_color=t.c("nav_indicator"))
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                    border_width=0,
                )
                if indicator and indicator.winfo_exists():
                    indicator.configure(fg_color="transparent")
        self._style_servers_subnav()

    def navigate(self, key: str, *, log: bool = True, view_extra: dict | None = None) -> None:
        extra = view_extra or {}
        if key == "servers":
            self._servers_filter = extra.get("servers_filter", self._servers_filter or "all")
            if "saved_server" in extra:
                self._active_saved_connection = extra.get("saved_server")
            elif self._servers_filter != "all":
                self._active_saved_connection = None

        self._active_nav = key
        self._style_navigation(key)

        for view in self.views.values():
            view.grid_forget()

        if key not in self.views:
            for nav_key, _, view_cls in NAV_ITEMS:
                if nav_key == key:
                    kwargs = {
                        "log_callback": self.append_log,
                        "connection": self.connection,
                    }
                    if extra:
                        kwargs.update(extra)
                    if view_cls is DashboardView:
                        kwargs["navigate_callback"] = self.navigate
                        kwargs["employee"] = self.employee
                    if view_cls is ServersView:
                        kwargs["establish_callback"] = (
                            lambda c: self._establish_connection(c, navigate_to=None)
                        )
                        kwargs["disconnect_callback"] = self.clear_connection
                        kwargs["saved_servers_changed_callback"] = self._on_saved_connection_changed
                        kwargs.setdefault("servers_filter", self._servers_filter)
                    if view_cls is LogsView:
                        kwargs["log_store"] = self.log_store
                        kwargs["register_log_panel"] = self._register_log_panel
                    if view_cls is SettingsView:
                        kwargs["theme_toggle_callback"] = self._on_theme_change
                    self.views[key] = view_cls(
                        self.view_container,
                        self.theme_manager,
                        **kwargs,
                    )
                    self.views[key].grid(row=0, column=0, sticky="nsew")
                    break
        else:
            self.views[key].grid(row=0, column=0, sticky="nsew")
            if key == "servers":
                servers_view = self.views[key]
                if hasattr(servers_view, "set_servers_filter"):
                    servers_view.set_servers_filter(self._servers_filter)
                saved = extra.get("saved_server")
                if saved and hasattr(servers_view, "load_saved_server"):
                    servers_view.load_saved_server(saved)
            elif key != "dashboard" and hasattr(self.views[key], "refresh"):
                self.views[key].refresh()

        for nav_key, view in self.views.items():
            if hasattr(view, "set_active"):
                view.set_active(nav_key == key)

        if log:
            self.append_log(f"Navigated to {key}")

    def _sign_out(self) -> None:
        close_ssh_tunnel()
        self.disconnect_requested = True
        self.destroy()

    def _theme_button_label(self) -> str:
        return "☀" if self._t.current == "dark" else "🌙"

    def _toggle_theme(self) -> None:
        if getattr(self, "_theme_switching", False):
            return
        self._theme_switching = True
        if self._theme_btn:
            self._theme_btn.configure(state="disabled")
        try:
            new_theme = self.theme_manager.toggle()
            self._t = self.theme_manager
            self._apply_theme(new_theme)
        except Exception as exc:
            self._theme_switching = False
            if self._theme_btn:
                self._theme_btn.configure(state="normal")
            self.append_log(f"Theme switch failed: {exc}", "ERROR")

    def _apply_theme(self, theme_name: str) -> None:
        t = self._t
        self.configure(fg_color=t.c("bg_app"))
        self.sidebar.configure(
            fg_color=t.sc("bg_sidebar") if t.is_light else t.c("bg_sidebar"),
            border_color=t.sc("border") if t.is_light else t.c("border_subtle"),
        )
        if self._brand_card:
            self._brand_card.configure(
                fg_color=t.sc("bg_card"),
                border_color=t.sc("border"),
            )
        if self._profile_card:
            self._profile_card.configure(
                fg_color=t.sc("bg_card"),
                border_color=t.sc("border"),
            )
        self.content.configure(fg_color=t.c("bg_content"))
        self.view_container.configure(
            fg_color=t.c("bg_mesh") if t.is_light else t.c("bg_content"),
        )

        if self._brand_title:
            self._brand_title.configure(text_color=t.sc("text_primary"))
        if self._brand_subtitle:
            self._brand_subtitle.configure(text_color=t.sc("text_muted"))
        if self._nav_header:
            self._nav_header.configure(text_color=t.sc("text_muted"))
        if self._theme_btn:
            self._theme_btn.configure(
                text=f"{self._theme_button_label()}  Theme",
                fg_color=t.sc("bg_card"),
                hover_color=t.sc("bg_hover"),
                border_color=t.sc("border"),
                text_color=t.sc("text_primary"),
            )
        if self._employee_name_label:
            self._employee_name_label.configure(text_color=t.sc("text_primary"))
        if self._employee_role_label:
            self._employee_role_label.configure(text_color=t.sc("text_muted"))

        self._style_navigation(self._active_nav)
        for key, btn in self.nav_buttons.items():
            btn.configure(
                text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                hover_color=t.sc("nav_hover") if t.is_light else t.c("bg_card_hover"),
            )
        for sub_key, btn in self._servers_sub_buttons.items():
            btn.configure(
                hover_color=t.sc("nav_hover") if t.is_light else t.c("bg_card_hover"),
            )
        for btn in self._servers_saved_buttons.values():
            btn.configure(
                hover_color=t.sc("nav_hover") if t.is_light else t.c("bg_card_hover"),
            )
        self._style_servers_subnav()

        self.status_footer.apply_theme(t)

        current = self._active_nav
        for view in list(self.views.values()):
            view.destroy()
        self.views.clear()

        self.update_idletasks()
        self.after(1, lambda: self._finish_theme_switch(theme_name, current))

    def _finish_theme_switch(self, theme_name: str, current: str) -> None:
        try:
            view_extra: dict = {}
            if current == "dashboard":
                view_extra["initial_refresh"] = False
            self.navigate(current, log=False, view_extra=view_extra)
            self.append_log(f"Theme switched to {theme_name}", "SUCCESS")

            if current == "dashboard":
                dash = self.views.get("dashboard")
                if dash:
                    self.after(300, dash.refresh)
            self.after(150, self.refresh_server_databases)
        except Exception as exc:
            self.append_log(f"Theme switch failed: {exc}", "ERROR")
        finally:
            self._theme_switching = False
            if self._theme_btn:
                self._theme_btn.configure(state="normal")

    def _on_theme_change(self) -> None:
        self._toggle_theme()
