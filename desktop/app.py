"""Main desktop application with sidebar navigation."""

from __future__ import annotations

import sys
import tkinter as tk

import customtkinter as ctk

from backend.core.logging import setup_logging
from desktop.connection import ConnectionResult
from desktop.constants import APP_NAME
from desktop.db_icons import DB_ICON_META
from desktop.execution_log import ExecutionLogPanel
from desktop.nav_icons import NAV_ICONS
from desktop.sidebar_databases import SidebarDatabasePanel
from desktop.status_footer import StatusFooterBar
from desktop.theme import ThemeManager
from desktop.views.backup_view import BackupView
from desktop.views.dashboard import DashboardView
from desktop.views.fdw_view import FDWView
from desktop.views.migration_view import MigrationView
from desktop.views.patch_view import PatchView
from desktop.views.restore_view import RestoreView
from desktop.views.settings_view import SettingsView

NAV_ITEMS = [
    ("dashboard", "Dashboard", DashboardView),
    ("backup", "Backup", BackupView),
    ("restore", "Restore", RestoreView),
    ("patch", "Patches", PatchView),
    ("fdw", "FDW", FDWView),
    ("migration", "Migration", MigrationView),
    ("settings", "Settings", SettingsView),
]

SIDEBAR_WIDTH = 280

# Disconnect always uses strong red (same in light and dark mode).
DISCONNECT_BTN_FG = "#7f1d1d"
DISCONNECT_BTN_HOVER = "#ef4444"
DISCONNECT_BTN_TEXT = "#fecaca"


class DatabaseAutomationApp(ctk.CTk):
    """Database Automation Pro desktop application."""

    def __init__(self, connection: ConnectionResult | None = None) -> None:
        super().__init__()
        setup_logging()
        self.connection = connection
        self.disconnect_requested = False
        self.theme_manager = ThemeManager()
        self.views: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._t = self.theme_manager
        self._db_panel: SidebarDatabasePanel | None = None
        self._active_nav = "dashboard"
        self._brand_title: ctk.CTkLabel | None = None
        self._brand_subtitle: ctk.CTkLabel | None = None
        self._nav_header: ctk.CTkLabel | None = None
        self._theme_btn: ctk.CTkButton | None = None
        self._conn_card: ctk.CTkFrame | None = None
        self._conn_name_label: ctk.CTkLabel | None = None
        self._conn_host_label: ctk.CTkLabel | None = None
        self._conn_disconnect_btn: ctk.CTkButton | None = None
        self._sidebar_conn_slot: ctk.CTkFrame | None = None
        self._theme_switching = False

        self.title(APP_NAME)
        self.configure(fg_color=self._t.c("bg_app"))
        self.minsize(1024, 680)
        self._maximize_window()

        self._build_layout()
        self.navigate("dashboard")
        self.after(150, self.refresh_server_databases)
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
            fg_color=self._t.c("bg_sidebar"),
            border_width=1,
            border_color=self._t.sc("border") if self._t.is_light else self._t.c("border_subtle"),
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(2, weight=1)
        self.sidebar.grid_columnconfigure(0, weight=1)

        # Row 3: navigation (pinned to bottom)
        nav_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav_container.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 14))

        self._nav_header = ctk.CTkLabel(
            nav_container,
            text="NAVIGATION",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._nav_header.pack(anchor="w", padx=10, pady=(0, 6))

        for key, label, _ in NAV_ITEMS:
            icon = NAV_ICONS.get(key, "•")
            btn = ctk.CTkButton(
                nav_container,
                text=f"  {icon}   {label}",
                anchor="w",
                height=32,
                corner_radius=8,
                fg_color="transparent",
                text_color=self._t.sc("text_secondary") if self._t.is_light else self._t.c("nav_idle_text"),
                hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self.navigate(k),
            )
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        # Row 2: databases (scrollable, expands)
        db_wrap = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        db_wrap.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        db_wrap.grid_rowconfigure(0, weight=1)
        db_wrap.grid_columnconfigure(0, weight=1)

        self._db_panel = SidebarDatabasePanel(db_wrap, self._t)
        self._db_panel.grid(row=0, column=0, sticky="nsew")

        # Row 1: connection card slot (fixed height area)
        self._sidebar_conn_slot = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self._sidebar_conn_slot.grid(row=1, column=0, sticky="ew")

        # Row 0: brand header
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))

        title_row = ctk.CTkFrame(brand, fg_color="transparent")
        title_row.pack(fill="x")

        title_block = ctk.CTkFrame(title_row, fg_color="transparent")
        title_block.pack(side="left", fill="x", expand=True)

        self._brand_title = ctk.CTkLabel(
            title_block,
            text=APP_NAME,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=self._t.sc("text_primary"),
            anchor="w",
        )
        self._brand_title.pack(anchor="w")

        self._brand_subtitle = ctk.CTkLabel(
            title_block,
            text="Enterprise DB Operations",
            font=ctk.CTkFont(size=11),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._brand_subtitle.pack(anchor="w", pady=(2, 0))

        self._theme_btn = ctk.CTkButton(
            title_row,
            text=self._theme_button_label(),
            width=40,
            height=36,
            corner_radius=8,
            fg_color=self._t.sc("bg_card"),
            hover_color=self._t.sc("bg_hover"),
            border_width=1,
            border_color=self._t.sc("border"),
            text_color=self._t.sc("text_primary"),
            font=ctk.CTkFont(size=16),
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=(8, 0))

        if self.connection:
            self._build_connection_card()

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color=self._t.c("bg_content"))
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=0)

        self._vertical_split = tk.PanedWindow(
            self.content,
            orient=tk.VERTICAL,
            sashwidth=7,
            sashrelief=tk.FLAT,
            bg=self._t.c("border_subtle"),
            bd=0,
            opaqueresize=True,
            showhandle=False,
        )
        self._vertical_split.grid(row=0, column=0, sticky="nsew")

        self.view_container = ctk.CTkFrame(
            self._vertical_split,
            fg_color=self._t.c("bg_content"),
        )
        self.view_container.grid_columnconfigure(0, weight=1)
        self.view_container.grid_rowconfigure(0, weight=1)

        self.execution_log = ExecutionLogPanel(self._vertical_split, self._t)

        self._vertical_split.add(self.view_container, minsize=220)
        self._vertical_split.add(self.execution_log, minsize=90)
        self.after_idle(self._set_initial_log_split)

        self.status_footer = StatusFooterBar(self.content, self._t)
        self.status_footer.grid(row=1, column=0, sticky="ew")

    def _set_initial_log_split(self) -> None:
        """Place the sash so the execution log starts at a comfortable height."""
        try:
            self.update_idletasks()
            total = self._vertical_split.winfo_height()
            if total > 200:
                self._vertical_split.sash_place(0, 0, max(220, total - 130))
        except tk.TclError:
            pass

    def _build_connection_card(self) -> None:
        assert self.connection is not None
        assert self._sidebar_conn_slot is not None

        for child in self._sidebar_conn_slot.winfo_children():
            child.destroy()

        meta = DB_ICON_META.get(self.connection.db_type, {"abbrev": "DB", "color": "#3b82f6"})

        card = ctk.CTkFrame(
            self._sidebar_conn_slot,
            corner_radius=10,
            fg_color=self._t.sc("bg_card"),
            border_width=1,
            border_color=self._t.sc("border"),
        )
        card.pack(fill="x", padx=14, pady=(0, 8))
        self._conn_card = card

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(12, 6))

        badge = ctk.CTkFrame(row, width=34, height=34, corner_radius=8, fg_color=meta["color"])
        badge.pack(side="left")
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge,
            text=meta["abbrev"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="white",
        ).place(relx=0.5, rely=0.5, anchor="center")

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=(10, 0))

        self._conn_name_label = ctk.CTkLabel(
            info,
            text=self.connection.name,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self._t.sc("text_primary"),
            anchor="w",
        )
        self._conn_name_label.pack(anchor="w")

        self._conn_host_label = ctk.CTkLabel(
            info,
            text=f"{self.connection.host}:{self.connection.port}",
            font=ctk.CTkFont(size=11),
            text_color=self._t.sc("text_muted"),
            anchor="w",
        )
        self._conn_host_label.pack(anchor="w")

        self._conn_disconnect_btn = ctk.CTkButton(
            card,
            text="Disconnect",
            height=30,
            corner_radius=6,
            fg_color=DISCONNECT_BTN_FG,
            hover_color=DISCONNECT_BTN_HOVER,
            text_color=DISCONNECT_BTN_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._disconnect,
        )
        self._conn_disconnect_btn.pack(fill="x", padx=12, pady=(4, 12))

    def _style_connection_card(self) -> None:
        if not self._conn_card:
            return
        t = self._t
        self._conn_card.configure(fg_color=t.sc("bg_card"), border_color=t.sc("border"))
        if self._conn_name_label:
            self._conn_name_label.configure(text_color=t.sc("text_primary"))
        if self._conn_host_label:
            self._conn_host_label.configure(text_color=t.sc("text_muted"))
        if self._conn_disconnect_btn:
            self._conn_disconnect_btn.configure(
                fg_color=DISCONNECT_BTN_FG,
                hover_color=DISCONNECT_BTN_HOVER,
                text_color=DISCONNECT_BTN_TEXT,
            )

    def refresh_server_databases(self) -> None:
        if self._db_panel:
            self._db_panel.refresh(self.connection)

    def append_log(self, message: str, level: str = "INFO") -> None:
        self.execution_log.add_entry(message, level)

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
                        kwargs["status_callback"] = self.update_status_footer
                        kwargs["database_list_callback"] = self.refresh_server_databases
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

        if log:
            self.append_log(f"Navigated to {key}")

    def _disconnect(self) -> None:
        if self.connection:
            self.append_log(f"Disconnected from {self.connection.name}")
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
        new_theme = self.theme_manager.toggle()
        self._t = self.theme_manager
        self._apply_theme(new_theme)

    def _apply_theme(self, theme_name: str) -> None:
        t = self._t
        self.configure(fg_color=t.c("bg_app"))
        self.sidebar.configure(
            fg_color=t.c("bg_sidebar"),
            border_color=t.sc("border") if t.is_light else t.c("border_subtle"),
        )
        self.content.configure(fg_color=t.c("bg_content"))
        self.view_container.configure(fg_color=t.c("bg_content"))
        self._vertical_split.configure(bg=t.c("border_subtle"))

        if self._brand_title:
            self._brand_title.configure(text_color=t.sc("text_primary"))
        if self._brand_subtitle:
            self._brand_subtitle.configure(text_color=t.sc("text_muted"))
        if self._nav_header:
            self._nav_header.configure(text_color=t.sc("text_muted"))
        if self._theme_btn:
            self._theme_btn.configure(
                text=self._theme_button_label(),
                fg_color=t.sc("bg_card"),
                hover_color=t.sc("bg_hover"),
                border_color=t.sc("border"),
                text_color=t.sc("text_primary"),
            )
        if self._conn_card:
            self._style_connection_card()

        self._style_navigation(self._active_nav)
        for btn in self.nav_buttons.values():
            btn.configure(
                hover_color=t.sc("nav_hover") if t.is_light else t.c("bg_card_hover"),
            )

        self.execution_log.apply_theme(t)
        self.status_footer.apply_theme(t)
        if self._db_panel:
            self._db_panel.apply_theme(t)

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
            self.append_log(f"Theme switched to {theme_name}")

            if current == "dashboard":
                dash = self.views.get("dashboard")
                if dash:
                    self.after(300, dash.refresh)
            self.after(150, self.refresh_server_databases)
        finally:
            self._theme_switching = False
            if self._theme_btn:
                self._theme_btn.configure(state="normal")

    def _on_theme_change(self) -> None:
        self._toggle_theme()
