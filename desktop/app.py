"""Main desktop application with sidebar navigation."""

from __future__ import annotations

import sys

import customtkinter as ctk

from backend.core.config import get_settings
from desktop.connection import ConnectionResult, apply_connection
from backend.core.logging import setup_logging
from desktop.ssh_tunnel import close_ssh_tunnel, open_ssh_tunnel, resolve_active_connection
from desktop.employee_store import EmployeeSession
from desktop.constants import APP_NAME
from desktop.execution_log import ExecutionLogPanel, ExecutionLogStore
from desktop.lucide_icons import lucide_ctk_image
from desktop.shell_header import ShellHeader, display_name, initials, role_label
from desktop.status_footer import StatusFooterBar
from desktop.window_icon import schedule_window_icon
from desktop.theme import (
    CONTENT_BOTTOM_INSET,
    ThemeManager,
    load_sidebar_collapsed,
    load_sidebar_width,
    save_sidebar_collapsed,
    save_sidebar_width,
)
from desktop.design_system import confirm
from desktop.views.backup_view import BackupView
from desktop.views.dashboard import DashboardView
from desktop.views.databases_view import DatabasesView
from desktop.views.logs_view import LogsView
from desktop.views.migration_view import MigrationView
from desktop.views.monitoring_view import MonitoringView
from desktop.views.patch_view import PatchView
from desktop.views.reports_view import ReportsView
from desktop.views.restore_view import RestoreView
from backend.models.schemas import DatabaseType
from desktop.connection_store import load_saved_servers
from desktop.db_icons import DB_ICON_META
from desktop.views.servers_view import ServersView
from desktop.views.settings_view import SettingsView

# Grouped shell navigation. Flat NAV_ITEMS is derived for lookups.
NAV_GROUPS: list[tuple[str, list[tuple[str, str, type]]]] = [
    (
        "Overview",
        [
            ("dashboard", "Dashboard", DashboardView),
        ],
    ),
    (
        "Infrastructure",
        [
            ("servers", "Servers", ServersView),
            ("databases", "Databases", DatabasesView),
        ],
    ),
    (
        "Operations",
        [
            ("backup", "Backup", BackupView),
            ("restore", "Restore", RestoreView),
            ("migration", "Migration", MigrationView),
            ("patching", "Patching", PatchView),
        ],
    ),
    (
        "Observability",
        [
            ("logs", "Execution Log", LogsView),
            ("audit", "Audit Log", MonitoringView),
            ("reports", "Reports", ReportsView),
        ],
    ),
    (
        "Settings",
        [
            ("settings", "Settings", SettingsView),
        ],
    ),
]

NAV_ITEMS: list[tuple[str, str, type]] = [
    (key, label, cls) for _, items in NAV_GROUPS for key, label, cls in items
]

SIDEBAR_WIDTH = 232
SIDEBAR_MIN_WIDTH = 180
SIDEBAR_MAX_WIDTH = 420
SIDEBAR_SASH_WIDTH = 4
NAV_ROW_HEIGHT = 30
NAV_GROUP_PADY = (4, 1)

# Pages that need an active DB session. Observability stays offline-ready.
# Backup (sidebar + Dashboard "Open backup") prompts to connect first.
CONNECTION_REQUIRED_KEYS = {
    "databases",
    "backup",
    "restore",
    "migration",
    "patching",
}


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
        self._servers_sub_buttons: dict[str, ctk.CTkButton] = {}
        self._servers_saved_buttons: dict[str, ctk.CTkButton] = {}
        self._servers_subnav_frame: ctk.CTkFrame | None = None
        self._servers_saved_frame: ctk.CTkFrame | None = None
        self._servers_nav_expanded = False
        self._saved_list_expanded = True
        self._servers_filter = "all"
        self._active_saved_connection: str | None = None
        self._t = self.theme_manager
        self._active_nav = "dashboard"
        self._brand_title: ctk.CTkLabel | None = None
        self._nav_header: ctk.CTkLabel | None = None
        self._nav_group_labels: list[ctk.CTkLabel] = []
        self._nav_rows: dict[str, ctk.CTkFrame] = {}
        self._nav_accents: dict[str, ctk.CTkFrame] = {}
        self._nav_labels: dict[str, str] = {}
        self._sidebar_collapsed = load_sidebar_collapsed()
        self._sidebar_width = load_sidebar_width(SIDEBAR_WIDTH)
        self._brand_row: ctk.CTkFrame | None = None
        self._nav_host: ctk.CTkScrollableFrame | ctk.CTkFrame | None = None
        self._nav_scroll_sync_job = None
        self._sidebar_sash: ctk.CTkFrame | None = None
        self._sidebar_toggle_busy = False
        self._sash_dragging = False
        self._sash_start_x = 0
        self._sash_start_w = 0
        self._sash_pending_w: int | None = None
        self._resize_guide = None  # lightweight tk line during drag only
        self._theme_switching = False
        self._page_refreshing = False
        self._session_poll_job: str | None = None
        self._session_poll_busy = False
        self.shell_header: ShellHeader | None = None
        self._sign_out_wrap: ctk.CTkFrame | None = None
        self._sign_out_divider: ctk.CTkFrame | None = None
        self._sign_out_btn: ctk.CTkButton | None = None
        self._identity_card: ctk.CTkFrame | None = None
        self._identity_avatar: ctk.CTkLabel | None = None
        self._identity_name: ctk.CTkLabel | None = None
        self._identity_meta: ctk.CTkLabel | None = None
        self._identity_role: ctk.CTkLabel | None = None
        self._last_updated = None
        self.log_store = ExecutionLogStore()
        self._active_log_panel: ExecutionLogPanel | None = None

        self.title(APP_NAME)
        self.configure(fg_color=self._t.c("bg_app"))
        schedule_window_icon(self)
        self.minsize(1024, 680)
        self._maximize_window()

        self._build_layout()
        self.navigate("dashboard")
        self.after(400, self._refresh_dashboard_on_login)
        if self.connection:
            self._start_session_status_poll()
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
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(
            self,
            width=self._sidebar_width,
            corner_radius=0,
            fg_color=self._t.sc("bg_sidebar") if self._t.is_light else self._t.c("bg_sidebar"),
            border_width=1,
            border_color=self._t.sc("border") if self._t.is_light else self._t.c("border_subtle"),
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(2, weight=1)
        self.sidebar.grid_columnconfigure(0, weight=1)

        # Row 0: brand
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 2))
        self._brand_row = brand
        self._brand_title = ctk.CTkLabel(
            brand,
            text="DB Automation Pro",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=self._t.sc("text_primary"),
            anchor="w",
        )
        self._brand_title.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Row 1: account identity (Chetan-style card, matched to this shell)
        self._build_sidebar_identity()

        # Row 2: grouped navigation (scrollable so Settings / lower items stay reachable)
        sidebar_bg = self._t.sc("bg_sidebar") if self._t.is_light else self._t.c("bg_sidebar")
        nav_host = ctk.CTkScrollableFrame(
            self.sidebar,
            fg_color=sidebar_bg,
            corner_radius=0,
            border_width=0,
            scrollbar_button_color=self._t.c("sidebar_bg_hover"),
            scrollbar_button_hover_color=self._t.c("sidebar_border"),
        )
        nav_host.grid(row=2, column=0, sticky="nsew", padx=(2, 0), pady=(2, 2))
        self._nav_host = nav_host
        self._nav_group_labels = []
        self._nav_rows = {}
        self._nav_accents = {}
        self._nav_labels = {}
        # Hide CTk's default scrollbar until content actually overflows (prevents
        # the floating blue thumb in the empty sidebar region).
        self._set_nav_scrollbar_visible(False)
        try:
            canvas = getattr(nav_host, "_parent_canvas", None)
            if canvas is not None:
                canvas.bind("<Configure>", lambda _e: self._request_nav_scrollbar_sync(80), add="+")
        except Exception:
            pass

        idle_icon = self._t.sc("text_secondary") if self._t.is_light else self._t.c("nav_idle_text")
        for group_name, items in NAV_GROUPS:
            group_lbl = ctk.CTkLabel(
                nav_host,
                text=group_name.upper(),
                font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                text_color=self._t.sc("text_muted"),
                anchor="w",
            )
            group_lbl.pack(anchor="w", padx=8, pady=NAV_GROUP_PADY)
            self._nav_group_labels.append(group_lbl)

            for key, label, _ in items:
                self._nav_labels[key] = label
                row = ctk.CTkFrame(nav_host, fg_color="transparent", height=NAV_ROW_HEIGHT)
                row.pack(fill="x", pady=0)
                row.pack_propagate(False)

                accent = ctk.CTkFrame(
                    row,
                    width=3,
                    height=NAV_ROW_HEIGHT,
                    corner_radius=0,
                    fg_color="transparent",
                )
                accent.pack(side="left", fill="y")
                accent.pack_propagate(False)

                btn_command = self._toggle_servers_nav if key == "servers" else (lambda k=key: self.navigate(k))
                icon_img = lucide_ctk_image(key, idle_icon, size=15)
                btn = ctk.CTkButton(
                    row,
                    text=f"  {label}",
                    image=icon_img,
                    compound="left",
                    anchor="w",
                    height=NAV_ROW_HEIGHT,
                    corner_radius=0,
                    fg_color="transparent",
                    text_color=idle_icon,
                    hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                    font=ctk.CTkFont(family="Segoe UI", size=12),
                    command=btn_command,
                )
                btn._lucide_key = key  # type: ignore[attr-defined]
                btn._lucide_image = icon_img  # type: ignore[attr-defined]
                btn.pack(side="left", fill="both", expand=True)

                self.nav_buttons[key] = btn
                self._nav_rows[key] = row
                self._nav_accents[key] = accent

                if key == "servers":
                    self._servers_subnav_frame = ctk.CTkFrame(nav_host, fg_color="transparent")
                    self._servers_subnav_frame.pack(fill="x", pady=(0, 2))
                    self._servers_subnav_frame.pack_forget()

                    new_conn_btn = ctk.CTkButton(
                        self._servers_subnav_frame,
                        text="    +  New Connection",
                        anchor="w",
                        height=28,
                        corner_radius=0,
                        fg_color="transparent",
                        text_color=self._t.sc("text_muted") if self._t.is_light else self._t.c("text_muted"),
                        hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                        font=ctk.CTkFont(family="Segoe UI", size=11),
                        command=self._navigate_new_connection,
                    )
                    new_conn_btn.pack(fill="x", pady=0, padx=6)
                    self._servers_sub_buttons["new"] = new_conn_btn

                    all_btn = ctk.CTkButton(
                        self._servers_subnav_frame,
                        text="    ▼  Saved Connection",
                        anchor="w",
                        height=28,
                        corner_radius=0,
                        fg_color="transparent",
                        text_color=self._t.sc("text_muted") if self._t.is_light else self._t.c("text_muted"),
                        hover_color=self._t.sc("nav_hover") if self._t.is_light else self._t.c("bg_card_hover"),
                        font=ctk.CTkFont(family="Segoe UI", size=11),
                        command=self._toggle_saved_connections_nav,
                    )
                    all_btn.pack(fill="x", pady=0, padx=6)
                    self._servers_sub_buttons["all"] = all_btn

                    self._servers_saved_frame = ctk.CTkFrame(self._servers_subnav_frame, fg_color="transparent")
                    self._servers_saved_frame.pack(fill="x", padx=4, pady=(0, 2))
                    self._refresh_servers_saved_nav()

        self._nav_header = self._nav_group_labels[0] if self._nav_group_labels else None

        # Row 3: Sign Out pinned at bottom (outside the scrollable nav).
        self.sidebar.grid_rowconfigure(3, weight=0)
        sign_out_wrap = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        sign_out_wrap.grid(row=3, column=0, sticky="ew", padx=6, pady=(0, 8))
        self._sign_out_wrap = sign_out_wrap

        border = self._t.sc("border") if self._t.is_light else self._t.c("border_subtle")
        self._sign_out_divider = ctk.CTkFrame(
            sign_out_wrap, height=1, fg_color=border, corner_radius=0
        )
        self._sign_out_divider.pack(fill="x", padx=8, pady=(2, 4))

        danger = self._t.c("destructive_text")
        sign_out_img = lucide_ctk_image("sign_out", danger, size=15)
        self._sign_out_btn = ctk.CTkButton(
            sign_out_wrap,
            text="  Sign Out",
            image=sign_out_img,
            compound="left",
            anchor="w",
            height=NAV_ROW_HEIGHT,
            corner_radius=0,
            fg_color="transparent",
            text_color=danger,
            hover_color=self._t.c("destructive_bg"),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._sign_out,
        )
        self._sign_out_btn._lucide_image = sign_out_img  # type: ignore[attr-defined]
        self._sign_out_btn.pack(fill="x")

        self._request_nav_scrollbar_sync(80)
        self.bind("<Configure>", self._on_shell_configure_nav_scroll, add="+")

        # Thin drag handle between sidebar and content (Claude-style resize).
        self._sidebar_sash = ctk.CTkFrame(
            self,
            width=SIDEBAR_SASH_WIDTH,
            corner_radius=0,
            fg_color=self._t.c("border_subtle"),
            cursor="sb_h_double_arrow",
        )
        self._sidebar_sash.grid(row=0, column=1, sticky="ns")
        self._sidebar_sash.grid_propagate(False)
        self._sidebar_sash.bind("<Enter>", self._on_sash_enter)
        self._sidebar_sash.bind("<Leave>", self._on_sash_leave)
        self._sidebar_sash.bind("<ButtonPress-1>", self._on_sash_press)
        self._sidebar_sash.bind("<B1-Motion>", self._on_sash_motion)
        self._sidebar_sash.bind("<ButtonRelease-1>", self._on_sash_release)

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color=self._t.c("bg_content"))
        self.content.grid(row=0, column=2, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.grid_rowconfigure(2, weight=0)

        self.shell_header = ShellHeader(
            self.content,
            self._t,
            employee=self.employee,
            connection=self.connection,
            on_refresh=self._refresh_active_view,
            on_theme=self._toggle_theme,
            on_toggle_sidebar=self._toggle_sidebar,
            sidebar_collapsed=self._sidebar_collapsed,
        )
        self.shell_header.grid(row=0, column=0, sticky="ew")

        self.view_container = ctk.CTkFrame(
            self.content,
            fg_color=self._t.c("bg_content"),
        )
        self.view_container.grid(row=1, column=0, sticky="nsew")
        self.view_container.grid_columnconfigure(0, weight=1)
        self.view_container.grid_rowconfigure(0, weight=1)

        self.status_footer = StatusFooterBar(self.content, self._t)
        self.status_footer.grid(row=2, column=0, sticky="ew")

        # Root only — bind_all can re-enter and freeze the UI on Windows/CTk.
        self.bind("<Control-b>", self._on_ctrl_b)
        self.bind("<Control-B>", self._on_ctrl_b)
        self._apply_sidebar_mode()

    def _build_sidebar_identity(self) -> None:
        """Compact account card — name + role only (no username)."""
        wrap = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        wrap.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 2))

        card = ctk.CTkFrame(
            wrap,
            fg_color=self._t.c("sidebar_bg_card"),
            corner_radius=self._t.radius("sm"),
            border_width=1,
            border_color=self._t.c("sidebar_border"),
        )
        card.pack(fill="x")
        self._identity_card = card

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=6)

        self._identity_avatar = ctk.CTkLabel(
            inner,
            text=initials(self.employee),
            width=28,
            height=28,
            corner_radius=14,
            fg_color=self._t.c("sidebar_bg_hover"),
            text_color=self._t.c("sidebar_text_primary"),
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
        )
        self._identity_avatar.pack(side="left")

        text_col = ctk.CTkFrame(inner, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self._identity_name = ctk.CTkLabel(
            text_col,
            text=display_name(self.employee),
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=self._t.c("sidebar_text_primary"),
            anchor="w",
        )
        self._identity_name.pack(anchor="w")

        self._identity_role = ctk.CTkLabel(
            text_col,
            text=role_label(self.employee),
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=self._t.c("sidebar_text_muted"),
            anchor="w",
        )
        self._identity_role.pack(anchor="w")
        self._identity_meta = None

    def _on_shell_configure_nav_scroll(self, _event=None) -> None:
        if getattr(self, "_nav_scroll_sync_job", None):
            try:
                self.after_cancel(self._nav_scroll_sync_job)
            except Exception:
                pass
        self._nav_scroll_sync_job = self.after(120, self._sync_nav_scrollbar)

    def _request_nav_scrollbar_sync(self, delay_ms: int = 80) -> None:
        """Debounced scrollbar sync — call after nav expand/collapse/resize."""
        if getattr(self, "_nav_scroll_sync_job", None):
            try:
                self.after_cancel(self._nav_scroll_sync_job)
            except Exception:
                pass
        self._nav_scroll_sync_job = self.after(delay_ms, self._sync_nav_scrollbar)

    def _set_nav_scrollbar_visible(self, visible: bool) -> None:
        """Theme the nav scrollbar; collapse width when unused (keep grid so scroll works)."""
        nav = self._nav_host
        if nav is None:
            return
        scrollbar = getattr(nav, "_scrollbar", None)
        if scrollbar is None:
            return
        # Always keep the CTk grid slot so mousewheel / yview keep working.
        if not scrollbar.winfo_manager():
            try:
                scrollbar.grid(row=0, column=1, sticky="ns")
            except Exception:
                pass
        if visible:
            try:
                scrollbar.configure(
                    width=10,
                    button_color=self._t.c("sidebar_bg_hover"),
                    button_hover_color=self._t.c("sidebar_border"),
                    fg_color=self._t.c("bg_sidebar"),
                )
            except Exception:
                pass
            return
        # Invisible when content fits — no floating thumb in empty sidebar space.
        try:
            sidebar_bg = self._t.c("bg_sidebar")
            scrollbar.configure(
                width=0,
                button_color=sidebar_bg,
                button_hover_color=sidebar_bg,
                fg_color=sidebar_bg,
            )
        except Exception:
            pass

    def _sync_nav_scrollbar(self) -> None:
        """Show the nav scrollbar only when the list actually overflows."""
        self._nav_scroll_sync_job = None
        nav = self._nav_host
        if nav is None or not hasattr(nav, "_scrollbar"):
            return
        try:
            nav.update_idletasks()
            canvas = getattr(nav, "_parent_canvas", None)
            if canvas is None:
                return
            visible_h = int(canvas.winfo_height())
            # Layout not ready yet — retry shortly instead of flashing the bar.
            if visible_h < 24:
                self._request_nav_scrollbar_sync(60)
                return
            bbox = canvas.bbox("all")
            if not bbox:
                self._set_nav_scrollbar_visible(False)
                return
            content_h = int(bbox[3] - bbox[1])
            overflows = content_h > visible_h + 2
            self._set_nav_scrollbar_visible(overflows)
            # If everything fits, snap back to top so Settings isn't left off-screen.
            if not overflows:
                try:
                    canvas.yview_moveto(0)
                except Exception:
                    pass
        except Exception:
            self._set_nav_scrollbar_visible(False)

    def _apply_sidebar_identity_theme(self, t: ThemeManager) -> None:
        if self._identity_card is None:
            return
        self._identity_card.configure(
            fg_color=t.c("sidebar_bg_card"),
            border_color=t.c("sidebar_border"),
        )
        if self._identity_avatar is not None:
            self._identity_avatar.configure(
                text=initials(self.employee),
                fg_color=t.c("sidebar_bg_hover"),
                text_color=t.c("sidebar_text_primary"),
            )
        if self._identity_name is not None:
            self._identity_name.configure(
                text=display_name(self.employee),
                text_color=t.c("sidebar_text_primary"),
            )
        if self._identity_role is not None:
            self._identity_role.configure(
                text=role_label(self.employee),
                text_color=t.c("sidebar_text_muted"),
            )

    def _register_log_panel(self, panel: ExecutionLogPanel | None) -> None:
        self._active_log_panel = panel
        if panel is not None:
            panel._render()

    def _on_ctrl_b(self, _event=None):
        self._toggle_sidebar()
        return "break"

    def _toggle_sidebar(self) -> None:
        """Schedule hide/show outside the button event — avoids CTk freeze."""
        if self._sidebar_toggle_busy or self._sash_dragging:
            return
        self._sidebar_toggle_busy = True
        self._sidebar_collapsed = not self._sidebar_collapsed
        self.after(1, self._apply_sidebar_mode)

    def _apply_sidebar_mode(self) -> None:
        """Hide = width 1; show = saved width. Resize via sash only when visible."""
        try:
            collapsed = self._sidebar_collapsed
            if collapsed:
                self.sidebar.configure(width=1)
                if self._sidebar_sash is not None:
                    self._sidebar_sash.configure(cursor="hand2")
            else:
                self.sidebar.configure(width=max(SIDEBAR_MIN_WIDTH, self._sidebar_width))
                if self._sidebar_sash is not None:
                    self._sidebar_sash.configure(cursor="sb_h_double_arrow")
            if self.shell_header is not None:
                self.shell_header.set_sidebar_collapsed(collapsed)
            self.after(0, lambda c=collapsed: save_sidebar_collapsed(c))
        except Exception:
            pass
        finally:
            self.after(120, self._clear_sidebar_toggle_busy)

    def _clear_sidebar_toggle_busy(self) -> None:
        self._sidebar_toggle_busy = False

    def _clamp_sidebar_width(self, width: int) -> int:
        return max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, int(width)))

    def _on_sash_enter(self, _event=None) -> None:
        if self._sash_dragging or self._sidebar_sash is None:
            return
        self._sidebar_sash.configure(fg_color=self._t.c("primary"))

    def _on_sash_leave(self, _event=None) -> None:
        if self._sash_dragging or self._sidebar_sash is None:
            return
        self._sidebar_sash.configure(fg_color=self._t.c("border_subtle"))

    def _on_sash_press(self, event) -> None:
        if self._sidebar_collapsed:
            self._toggle_sidebar()
            return
        self._sash_dragging = True
        self._sash_start_x = int(event.x_root)
        self._sash_start_w = int(self.sidebar.winfo_width() or self._sidebar_width)
        self._sash_pending_w = self._sash_start_w
        self._show_resize_guide(self._sash_start_w)
        # Track outside the thin sash so drag doesn't stall.
        self.bind("<B1-Motion>", self._on_sash_motion)
        self.bind("<ButtonRelease-1>", self._on_sash_release)

    def _show_resize_guide(self, width: int) -> None:
        """Cheap visual line only — never resizes CTk widgets while dragging."""
        import tkinter as tk

        color = self._t.c("primary")
        if self._resize_guide is None:
            self._resize_guide = tk.Frame(self, width=2, bg=color, cursor="sb_h_double_arrow")
        else:
            try:
                self._resize_guide.configure(bg=color)
            except Exception:
                pass
        try:
            self._resize_guide.place(x=max(0, width), y=0, relheight=1.0, width=2)
            self._resize_guide.lift()
        except Exception:
            pass

    def _hide_resize_guide(self) -> None:
        guide = self._resize_guide
        self._resize_guide = None
        if guide is not None:
            try:
                guide.place_forget()
                guide.destroy()
            except Exception:
                pass

    def _on_sash_motion(self, event) -> None:
        if not self._sash_dragging or self._sidebar_collapsed:
            return
        delta = int(event.x_root) - self._sash_start_x
        width = self._clamp_sidebar_width(self._sash_start_w + delta)
        if width == self._sash_pending_w:
            return
        self._sash_pending_w = width
        # Move guide only — do NOT call sidebar.configure here (that freezes CTk).
        self._show_resize_guide(width)

    def _on_sash_release(self, _event=None) -> None:
        if not self._sash_dragging:
            return
        self._sash_dragging = False
        try:
            self.unbind("<B1-Motion>")
            self.unbind("<ButtonRelease-1>")
        except Exception:
            pass
        self._hide_resize_guide()
        width = self._clamp_sidebar_width(self._sash_pending_w or self._sidebar_width)
        self._sidebar_width = width
        self._sash_pending_w = None
        # Single layout pass after the drag ends.
        try:
            self.sidebar.configure(width=width)
        except Exception:
            pass
        self.after(0, lambda w=width: save_sidebar_width(w))
        if self._sidebar_sash is not None:
            try:
                self._sidebar_sash.configure(fg_color=self._t.c("border_subtle"))
            except Exception:
                pass

    def _repack_nav_groups(self) -> None:
        """Show group labels again in expanded mode (packed before each group's first row)."""
        if self._nav_host is None:
            return
        group_first: list[tuple[ctk.CTkLabel, str]] = []
        idx = 0
        for _group_name, items in NAV_GROUPS:
            if idx >= len(self._nav_group_labels) or not items:
                continue
            first_key = items[0][0]
            group_first.append((self._nav_group_labels[idx], first_key))
            idx += 1

        for lbl, first_key in group_first:
            row = self._nav_rows.get(first_key)
            if row is None:
                continue
            if lbl.winfo_manager():
                lbl.pack_forget()
            try:
                lbl.pack(anchor="w", padx=8, pady=(8, 2), before=row)
            except Exception:
                lbl.pack(anchor="w", padx=8, pady=(8, 2))

    def refresh_server_databases(self) -> None:
        db_view = self.views.get("databases")
        if db_view and hasattr(db_view, "refresh"):
            db_view.refresh()
        self._refresh_dashboard_on_login()

    def _refresh_dashboard_on_login(self) -> None:
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
        apply_connection(connection)
        effective = open_ssh_tunnel(connection)
        # Re-apply so DB uses the tunnel bind address while SSH extras keep the real host.
        apply_connection(effective)
        self.set_connection(effective, navigate_to=navigate_to)

    def set_connection(
        self,
        connection: ConnectionResult,
        *,
        navigate_to: str | None = None,
    ) -> None:
        self.connection = resolve_active_connection(connection)
        # Keep Servers workspace on the profile we just connected (avoid reset to New PostgreSQL).
        saved = connection.saved_name or connection.name
        if saved:
            self._active_saved_connection = saved
            self._servers_filter = "all"
            self._saved_list_expanded = True
            self._refresh_servers_saved_nav()
        endpoint = self.connection.endpoint_label
        self.append_log(f"Connected: {self.connection.name} ({endpoint})")
        self.update_status_footer(
            connection_status=f"● Connected · {self.connection.name}",
        )
        if self.shell_header:
            self.shell_header.set_connection(self.connection)
        self._start_session_status_poll()
        # Keep pages alive — push connection instead of destroying the workspace.
        self.after(0, lambda: self._on_connection_changed(navigate_to=navigate_to))

    def clear_connection(self) -> None:
        if self.connection:
            self.append_log(f"Disconnected from {self.connection.name}")
        close_ssh_tunnel()
        self.connection = None
        self._stop_session_status_poll()
        self.update_status_footer(idle=True)
        if self.shell_header:
            self.shell_header.set_connection(None)
        self.after(0, lambda: self._on_connection_changed(navigate_to=None, disconnected=True))

    def _start_session_status_poll(self) -> None:
        """Refresh Version / Uptime / DB sessions even when Dashboard is hidden."""
        self._stop_session_status_poll()
        self._poll_session_status()

    def _stop_session_status_poll(self) -> None:
        self._session_poll_busy = False
        job = self._session_poll_job
        self._session_poll_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass

    def _poll_session_status(self) -> None:
        if not self.connection:
            self.update_status_footer(idle=True)
            return
        if self._session_poll_busy:
            self._session_poll_job = self.after(15000, self._poll_session_status)
            return

        connection = self.connection
        self._session_poll_busy = True

        def worker() -> None:
            metrics: dict = {}
            try:
                from desktop.db_performance import fetch_database_performance

                metrics = fetch_database_performance(connection)
            except Exception:
                metrics = {}
            try:
                self.after(0, lambda m=metrics: self._apply_session_status(m))
            except RuntimeError:
                pass

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def _apply_session_status(self, metrics: dict) -> None:
        self._session_poll_busy = False
        if not self.connection:
            self.update_status_footer(idle=True)
            return

        name = self.connection.saved_name or self.connection.name
        version = str(metrics.get("version") or "").strip() or "—"
        uptime = str(metrics.get("uptime") or "—")
        if metrics.get("connected"):
            sessions = str(int(metrics.get("connections_num") or 0))
        else:
            sessions = "—"
        self.update_status_footer(
            connection_status=f"● Connected · {name}",
            version=version,
            uptime=uptime,
            sessions=sessions,
        )
        self._session_poll_job = self.after(15000, self._poll_session_status)

    def mark_last_updated(self, when=None) -> None:
        """Single shared refresh stamp for the shell header."""
        from datetime import datetime

        self._last_updated = when or datetime.now()
        if self.shell_header:
            self.shell_header.set_last_updated(self._last_updated)

    def set_updating(self) -> None:
        if self.shell_header:
            self.shell_header.set_updating()

    def _refresh_active_view(self) -> None:
        """Header Refresh: reload the visible workspace page with a loading state."""
        if getattr(self, "_page_refreshing", False):
            return
        key = self._active_nav
        if not key:
            return
        self._page_refreshing = True
        if self.shell_header:
            self.shell_header.set_refreshing(True)
        self.set_updating()
        # Run outside the button event so CTk stays responsive.
        self.after(1, lambda k=key: self._run_active_view_refresh(k))

    def _run_active_view_refresh(self, key: str) -> None:
        try:
            view = self.views.get(key)
            refreshed = False
            if view is not None and callable(getattr(view, "refresh", None)):
                try:
                    view.refresh(force=True)  # type: ignore[call-arg]
                    refreshed = True
                except TypeError:
                    view.refresh()
                    refreshed = True

            if not refreshed:
                # Pages without refresh(): hard-reload by recreating the view.
                if view is not None:
                    try:
                        view.destroy()
                    except Exception:
                        pass
                    self.views.pop(key, None)
                extras: dict = {}
                if key == "servers":
                    extras["servers_filter"] = self._servers_filter or "all"
                    if self._active_saved_connection:
                        extras["saved_server"] = self._active_saved_connection
                elif key == "dashboard":
                    extras["initial_refresh"] = False
                self.navigate(key, log=False, view_extra=extras)
                if key == "dashboard":
                    dash = self.views.get("dashboard")
                    if dash and hasattr(dash, "refresh"):
                        dash.refresh()

            if key != "dashboard":
                self.mark_last_updated()
            if self.connection:
                self._poll_session_status()
            self.append_log(f"Refreshed {key.replace('_', ' ')}", "INFO")
        except Exception as exc:
            try:
                self.append_log(f"Refresh failed: {exc}", "ERROR")
            except Exception:
                pass
            self.mark_last_updated()
        finally:
            self._page_refreshing = False
            if self.shell_header:
                self.shell_header.set_refreshing(False)

    def _on_connection_changed(
        self,
        *,
        navigate_to: str | None = None,
        disconnected: bool = False,
    ) -> None:
        self._propagate_connection()
        if disconnected and self._active_nav in CONNECTION_REQUIRED_KEYS:
            self.navigate("dashboard", log=False)
        elif navigate_to:
            self.navigate(navigate_to)
        elif self._active_nav in self.views:
            view = self.views[self._active_nav]
            if self._active_nav == "dashboard" and hasattr(view, "resume"):
                view.resume()
            elif hasattr(view, "refresh"):
                view.refresh()

    def _propagate_connection(self) -> None:
        """Update cached views with the current connection without destroying them."""
        for key, view in list(self.views.items()):
            if not view.winfo_exists():
                self.views.pop(key, None)
                continue
            if hasattr(view, "set_connection"):
                view.set_connection(self.connection)
            else:
                try:
                    view.connection = self.connection
                except Exception:
                    pass

    def _reload_views(self) -> None:
        """Theme rebuild path only — destroys and recreates pages for baked colors."""
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
            self._saved_list_expanded = True
        else:
            self._active_saved_connection = None
        self._refresh_servers_saved_nav()
        self._style_servers_subnav()

    def _toggle_servers_nav(self) -> None:
        """Expand/collapse the full Servers sub-menu."""
        if self._servers_nav_expanded and self._active_nav == "servers":
            self._servers_nav_expanded = False
            self._style_servers_subnav()
            self._update_servers_nav_label()
            return

        self._servers_nav_expanded = True
        self._update_servers_nav_label()
        self._navigate_servers(self._servers_filter or "all")

    def _update_servers_nav_label(self) -> None:
        btn = self.nav_buttons.get("servers")
        if btn is None:
            return
        btn.configure(text="  Servers")

    def _nav_icon_color(self, *, active: bool) -> str:
        t = self._t
        if active:
            return t.c("nav_active_text")
        return t.sc("text_secondary") if t.is_light else t.c("nav_idle_text")

    def _apply_nav_icon(self, key: str, btn: ctk.CTkButton, *, active: bool) -> None:
        color = self._nav_icon_color(active=active)
        img = lucide_ctk_image(key, color, size=16)
        btn.configure(image=img)
        btn._lucide_image = img  # type: ignore[attr-defined]

    def _toggle_saved_connections_nav(self) -> None:
        """Expand/collapse the Saved Connection list under Servers."""
        if self._saved_list_expanded and (
            self._servers_filter == "all" or self._active_saved_connection
        ):
            # Currently open → collapse / unclick
            self._saved_list_expanded = False
            self._active_saved_connection = None
            self._apply_saved_list_visibility()
            self._style_servers_subnav()
            return

        # Closed → expand and show saved list
        self._saved_list_expanded = True
        self._apply_saved_list_visibility()
        self._navigate_servers("all")

    def _apply_saved_list_visibility(self) -> None:
        if self._servers_saved_frame is None:
            return
        all_btn = self._servers_sub_buttons.get("all")
        if self._saved_list_expanded:
            if not self._servers_saved_frame.winfo_manager():
                if all_btn is not None and all_btn.winfo_manager():
                    self._servers_saved_frame.pack(after=all_btn, fill="x", padx=4, pady=(0, 4))
                else:
                    self._servers_saved_frame.pack(fill="x", padx=4, pady=(0, 4))
            if all_btn is not None:
                all_btn.configure(text="    ▼  Saved Connection")
        else:
            self._servers_saved_frame.pack_forget()
            if all_btn is not None:
                all_btn.configure(text="    ▶  Saved Connection")

    def _navigate_servers(self, servers_filter: str) -> None:
        self._active_saved_connection = None
        self._servers_nav_expanded = True
        self._update_servers_nav_label()
        if servers_filter == "all":
            self._saved_list_expanded = True
            self._apply_saved_list_visibility()
        self.navigate("servers", view_extra={"servers_filter": servers_filter})

    def _navigate_new_connection(self) -> None:
        self._servers_nav_expanded = True
        self._update_servers_nav_label()
        self._active_saved_connection = None
        self.navigate(
            "servers",
            view_extra={"servers_filter": "all", "new_connection": True},
        )

    def _navigate_saved_server(self, server_name: str) -> None:
        self._servers_nav_expanded = True
        self._update_servers_nav_label()
        self._saved_list_expanded = True
        self._apply_saved_list_visibility()
        self._active_saved_connection = server_name
        self.navigate(
            "servers",
            view_extra={"servers_filter": "all", "saved_server": server_name},
        )

    def _refresh_servers_saved_nav(self) -> None:
        if self._servers_saved_frame is None:
            return

        for widget in self._servers_saved_frame.winfo_children():
            widget.destroy()
        self._servers_saved_buttons.clear()

        servers = sorted(load_saved_servers(), key=lambda s: s.name.lower())
        # Hide duplicate names (same label would break click/selection).
        seen: set[str] = set()
        unique_servers = []
        for server in servers:
            key = server.name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique_servers.append(server)
        servers = unique_servers
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
                text=f"          {meta['abbrev']}  {server.name}",
                anchor="w",
                height=28,
                corner_radius=6,
                fg_color="transparent",
                text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                hover_color=t.sc("nav_hover") if t.is_light else t.c("bg_card_hover"),
                font=ctk.CTkFont(size=11),
                command=lambda name=server.name: self._navigate_saved_server(name),
            )
            btn.pack(fill="x", pady=1, padx=12)
            self._servers_saved_buttons[server.name] = btn

        self._apply_saved_list_visibility()
        self._style_servers_subnav()

    def _style_servers_subnav(self) -> None:
        t = self._t
        on_servers = self._active_nav == "servers"
        show_subnav = (
            on_servers
            and self._servers_nav_expanded
            and not self._sidebar_collapsed
        )
        if self._servers_subnav_frame is not None:
            if show_subnav:
                if not self._servers_subnav_frame.winfo_manager():
                    servers_row = self._nav_rows.get("servers")
                    if servers_row is not None:
                        self._servers_subnav_frame.pack(after=servers_row, fill="x", pady=(0, 2))
                    else:
                        self._servers_subnav_frame.pack(fill="x", pady=(0, 2))
            else:
                self._servers_subnav_frame.pack_forget()

        self._update_servers_nav_label()
        self._apply_saved_list_visibility()

        for sub_key, btn in self._servers_sub_buttons.items():
            if sub_key == "all":
                active = (
                    show_subnav
                    and self._saved_list_expanded
                    and (self._servers_filter == "all" or bool(self._active_saved_connection))
                )
            else:
                active = (
                    show_subnav
                    and self._servers_filter == sub_key
                    and not self._active_saved_connection
                )
            if active:
                btn.configure(
                    fg_color=t.sc("nav_active") if t.is_light else t.c("nav_active_bg"),
                    text_color=t.c("nav_active_text"),
                    border_width=0,
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
                    fg_color=t.sc("nav_active") if t.is_light else t.c("nav_active_bg"),
                    text_color=t.c("nav_active_text"),
                    border_width=0,
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                    border_width=0,
                )
        self._request_nav_scrollbar_sync(60)

    def _style_navigation(self, key: str) -> None:
        t = self._t
        accent_color = t.sc("nav_accent") if t.is_light else t.c("nav_indicator")
        active_bg = t.sc("nav_active") if t.is_light else t.c("nav_active_bg")
        for k, btn in self.nav_buttons.items():
            active = k == key
            accent = self._nav_accents.get(k)
            row = self._nav_rows.get(k)
            if active:
                if accent is not None:
                    accent.configure(fg_color=accent_color)
                if row is not None:
                    row.configure(fg_color=active_bg)
                btn.configure(
                    fg_color=active_bg,
                    text_color=t.c("nav_active_text"),
                    border_width=0,
                )
            else:
                if accent is not None:
                    accent.configure(fg_color="transparent")
                if row is not None:
                    row.configure(fg_color="transparent")
                btn.configure(
                    fg_color="transparent",
                    text_color=t.sc("text_secondary") if t.is_light else t.c("nav_idle_text"),
                    border_width=0,
                )
            self._apply_nav_icon(k, btn, active=active)
        self._style_servers_subnav()

    def navigate(self, key: str, *, log: bool = True, view_extra: dict | None = None) -> None:
        if key in CONNECTION_REQUIRED_KEYS and self.connection is None:
            # Land on the dashboard rather than opening a page that has
            # nothing to operate on, then explain why.
            if self._active_nav != "dashboard":
                self.navigate("dashboard", log=False)
            self._prompt_connect_required(key)
            return

        extra = view_extra or {}
        if key == "servers":
            self._servers_filter = extra.get("servers_filter", self._servers_filter or "all")
            if extra.get("open_saved"):
                # Dashboard "Saved Servers": expand the list and mark the live profile.
                self._servers_nav_expanded = True
                self._saved_list_expanded = True
                self._servers_filter = "all"
                connected = (extra.get("saved_server") or "").strip()
                self._active_saved_connection = connected or None
            elif "saved_server" in extra:
                self._servers_nav_expanded = True
                self._saved_list_expanded = True
                self._active_saved_connection = extra.get("saved_server")
            elif self._servers_filter != "all":
                self._active_saved_connection = None

        self._active_nav = key
        if key != "servers":
            self._servers_nav_expanded = False
        self._style_navigation(key)

        for view in self.views.values():
            view.grid_forget()

        # Drop stale Databases / Audit chrome (old in-page Refresh sat under the shell Refresh).
        if key in {"databases", "audit"} and key in self.views:
            try:
                self.views[key].destroy()
            except Exception:
                pass
            self.views.pop(key, None)

        if key not in self.views:
            for nav_key, _, view_cls in NAV_ITEMS:
                if nav_key == key:
                    kwargs: dict = {
                        "log_callback": self.append_log,
                        "connection": self.connection,
                    }
                    if view_cls is DashboardView:
                        kwargs["navigate_callback"] = self.navigate
                        kwargs["employee"] = self.employee
                        if "initial_refresh" in extra:
                            kwargs["initial_refresh"] = extra["initial_refresh"]
                    elif view_cls is ServersView:
                        kwargs["establish_callback"] = (
                            lambda c: self._establish_connection(c, navigate_to=None)
                        )
                        kwargs["disconnect_callback"] = self.clear_connection
                        kwargs["saved_servers_changed_callback"] = self._on_saved_connection_changed
                        kwargs["servers_filter"] = self._servers_filter
                        restore = extra.get("saved_server") or self._active_saved_connection
                        if not restore and self.connection:
                            restore = self.connection.saved_name or self.connection.name
                        if restore:
                            kwargs["saved_server"] = restore
                        if extra.get("new_connection"):
                            kwargs["new_connection"] = True
                    elif view_cls is LogsView:
                        kwargs["log_store"] = self.log_store
                        kwargs["register_log_panel"] = self._register_log_panel
                        if extra.get("log_filter"):
                            kwargs["log_filter"] = extra["log_filter"]
                    if key == "audit":
                        kwargs["auto_action"] = "audit"
                    self.views[key] = view_cls(
                        self.view_container,
                        self.theme_manager,
                        **kwargs,
                    )
                    self.views[key].grid(
                        row=0, column=0, sticky="nsew", pady=(0, CONTENT_BOTTOM_INSET)
                    )
                    break
        else:
            self.views[key].grid(
                row=0, column=0, sticky="nsew", pady=(0, CONTENT_BOTTOM_INSET)
            )
            if key == "servers":
                servers_view = self.views[key]
                if hasattr(servers_view, "set_servers_filter"):
                    servers_view.set_servers_filter(self._servers_filter)
                if extra.get("new_connection") and hasattr(servers_view, "start_new_connection"):
                    servers_view.start_new_connection()
                else:
                    saved = extra.get("saved_server")
                    if saved and hasattr(servers_view, "load_saved_server"):
                        servers_view.load_saved_server(saved)
                    elif not saved and hasattr(servers_view, "refresh"):
                        servers_view.refresh()
            elif key == "logs":
                logs_view = self.views[key]
                if hasattr(logs_view, "apply_log_filter"):
                    # None clears a previous alerts/jobs chip from Dashboard.
                    logs_view.apply_log_filter(extra.get("log_filter"))
            elif key == "dashboard" and hasattr(self.views[key], "resume"):
                self.views[key].resume()
            elif hasattr(self.views[key], "refresh"):
                self.views[key].refresh()

        self._style_servers_subnav()

        if log:
            self.append_log(f"Navigated to {key}")

    def _prompt_connect_required(self, key: str) -> None:
        label = next((lbl for k, lbl, _ in NAV_ITEMS if k == key), key.title())
        confirm(
            self,
            self._t,
            title="Connect to a database first",
            message=(
                f"{label} works against your active database connection, and "
                "you don't have one yet. Connect to a server, then open "
                f"{label} again."
            ),
            confirm_text="Go to Servers",
            cancel_text="Cancel",
            on_confirm=lambda: self.navigate("servers"),
        )

    def _sign_out(self) -> None:
        confirm(
            self,
            self._t,
            title="Sign out?",
            message=(
                "You'll be returned to the login screen and any active "
                "database connection will be closed."
            ),
            confirm_text="Sign Out",
            destructive=True,
            on_confirm=self._do_sign_out,
        )

    def _do_sign_out(self) -> None:
        close_ssh_tunnel()
        self.disconnect_requested = True
        self.destroy()

    def _toggle_theme(self) -> None:
        """Queue the switch off the button event — sync destroy freezes CTk on Windows."""
        if getattr(self, "_theme_switching", False):
            return
        self._theme_switching = True
        # Failsafe so a hung switch can't permanently disable the Light/Dark button.
        self.after(4000, self._clear_theme_switching)
        self.after(1, self._run_theme_switch)

    def _clear_theme_switching(self) -> None:
        self._theme_switching = False

    def _run_theme_switch(self) -> None:
        try:
            new_theme = self.theme_manager.toggle()
            self._t = self.theme_manager
            self._apply_theme(new_theme)
        except Exception as exc:
            self._theme_switching = False
            try:
                self.append_log(f"Theme switch failed: {exc}", "ERROR")
            except Exception:
                pass

    def _apply_theme(self, theme_name: str) -> None:
        t = self._t
        current = self._active_nav or "dashboard"
        try:
            self.configure(fg_color=t.c("bg_app"))
            sidebar_bg = t.sc("bg_sidebar") if t.is_light else t.c("bg_sidebar")
            self.sidebar.configure(
                fg_color=sidebar_bg,
                border_color=t.sc("border") if t.is_light else t.c("border_subtle"),
            )
            if self._nav_host is not None:
                try:
                    self._nav_host.configure(
                        fg_color=sidebar_bg,
                        scrollbar_button_color=t.c("sidebar_bg_hover"),
                        scrollbar_button_hover_color=t.c("sidebar_border"),
                    )
                except Exception:
                    self._nav_host.configure(fg_color=sidebar_bg)
            self.content.configure(fg_color=t.c("bg_content"))
            self.view_container.configure(fg_color=t.c("bg_content"))

            if self._brand_title:
                self._brand_title.configure(text_color=t.sc("text_primary"))
            self._apply_sidebar_identity_theme(t)
            for group_lbl in self._nav_group_labels:
                group_lbl.configure(text_color=t.sc("text_muted"))
            if self._nav_header and self._nav_header not in self._nav_group_labels:
                self._nav_header.configure(text_color=t.sc("text_muted"))

            for key, btn in self.nav_buttons.items():
                btn.configure(
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
            if self._sign_out_divider is not None:
                self._sign_out_divider.configure(
                    fg_color=t.sc("border") if t.is_light else t.c("border_subtle"),
                )
            if self._sign_out_btn is not None:
                danger = t.c("destructive_text")
                img = lucide_ctk_image("sign_out", danger, size=15)
                self._sign_out_btn.configure(
                    text_color=danger,
                    hover_color=t.c("destructive_bg"),
                    image=img,
                )
                self._sign_out_btn._lucide_image = img  # type: ignore[attr-defined]
            self._apply_sidebar_mode()

            if self.shell_header:
                self.shell_header.apply_theme(t)
                self.shell_header.set_connection(self.connection)
            self.status_footer.apply_theme(t)

            for view in list(self.views.values()):
                try:
                    view.destroy()
                except Exception:
                    pass
            self.views.clear()
        except Exception as exc:
            self._theme_switching = False
            try:
                self.append_log(f"Theme apply failed: {exc}", "ERROR")
            except Exception:
                pass
            return

        self.update_idletasks()
        self.after(1, lambda: self._finish_theme_switch(theme_name, current))

    def _finish_theme_switch(self, theme_name: str, current: str) -> None:
        try:
            view_extra: dict = {}
            if current == "dashboard":
                view_extra["initial_refresh"] = False
            self.navigate(current or "dashboard", log=False, view_extra=view_extra)
            self.append_log(f"Theme switched to {theme_name}", "SUCCESS")

            if current == "dashboard":
                dash = self.views.get("dashboard")
                if dash and hasattr(dash, "refresh"):
                    self.after(300, dash.refresh)
            self.after(150, self.refresh_server_databases)
        except Exception as exc:
            try:
                self.append_log(f"Theme switch failed: {exc}", "ERROR")
            except Exception:
                pass
        finally:
            self._theme_switching = False
