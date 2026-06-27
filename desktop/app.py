"""Main desktop application with sidebar navigation."""

from __future__ import annotations

import sys
import threading

import customtkinter as ctk

from backend.core.config import get_settings
from desktop.connection import ConnectionResult, apply_connection, test_connection
from desktop.connection_store import SavedServer
from desktop.connection_dialog import ConnectionDialog
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
from desktop.views.monitoring_view import MonitoringView
from desktop.views.restore_view import RestoreView
from desktop.views.servers_view import ServersView
from desktop.views.settings_view import SettingsView

NAV_ITEMS = [
    ("dashboard", "Dashboard", DashboardView),
    ("servers", "Servers", ServersView),
    ("databases", "Databases", DatabasesView),
    ("backup", "Backup", BackupView),
    ("restore", "Restore", RestoreView),
    ("logs", "Logs", LogsView),
    ("reports", "Reports", MonitoringView),
    ("settings", "Settings", SettingsView),
]

SIDEBAR_WIDTH = 280

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

        # Row 0: brand card
        brand_outer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_outer.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 10))

        brand_card = ctk.CTkFrame(
            brand_outer,
            corner_radius=10,
            fg_color=self._t.sc("bg_card"),
            border_width=1,
            border_color=self._t.sc("border"),
        )
        brand_card.pack(fill="x", padx=2, pady=2)
        self._brand_card = brand_card

        brand_inner = ctk.CTkFrame(brand_card, fg_color="transparent")
        brand_inner.pack(fill="x", padx=14, pady=12)

        self._brand_title = ctk.CTkLabel(
            brand_inner,
            text="DB Automation Pro",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=self._t.sc("text_primary"),
            anchor="w",
        )
        self._brand_title.pack(anchor="w")

        self._brand_subtitle = ctk.CTkLabel(
            brand_inner,
            text="Enterprise DBA Platform",
            font=ctk.CTkFont(size=11),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._brand_subtitle.pack(anchor="w", pady=(2, 0))

        # Row 1: employee profile
        profile_outer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        profile_outer.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        profile_card = ctk.CTkFrame(
            profile_outer,
            corner_radius=10,
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
            width=40,
            height=40,
            corner_radius=20,
            fg_color=self._t.c("primary"),
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
            btn = ctk.CTkButton(
                nav_scroll,
                text=f"  {icon}   {label}",
                anchor="w",
                height=34,
                corner_radius=8,
                fg_color="transparent",
                text_color=self._t.sc("text_secondary") if self._t.is_light else self._t.c("nav_idle_text"),
                hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self.navigate(k),
            )
            btn.pack(fill="x", pady=2, padx=4)
            self.nav_buttons[key] = btn

        # Row 3: theme toggle
        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))

        self._theme_btn = ctk.CTkButton(
            footer,
            text=f"{self._theme_button_label()}  Theme",
            height=34,
            corner_radius=8,
            fg_color=self._t.sc("bg_card"),
            hover_color=self._t.sc("bg_hover"),
            border_width=1,
            border_color=self._t.sc("border"),
            text_color=self._t.sc("text_primary"),
            font=ctk.CTkFont(size=13),
            command=self._toggle_theme,
        )
        self._theme_btn.pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            footer,
            text="Sign Out",
            height=34,
            corner_radius=8,
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
            fg_color=self._t.c("bg_content"),
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
        dash = self.views.get("dashboard")
        if dash and hasattr(dash, "refresh"):
            dash.refresh()

    def connect_server(self, *, saved: SavedServer | None = None) -> None:
        if saved is not None:
            self._connect_saved_server(saved)
            return
        connection = ConnectionDialog.show(parent=self)
        if not connection:
            return
        self._establish_connection(connection)

    def _connect_saved_server(self, server: SavedServer) -> None:
        connection = server.to_connection_result()
        self.append_log(f"Connecting to {server.name} ({server.db_type.upper()})...", "INFO")

        def run() -> None:
            ok, message = test_connection(connection)
            self.after(0, lambda: self._on_saved_connect_complete(ok, message, connection))

        threading.Thread(target=run, daemon=True).start()

    def _on_saved_connect_complete(
        self,
        ok: bool,
        message: str,
        connection: ConnectionResult,
    ) -> None:
        if ok:
            self.append_log(message, "SUCCESS")
            self._establish_connection(connection)
        else:
            self.append_log(message, "ERROR")

    def _establish_connection(self, connection: ConnectionResult) -> None:
        close_ssh_tunnel()
        apply_connection(connection)
        effective = open_ssh_tunnel(connection)
        if connection.ssh_enabled and effective.host != connection.host:
            settings = get_settings()
            profile = "default"
            if profile in settings.connections:
                settings.connections[profile].host = effective.host
                settings.connections[profile].port = effective.port
        self.set_connection(connection, navigate_to="databases")

    def set_connection(
        self,
        connection: ConnectionResult,
        *,
        navigate_to: str | None = None,
    ) -> None:
        self.connection = connection
        self.append_log(f"Connected: {connection.name} ({connection.host})")
        self.update_status_footer(
            connection_status=f"● Connected · {connection.name}",
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

    def _style_navigation(self, key: str) -> None:
        t = self._t
        for k, btn in self.nav_buttons.items():
            if k == key:
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

    def navigate(self, key: str, *, log: bool = True, view_extra: dict | None = None) -> None:
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
                    if view_extra:
                        kwargs.update(view_extra)
                    if view_cls is DashboardView:
                        kwargs["navigate_callback"] = self.navigate
                        kwargs["employee"] = self.employee
                    if view_cls is ServersView:
                        kwargs["connect_callback"] = self.connect_server
                        kwargs["disconnect_callback"] = self.clear_connection
                    if view_cls is LogsView:
                        kwargs["log_store"] = self.log_store
                        kwargs["register_log_panel"] = self._register_log_panel
                    if view_cls is MonitoringView:
                        auto_actions = {
                            "reports": "report",
                        }
                        kwargs["auto_action"] = auto_actions.get(key)
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
            if hasattr(self.views[key], "refresh"):
                self.views[key].refresh()

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
        self.view_container.configure(fg_color=t.c("bg_content"))

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
