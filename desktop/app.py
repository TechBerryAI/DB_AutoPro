"""Main desktop application with sidebar navigation."""

from __future__ import annotations

import sys
import threading

import customtkinter as ctk

from backend.services.monitoring_service import MonitoringService
from backend.core.logging import setup_logging
from desktop.connection import ConnectionResult, apply_connection, test_connection
from desktop.connection_audit import log_connection_attempt
from desktop.connection_store import SavedServer
from desktop.connection_dialog import ConnectionDialog
from desktop.sidebar import EnterpriseSidebar
from desktop.ssh_tunnel import close_ssh_tunnel, open_ssh_tunnel
from desktop.employee_store import EmployeeSession
from desktop.constants import APP_NAME
from desktop.execution_log import ExecutionLogPanel, ExecutionLogStore
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

SIDEBAR_WIDTH = EnterpriseSidebar.WIDTH


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
        self._sidebar: EnterpriseSidebar | None = None
        self._monitoring = MonitoringService()
        self._t = self.theme_manager
        self._active_nav = "dashboard"
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

        self._sidebar = EnterpriseSidebar(
            self,
            self._t,
            employee_name=self.employee.full_name if self.employee else "User",
            employee_role=self._employee_role_text(),
            initials=self._employee_initials(),
            on_navigate=self.navigate,
            on_theme_toggle=self._toggle_theme,
            on_sign_out=self._sign_out,
            on_log=self.append_log,
        )
        self._sidebar.grid(row=0, column=0, sticky="nsew")

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
        self.after(500, self._refresh_sidebar)

    def _refresh_sidebar(self) -> None:
        if not self._sidebar:
            return
        self._sidebar.set_theme_button_label(self._theme_button_label())

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
        self._refresh_sidebar()

    def connect_server(self, *, saved: SavedServer | None = None) -> None:
        if saved is not None:
            self._connect_saved_server(saved)
            return
        connection = ConnectionDialog.show(
            parent=self,
            owner_username=self.employee.username if self.employee else None,
        )
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
        user = self.employee.username if self.employee else None
        if ok:
            self.append_log(message, "SUCCESS")
            self._establish_connection(connection)
        else:
            self.append_log(message, "ERROR")
            log_connection_attempt(user, connection, success=False, error=message)
            self._refresh_dashboard_on_login()
            servers = self.views.get("servers")
            if servers and hasattr(servers, "refresh"):
                servers.refresh()

    def _establish_connection(self, connection: ConnectionResult) -> None:
        close_ssh_tunnel()
        effective = open_ssh_tunnel(connection)
        apply_connection(effective)
        user = self.employee.username if self.employee else None
        log_connection_attempt(user, connection, success=True)
        self.set_connection(effective, navigate_to="databases")

    def set_connection(
        self,
        connection: ConnectionResult,
        *,
        navigate_to: str | None = None,
    ) -> None:
        self.connection = connection
        self.append_log(
            f"Connected: {connection.name} ({connection.endpoint_label})"
        )
        self.update_status_footer(
            connection_status=f"● Connected · {connection.name}",
        )
        self._reload_views()
        if navigate_to:
            self.navigate(navigate_to)
        self._refresh_sidebar()

    def clear_connection(self) -> None:
        if self.connection:
            self.append_log(f"Disconnected from {self.connection.name}")
        close_ssh_tunnel()
        self.connection = None
        self.update_status_footer(connection_status="● Disconnected")
        self._reload_views()
        self._refresh_sidebar()

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
        if self._sidebar:
            self._sidebar.style_navigation(key)

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
                        "employee": self.employee,
                    }
                    if view_extra:
                        kwargs.update(view_extra)
                    if view_cls is DashboardView:
                        kwargs["navigate_callback"] = self.navigate
                        kwargs["sidebar_refresh"] = self._refresh_sidebar
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
        try:
            new_theme = self.theme_manager.toggle()
            self._t = self.theme_manager
            self._apply_theme(new_theme)
        except Exception as exc:
            self._theme_switching = False
            self.append_log(f"Theme switch failed: {exc}", "ERROR")

    def _apply_theme(self, theme_name: str) -> None:
        t = self._t
        self.configure(fg_color=t.c("bg_app"))
        if self._sidebar:
            self._sidebar.apply_theme(t)
            self._sidebar.set_theme_button_label(self._theme_button_label())
        self.content.configure(fg_color=t.c("bg_content"))
        self.view_container.configure(fg_color=t.c("bg_content"))
        self._style_navigation(self._active_nav)
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
            self._refresh_sidebar()

    def _on_theme_change(self) -> None:
        self._toggle_theme()
