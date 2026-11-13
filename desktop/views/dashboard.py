"""Premium Beacon-inspired Database Operations Command Center."""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService
from desktop.connection_store import load_saved_servers
from desktop.db_performance import fetch_database_performance
from desktop.db_performance_widgets import build_database_performance_section
from desktop.design_system import font
from desktop.employee_store import EmployeeSession
from desktop.lucide_icons import lucide_icon_badge
from desktop.ui_helpers import make_clickable
from desktop.ops_widgets import (
    beacon_hero_header,
    compact_kpi_card,
    empty_state,
)
from desktop.theme import CONTENT_BOTTOM_INSET, ThemeManager
from desktop.views.base_view import BaseView


def _engine_label(db_type) -> str:
    """Canonical ENGINE column text from a saved/live db_type value."""
    raw = getattr(db_type, "value", db_type)
    text = str(raw or "").strip().lower()
    if not text:
        return "—"
    try:
        return DatabaseType(text).value.upper()
    except ValueError:
        return text.upper()


class DashboardView(BaseView):
    """Premium enterprise analytics dashboard for Database Automation Pro."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        navigate_callback: Callable[[str], None] | None = None,
        employee: EmployeeSession | None = None,
        sidebar_refresh: Callable[[], None] | None = None,
        on_object_properties: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        initial_refresh = kwargs.pop("initial_refresh", True)
        super().__init__(master, theme, log_callback, employee=employee, **kwargs)
        self.monitoring = MonitoringService()
        self.navigate_callback = navigate_callback
        self.sidebar_refresh = sidebar_refresh
        self.on_object_properties = on_object_properties
        self._metric_values: dict[str, ctk.CTkLabel] = {}
        self._metric_secondary: dict[str, ctk.CTkLabel] = {}
        self._poll_job: str | None = None
        self._db_poll_busy = False
        self._db_poll_pending = False
        self._db_poll_token: int | None = None
        self._db_poll_result: tuple[dict, int] | None = None
        self._db_perf_refs: dict = {}
        self._db_samples: deque = deque(maxlen=36)
        self._last_db_metrics: dict = {}
        self._timers_active = True
        self._db_health_rows: ctk.CTkFrame | None = None
        self._infra_summary_lbl: ctk.CTkLabel | None = None
        self._activity_list: ctk.CTkFrame | None = None
        self._jobs_list: ctk.CTkFrame | None = None
        self._backup_labels: dict[str, ctk.CTkLabel] = {}
        self._overview_labels: dict[str, ctk.CTkLabel] = {}
        self._system_status_lbl: ctk.CTkLabel | None = None
        self._conn_status_online: ctk.CTkLabel | None = None
        self._conn_status_offline: ctk.CTkLabel | None = None
        self._conn_status_health: ctk.CTkLabel | None = None
        self._jobs_count_label: ctk.CTkLabel | None = None
        self._hero_refs: dict = {}
        self._perf_range = "24H"
        self._panel_fingerprint: dict[str, str] = {}
        self._build_ui()
        if initial_refresh:
            self.refresh()
        self._schedule_live_poll()

    def on_connection_changed(self) -> None:
        """Re-poll as soon as the app pushes a new session (drop stale offline results)."""
        self._db_poll_pending = True
        if self.connection:
            self._poll_database_metrics()
        else:
            self._db_samples.clear()
            empty = fetch_database_performance(None)
            self._last_db_metrics = empty
            self._apply_db_performance_dashboard(empty)
            self._apply_command_center_panels(empty, force=True)
            self._refresh_key_metrics()
            self._update_status_footer_from_metrics(empty)

    def destroy(self) -> None:
        self._timers_active = False
        self._db_poll_busy = False
        self._db_poll_pending = False
        job = getattr(self, "_poll_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._poll_job = None
        super().destroy()

    def _content_changed(self, key: str, fingerprint: str, *, force: bool = False) -> bool:
        if not force and self._panel_fingerprint.get(key) == fingerprint:
            return False
        self._panel_fingerprint[key] = fingerprint
        return True

    def _nav(self, key: str, **extra) -> None:
        if not self.navigate_callback:
            return
        if extra:
            self.navigate_callback(key, view_extra=extra)
        else:
            self.navigate_callback(key)

    def _open_database_properties(self, name: str) -> None:
        """Show properties in the classic workspace when embedded; otherwise a child window."""
        if self.on_object_properties:
            self.on_object_properties(name)
            return

        from desktop.database_properties_window import open_database_properties_by_name

        app = self.winfo_toplevel()
        theme = self.theme
        active = self.connection
        log_store = getattr(app, "log_store", None)

        def _disconnect() -> None:
            if callable(getattr(app, "clear_connection", None)):
                app.clear_connection()

        def _navigate(key: str) -> None:
            self._nav(key)

        def _connect(connection) -> None:
            if callable(getattr(app, "_establish_connection", None)):
                app._establish_connection(connection, navigate_to=None)

        open_database_properties_by_name(
            app,
            theme,
            name,
            owner_username=self.owner_username,
            active_connection=active,
            log_store=log_store,
            on_disconnect=_disconnect,
            on_navigate=_navigate,
            on_connect=_connect,
        )

    def _nav_kpi(self, key: str) -> None:
        connected_name = ""
        if self.connection:
            connected_name = (self.connection.saved_name or self.connection.name or "").strip()
        server_extra: dict = {"servers_filter": "all", "open_saved": True}
        if connected_name:
            server_extra["saved_server"] = connected_name
        routes = {
            "linux_servers": ("servers", server_extra),
            "databases": ("databases", {}),
            "active_alerts": ("logs", {"log_filter": "alerts"}),
            "failed_today": ("backup", {}),
            "active_jobs": ("logs", {"log_filter": "jobs"}),
            "system_status": ("databases", {}),
        }
        target = routes.get(key)
        if not target:
            return
        page, extra = target
        self._nav(page, **extra)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Single vertical scroll host — avoid nested scrollables that clip content.
        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=self.theme.c("bg_elevated"),
            scrollbar_button_hover_color=self.theme.c("primary"),
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=20, pady=(10, 0))
        scroll.grid_columnconfigure(0, weight=1)
        self._scroll = scroll
        self._build_command_center(scroll)

    def _build_command_center(self, scroll: ctk.CTkScrollableFrame) -> None:
        # Approved order: hero → KPIs → infra+overview → performance → backup+jobs → recent
        self._hero_refs = beacon_hero_header(
            scroll,
            self.theme,
            on_refresh=self.refresh,
            on_quick_action=lambda: self._nav("servers"),
            quick_action_text="Open Servers",
        )
        self._build_kpi_row(scroll)

        self._build_infrastructure_section(scroll)
        self._build_performance_overview(scroll)
        self._build_backup_jobs_row(scroll)
        self._build_recent_operations(scroll)
        # Clear the fixed status footer so the last section is fully reachable.
        ctk.CTkFrame(scroll, fg_color="transparent", height=CONTENT_BOTTOM_INSET).pack(fill="x")

    def _build_kpi_row(self, scroll: ctk.CTkScrollableFrame) -> None:
        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 8))
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1, uniform="kpi4")

        tiles = [
            ("linux_servers", "Registered Databases", "Saved profiles"),
            ("databases", "Connected Databases", "Active session"),
            ("active_alerts", "Active Alerts", "Failed ops today"),
            ("failed_today", "Failed Backups", "Failed today"),
            ("active_jobs", "Running Automation Jobs", "In-flight ops"),
            ("system_status", "System Health", "Live session health"),
        ]
        for i, (key, label, secondary) in enumerate(tiles[:4]):
            card, val, sec = compact_kpi_card(
                grid, self.theme, key=key, label=label, secondary=secondary
            )
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 4, 0), pady=0)
            self._metric_values[key] = val
            self._metric_secondary[key] = sec
            make_clickable(
                card,
                self.theme,
                lambda k=key: self._nav_kpi(k),
                hover_border=self.theme.c("border_strong") if hasattr(self.theme, "c") else self.theme.c("border"),
            )

        for i, (key, label, secondary) in enumerate(tiles[4:]):
            card, val, sec = compact_kpi_card(
                grid, self.theme, key=key, label=label, secondary=secondary
            )
            card.grid(
                row=1,
                column=i * 2,
                columnspan=2,
                sticky="nsew",
                padx=(0 if i == 0 else 4, 0),
                pady=(4, 0),
            )
            self._metric_values[key] = val
            self._metric_secondary[key] = sec
            make_clickable(
                card,
                self.theme,
                lambda k=key: self._nav_kpi(k),
                hover_border=self.theme.c("border"),
            )

    def _build_infrastructure_section(self, parent: ctk.CTkFrame) -> None:
        """Technical Database Infrastructure grid + compact System Status."""
        shell = ctk.CTkFrame(
            parent,
            fg_color=self.theme.c("surface"),
            corner_radius=2,
            border_width=1,
            border_color=self.theme.c("border"),
        )
        shell.pack(fill="x", pady=(0, 8))
        content = ctk.CTkFrame(shell, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=10)

        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            header,
            text="DATABASE INFRASTRUCTURE",
            font=font(self.theme, 11, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(side="left")
        view_all = ctk.CTkLabel(
            header,
            text="View all →",
            font=font(self.theme, 10),
            text_color=self.theme.c("text_secondary"),
            cursor="hand2",
        )
        view_all.pack(side="right")
        view_all.bind("<Button-1>", lambda _e: self._nav("databases"))

        self._infra_summary_lbl = ctk.CTkLabel(
            content,
            text="",
            font=font(self.theme, 10),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        )
        self._infra_summary_lbl.pack(fill="x", pady=(0, 8))

        grid = ctk.CTkFrame(content, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure(0, weight=7)
        grid.grid_columnconfigure(1, weight=0)
        grid.grid_columnconfigure(2, weight=3, minsize=150)

        left = ctk.CTkFrame(grid, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkFrame(grid, width=1, fg_color=self.theme.c("border")).grid(
            row=0, column=1, sticky="ns", padx=6
        )
        right = ctk.CTkFrame(grid, fg_color="transparent")
        right.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        cols = ctk.CTkFrame(left, fg_color=self.theme.c("bg_elevated"), corner_radius=0, height=22)
        cols.pack(fill="x", pady=(0, 2))
        cols.pack_propagate(False)
        for text, width in (
            ("DATABASE", 140),
            ("ENGINE", 88),
            ("HOST", 118),
            ("STATUS", 100),
            ("VERSION", 78),
            ("SESSIONS", 62),
        ):
            ctk.CTkLabel(
                cols,
                text=text,
                width=width,
                anchor="w",
                font=font(self.theme, 9, weight="bold"),
                text_color=self.theme.c("text_muted"),
            ).pack(side="left", padx=(6 if text == "DATABASE" else 4, 0))

        self._db_health_rows = ctk.CTkFrame(left, fg_color="transparent")
        self._db_health_rows.pack(fill="x")

        # Traditional system-information style status panel
        panel = ctk.CTkFrame(
            right,
            fg_color=self.theme.c("bg_elevated"),
            corner_radius=0,
            border_width=1,
            border_color=self.theme.c("border"),
        )
        panel.pack(fill="x", anchor="n")
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(
            inner,
            text="CONNECTION STATUS",
            font=font(self.theme, 10, weight="bold"),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        online_row = ctk.CTkFrame(inner, fg_color="transparent")
        online_row.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            online_row,
            text="●  ONLINE",
            font=font(self.theme, 11, weight="bold"),
            text_color=self.theme.c("success"),
            anchor="w",
        ).pack(side="left")
        self._conn_status_online = ctk.CTkLabel(
            online_row,
            text="0",
            font=font(self.theme, 12, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="e",
            width=28,
        )
        self._conn_status_online.pack(side="right")

        offline_row = ctk.CTkFrame(inner, fg_color="transparent")
        offline_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            offline_row,
            text="○  OFFLINE",
            font=font(self.theme, 11, weight="bold"),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(side="left")
        self._conn_status_offline = ctk.CTkLabel(
            offline_row,
            text="0",
            font=font(self.theme, 12, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="e",
            width=28,
        )
        self._conn_status_offline.pack(side="right")

        ctk.CTkFrame(inner, height=1, fg_color=self.theme.c("border")).pack(fill="x", pady=(0, 8))

        self._conn_status_health = ctk.CTkLabel(
            inner,
            text="○ READY",
            font=font(self.theme, 11, weight="bold"),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        )
        self._conn_status_health.pack(fill="x", pady=(0, 6))
        self._system_status_lbl = self._conn_status_health

        for key, label in [
            ("registered", "Registered"),
            ("connected", "Active"),
            ("alerts", "Alerts"),
        ]:
            block = ctk.CTkFrame(inner, fg_color="transparent")
            block.pack(fill="x", pady=(0, 3))
            ctk.CTkLabel(
                block,
                text=label,
                font=font(self.theme, 10),
                text_color=self.theme.c("text_secondary"),
                anchor="w",
            ).pack(side="left")
            val = ctk.CTkLabel(
                block,
                text="0",
                font=font(self.theme, 11, weight="bold"),
                text_color=self.theme.c("text_primary"),
                anchor="e",
            )
            val.pack(side="right")
            self._overview_labels[key] = val

    def _build_performance_overview(self, parent: ctk.CTkFrame) -> None:
        self._db_perf_refs = build_database_performance_section(
            parent,
            self.theme,
            on_view_all=lambda: self._nav("databases"),
        )
        self._perf_range = "24H"

    def _on_perf_range_change(self, name: str) -> None:
        self._perf_range = name
        self._apply_db_performance_dashboard(self._last_db_metrics or None)

    def _build_backup_jobs_row(self, parent: ctk.CTkFrame) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))
        row.grid_columnconfigure(0, weight=1, uniform="ops2")
        row.grid_columnconfigure(1, weight=1, uniform="ops2")

        # Backup & Recovery — large hero metric
        left = ctk.CTkFrame(
            row,
            fg_color=self.theme.c("surface"),
            corner_radius=2,
            border_width=1,
            border_color=self.theme.c("border"),
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        bak = ctk.CTkFrame(left, fg_color="transparent")
        bak.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(
            bak,
            text="BACKUP & RECOVERY",
            font=font(self.theme, 10, weight="bold"),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w")
        ok_val = ctk.CTkLabel(
            bak,
            text="0",
            font=font(self.theme, 22, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        )
        ok_val.pack(anchor="w", pady=(6, 0))
        self._backup_labels["successful"] = ok_val
        ctk.CTkLabel(
            bak,
            text="Successful today",
            font=font(self.theme, 12),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 12))
        for key, label in [("failed", "Failed"), ("running", "Running")]:
            line = ctk.CTkFrame(bak, fg_color="transparent")
            line.pack(fill="x", pady=4)
            ctk.CTkLabel(
                line,
                text=label,
                font=font(self.theme, 12),
                text_color=self.theme.c("text_secondary"),
                anchor="w",
            ).pack(side="left")
            val = ctk.CTkLabel(
                line,
                text="0",
                font=font(self.theme, 13, weight="bold"),
                text_color=self.theme.c("text_primary"),
                anchor="e",
            )
            val.pack(side="right")
            self._backup_labels[key] = val
        # Keep last/next available for refresh without showing in this compact card
        self._backup_labels["last"] = ctk.CTkLabel(bak, text="")
        self._backup_labels["next"] = ctk.CTkLabel(bak, text="")
        self._backup_labels["last"].pack_forget()
        self._backup_labels["next"].pack_forget()

        # Automation Jobs
        right = ctk.CTkFrame(
            row,
            fg_color=self.theme.c("surface"),
            corner_radius=2,
            border_width=1,
            border_color=self.theme.c("border"),
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        jobs = ctk.CTkFrame(right, fg_color="transparent")
        jobs.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(
            jobs,
            text="AUTOMATION JOBS",
            font=font(self.theme, 10, weight="bold"),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w")
        self._jobs_count_label = ctk.CTkLabel(
            jobs,
            text="0",
            font=font(self.theme, 22, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        )
        self._jobs_count_label.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(
            jobs,
            text="Currently running",
            font=font(self.theme, 11),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        self._jobs_list = ctk.CTkFrame(jobs, fg_color="transparent")
        self._jobs_list.pack(fill="both", expand=True)

    def _build_recent_operations(self, parent: ctk.CTkFrame) -> None:
        shell = ctk.CTkFrame(
            parent,
            fg_color=self.theme.c("surface"),
            corner_radius=2,
            border_width=1,
            border_color=self.theme.c("border"),
        )
        shell.pack(fill="x", pady=(0, 8))
        content = ctk.CTkFrame(shell, fg_color="transparent")
        content.pack(fill="x", padx=12, pady=10)
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            header,
            text="RECENT OPERATIONS",
            font=font(self.theme, 11, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(side="left")
        view_all = ctk.CTkLabel(
            header,
            text="View all →",
            font=font(self.theme, 12),
            text_color=self.theme.c("primary"),
            cursor="hand2",
        )
        view_all.pack(side="right")
        view_all.bind("<Button-1>", lambda _e: self._nav("logs"))
        self._activity_list = ctk.CTkFrame(content, fg_color="transparent")
        self._activity_list.pack(fill="x")

    def _apply_command_center_panels(
        self, db_metrics: dict | None = None, *, force: bool = False
    ) -> None:
        metrics = db_metrics if db_metrics is not None else self._last_db_metrics
        self._apply_db_health_overview(metrics, force=force)
        self._apply_backup_recovery_panel()
        self._apply_automation_jobs_panel(force=force)
        self._apply_recent_operations_panel(force=force)
        self._apply_system_overview()
        self._apply_hero()

    def _is_actively_connected(self, metrics: dict | None = None) -> bool:
        if not self.connection:
            return False
        m = metrics if metrics is not None else (self._last_db_metrics or {})
        if "connected" in m:
            return bool(m.get("connected"))
        return True

    def _connection_health_bucket(self, metrics: dict) -> str:
        status = str(metrics.get("status", "")).lower()
        load = float(metrics.get("load_pct") or 0)
        if load >= 85 or status in {"critical", "error", "down", "offline"}:
            return "critical"
        if load >= 70 or status in {"warning", "degraded"}:
            return "warning"
        return "healthy"

    def _apply_hero(self) -> None:
        refs = self._hero_refs or {}
        now = datetime.now().astimezone()
        if refs.get("datetime"):
            refs["datetime"].configure(text=now.strftime("%A, %d %B %Y · %H:%M"))
        if refs.get("refreshed"):
            refs["refreshed"].configure(text=f"Last refreshed {now.strftime('%H:%M:%S')}")

        saved = load_saved_servers(self.owner_username)
        if not saved and self.owner_username:
            saved = load_saved_servers()
        registered = len(saved)
        connected = 1 if self._is_actively_connected() else 0
        metrics = self._last_db_metrics or {}
        _bak_ok, bak_fail = self.monitoring.count_audit_today(
            actions=("backup",), user=self.owner_username
        )
        _r_ok, restore_fail = self.monitoring.count_audit_today(
            actions=("restore",), user=self.owner_username
        )
        _c_ok, connect_fail = self.monitoring.count_audit_today(
            actions=("connect",), user=self.owner_username
        )
        alerts = bak_fail + restore_fail + connect_fail

        if connected:
            bucket = self._connection_health_bucket(metrics)
            if bucket == "critical":
                headline = "Your database infrastructure needs attention."
                pulse_color = self.theme.c("error")
                pulse_text = "● CRITICAL"
            elif bucket == "warning" or alerts:
                headline = "Your database infrastructure has warnings."
                pulse_color = self.theme.c("warning")
                pulse_text = "● DEGRADED"
            else:
                headline = "Your database infrastructure is looking healthy."
                pulse_color = self.theme.c("success")
                pulse_text = "● LIVE"
        elif registered:
            headline = "Registered databases are ready to connect."
            pulse_color = self.theme.c("text_muted")
            pulse_text = "● IDLE"
        else:
            headline = "Welcome to Database Automation Pro."
            pulse_color = self.theme.c("info")
            pulse_text = "● READY"

        if refs.get("headline"):
            refs["headline"].configure(text=headline)
        if refs.get("summary"):
            refs["summary"].configure(
                text=(
                    f"{registered} registered databases · {connected} active connection"
                    f"{'' if connected == 1 else 's'} · {alerts} critical alerts"
                )
            )
        if refs.get("pulse"):
            refs["pulse"].configure(text=pulse_text, text_color=pulse_color)

    def _status_pill(self, parent, text: str, tone: str) -> ctk.CTkLabel:
        palette = {
            "success": (self.theme.c("success_muted"), self.theme.c("success")),
            "warning": (self.theme.c("warning_muted"), self.theme.c("warning")),
            "error": (self.theme.c("error_muted"), self.theme.c("error")),
            "neutral": (self.theme.c("bg_elevated"), self.theme.c("text_secondary")),
        }
        bg, fg = palette.get(tone, palette["neutral"])
        return ctk.CTkLabel(
            parent,
            text=f"  {text}  ",
            font=font(self.theme, 11, weight="bold"),
            text_color=fg,
            fg_color=bg,
            corner_radius=12,
            height=24,
        )

    def _apply_system_overview(self) -> None:
        saved = load_saved_servers(self.owner_username)
        if not saved and self.owner_username:
            saved = load_saved_servers()
        registered = len(saved)
        connected = 1 if self._is_actively_connected() else 0
        offline = max(registered - connected, 0)
        _bak_ok, bak_fail = self.monitoring.count_audit_today(
            actions=("backup",), user=self.owner_username
        )
        _r_ok, restore_fail = self.monitoring.count_audit_today(
            actions=("restore",), user=self.owner_username
        )
        _c_ok, connect_fail = self.monitoring.count_audit_today(
            actions=("connect",), user=self.owner_username
        )
        alerts = bak_fail + restore_fail + connect_fail

        if self._conn_status_online is not None:
            self._conn_status_online.configure(text=str(connected))
        if self._conn_status_offline is not None:
            self._conn_status_offline.configure(text=str(offline))

        mapping = {
            "registered": (str(registered), self.theme.c("text_primary")),
            "connected": (str(connected), self.theme.c("text_primary")),
            "alerts": (
                str(alerts),
                self.theme.c("error") if alerts else self.theme.c("text_primary"),
            ),
        }
        for key, (text, color) in mapping.items():
            lbl = self._overview_labels.get(key)
            if lbl:
                lbl.configure(text=text, text_color=color)

        if self._system_status_lbl is not None:
            if connected:
                bucket = self._connection_health_bucket(self._last_db_metrics or {})
                if bucket == "critical":
                    self._system_status_lbl.configure(
                        text="● CRITICAL", text_color=self.theme.c("error")
                    )
                elif bucket == "warning" or alerts:
                    self._system_status_lbl.configure(
                        text="● DEGRADED", text_color=self.theme.c("warning")
                    )
                else:
                    self._system_status_lbl.configure(
                        text="● HEALTHY", text_color=self.theme.c("success")
                    )
            elif registered:
                self._system_status_lbl.configure(
                    text="○ READY", text_color=self.theme.c("text_secondary")
                )
            else:
                self._system_status_lbl.configure(
                    text="○ IDLE", text_color=self.theme.c("text_muted")
                )

    def _apply_db_health_overview(self, metrics: dict, *, force: bool = False) -> None:
        saved = load_saved_servers(self.owner_username)
        if not saved and self.owner_username:
            saved = load_saved_servers()
        connected = self._is_actively_connected(metrics)

        healthy = warning = critical = 0
        if connected:
            bucket = self._connection_health_bucket(metrics)
            if bucket == "critical":
                critical = 1
            elif bucket == "warning":
                warning = 1
            else:
                healthy = 1

        if not self._db_health_rows:
            return

        version = str((metrics or {}).get("version") or "").strip() or "—"
        sessions = metrics.get("sessions")
        session_txt = str(sessions) if sessions not in (None, "", "—") else "—"
        # Rows: name, engine, host, status_text, tone, version, sessions
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        active_saved = ""
        online_count = 0
        if self.connection:
            active_saved = (self.connection.saved_name or self.connection.name or "").strip()
            name = self.connection.saved_name or self.connection.name
            engine_title = _engine_label(self.connection.db_type)
            host = f"{self.connection.host}:{self.connection.port}" if self.connection.host else "—"
            if critical:
                rows.append((name, engine_title, host, "● CRITICAL", "error", version, session_txt))
                online_count = 1
            elif warning:
                rows.append((name, engine_title, host, "⚠ WARNING", "warning", version, session_txt))
                online_count = 1
            elif connected:
                rows.append((name, engine_title, host, "● ONLINE", "success", version, session_txt))
                online_count = 1
            else:
                rows.append((name, engine_title, host, "○ OFFLINE", "error", version, "—"))

        offline_rows: list[tuple[str, str, str, str, str, str, str]] = []
        for server in saved:
            if active_saved and server.name == active_saved:
                continue
            host = f"{server.host}:{server.port}" if server.host else "—"
            offline_rows.append(
                (
                    server.name,
                    _engine_label(server.db_type),
                    host,
                    "○ OFFLINE",
                    "neutral",
                    "—",
                    "—",
                )
            )
        offline_rows.sort(key=lambda r: r[0].lower())
        rows.extend(offline_rows)

        if self._infra_summary_lbl is not None:
            total = len(rows)
            self._infra_summary_lbl.configure(
                text=f"{total} registered · {online_count} online"
            )

        show_empty = not rows
        fingerprint = f"grid|{show_empty}|{rows[:20]}"
        if not self._content_changed("health_rows", fingerprint, force=force):
            return

        for child in self._db_health_rows.winfo_children():
            child.destroy()

        if show_empty:
            empty_state(
                self._db_health_rows,
                self.theme,
                icon="database",
                title="No databases registered",
                message="Save a connection on Servers, then connect to monitor health.",
                action_text="+ Connect Database",
                action_command=lambda: self._nav("servers"),
                tone="info",
            )
            return

        tone_colors = {
            "success": self.theme.c("success"),
            "warning": self.theme.c("warning"),
            "error": self.theme.c("error"),
            "neutral": self.theme.c("text_muted"),
        }
        for idx, (name, engine, host, status, tone, ver, sess) in enumerate(rows):
            bg = self.theme.c("bg_elevated") if idx % 2 else "transparent"
            line = ctk.CTkFrame(self._db_health_rows, fg_color=bg, corner_radius=0, height=24)
            line.pack(fill="x")
            line.pack_propagate(False)
            line.configure(cursor="hand2")
            line.bind("<Button-1>", lambda _e, n=name: self._open_database_properties(n))
            cells = (
                (name, 140, self.theme.c("text_primary"), True),
                (engine, 88, self.theme.c("text_secondary"), False),
                (host, 118, self.theme.c("text_secondary"), False),
                (status, 100, tone_colors.get(tone, self.theme.c("text_muted")), True),
                (ver, 78, self.theme.c("text_secondary"), False),
                (sess, 62, self.theme.c("text_secondary"), False),
            )
            for text, width, color, bold in cells:
                lbl = ctk.CTkLabel(
                    line,
                    text=text,
                    width=width,
                    anchor="w",
                    font=font(self.theme, 10, weight="bold" if bold else "normal"),
                    text_color=color,
                    cursor="hand2",
                )
                lbl.pack(side="left", padx=(6 if text == name else 4, 0))
                lbl.bind("<Button-1>", lambda _e, n=name: self._open_database_properties(n))

    def _format_last_backup(self) -> str:
        try:
            _ok, _fail, last_success = self.monitoring._backup_audit_stats()
        except Exception:
            return "—"
        if not last_success:
            return "—"
        try:
            return last_success.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "—"

    def _format_next_backup(self) -> str:
        try:
            from backend.services.scheduler_service import SchedulerService

            jobs = SchedulerService().list_jobs()
        except Exception:
            return "Not configured"
        next_runs = [j.get("next_run") for j in jobs if j.get("next_run") and j.get("next_run") != "None"]
        if not next_runs:
            return "Not configured"
        raw = str(next_runs[0])
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return raw[:16] if len(raw) > 16 else raw

    def _count_running_backups(self) -> int:
        try:
            jobs = self.monitoring.list_live_jobs()
        except Exception:
            return 0
        return sum(
            1
            for j in jobs
            if str(j.get("operation", "")).lower() == "backup"
            and str(j.get("status", "")).lower() in {"running", "pending"}
        )

    @staticmethod
    def _format_job_duration(started_at: str | None) -> str:
        if not started_at:
            return "—"
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            seconds = max(0, int((datetime.now(started.tzinfo) - started).total_seconds()))
        except Exception:
            return "—"
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {sec}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

    def _apply_backup_recovery_panel(self) -> None:
        try:
            ok, failed = self.monitoring.count_audit_today(
                actions=("backup",),
                user=self.owner_username,
            )
        except Exception:
            ok, failed = 0, 0
        running = self._count_running_backups()
        mapping = {
            "successful": str(ok),
            "failed": str(failed),
            "running": str(running),
            "last": self._format_last_backup(),
            "next": self._format_next_backup(),
        }
        for key, text in mapping.items():
            lbl = self._backup_labels.get(key)
            if not lbl:
                continue
            color = self.theme.c("text_primary")
            if key == "failed" and failed > 0:
                color = self.theme.c("error")
            elif key == "successful" and ok > 0:
                color = self.theme.c("success")
            elif key == "running" and running > 0:
                color = self.theme.c("warning")
            lbl.configure(text=text, text_color=color)

    def _apply_automation_jobs_panel(self, *, force: bool = False) -> None:
        if not self._jobs_list:
            return
        try:
            jobs = self.monitoring.list_live_jobs()
        except Exception:
            jobs = []
        fingerprint = repr(
            [
                (
                    j.get("id"),
                    j.get("status"),
                    j.get("name"),
                    j.get("progress"),
                    j.get("target"),
                    j.get("started_at"),
                )
                for j in jobs
            ]
        ) or "empty"
        if not self._content_changed("jobs", fingerprint, force=force):
            return

        for child in self._jobs_list.winfo_children():
            child.destroy()
        if self._jobs_count_label:
            self._jobs_count_label.configure(text=str(len(jobs)))

        if not jobs:
            ctk.CTkLabel(
                self._jobs_list,
                text="No active jobs currently running.",
                font=font(self.theme, 12),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w")
            return

        for job in jobs[:4]:
            line = ctk.CTkFrame(self._jobs_list, fg_color="transparent")
            line.pack(fill="x", pady=3)
            name = str(job.get("name") or job.get("operation") or "Job")
            if len(name) > 36:
                name = name[:33] + "…"
            ctk.CTkLabel(
                line,
                text=name,
                font=font(self.theme, 12),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            progress = job.get("progress")
            if progress is not None:
                try:
                    right_text = f"{int(float(progress) * 100)}%"
                except (TypeError, ValueError):
                    right_text = str(job.get("status") or "").title()
            else:
                right_text = str(job.get("status") or "Running").title()
            ctk.CTkLabel(
                line,
                text=right_text,
                font=font(self.theme, 11, weight="bold"),
                text_color=self.theme.c("primary"),
                anchor="e",
            ).pack(side="right")

    def _friendly_operation_title(self, action: str, status: str) -> str:
        action_l = action.lower().replace("_", " ").strip()
        status_l = status.lower()
        mapping = {
            ("backup", "success"): "Backup completed",
            ("backup", "failed"): "Backup failed",
            ("restore", "success"): "Restore completed",
            ("restore", "failed"): "Restore failed",
            ("connect", "success"): "Database connection established",
            ("connect", "failed"): "Database connection failed",
            ("migration", "success"): "Migration executed",
            ("migration", "failed"): "Migration failed",
            ("patch", "success"): "Patching executed",
            ("patch_execute", "success"): "Patching executed",
            ("patch", "failed"): "Patching failed",
        }
        for (act, st), title in mapping.items():
            if action_l.startswith(act) and status_l == st:
                return title
        return f"{action_l.title()} {status_l}"

    def _apply_recent_operations_panel(self, *, force: bool = False) -> None:
        if not self._activity_list:
            return
        try:
            events = self.monitoring.get_recent_activity(limit=5)
        except Exception:
            events = []
        fingerprint = repr(
            [(e.get("time"), e.get("action"), e.get("status"), e.get("resource"), e.get("user")) for e in events]
        ) or "empty"
        if not self._content_changed("activity", fingerprint, force=force):
            return

        for child in self._activity_list.winfo_children():
            child.destroy()
        if not events:
            ctk.CTkLabel(
                self._activity_list,
                text="No recent operations yet.",
                font=font(self.theme, 12),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w")
            return
        for event in events:
            status = str(event.get("status", "info")).lower()
            if status == "success":
                tone = "success"
            elif status == "failed":
                tone = "error"
            elif status in ("warning", "warn"):
                tone = "warning"
            else:
                tone = "info"
            action = str(event.get("action", "event"))
            title = self._friendly_operation_title(action, status)
            row = ctk.CTkFrame(self._activity_list, fg_color="transparent")
            row.pack(fill="x", pady=6)
            badge = lucide_icon_badge(
                row,
                "activity",
                self.theme.c("success") if tone == "success" else self.theme.c(
                    "error" if tone == "error" else "primary"
                ),
                self.theme.c("bg_elevated"),
                box=28,
                icon_size=14,
                radius=8,
            )
            badge.pack(side="left")
            ctk.CTkLabel(
                row,
                text=title,
                font=font(self.theme, 13),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left", padx=(10, 0), fill="x", expand=True)
            link = ctk.CTkLabel(
                row,
                text="Activity log",
                font=font(self.theme, 12),
                text_color=self.theme.c("text_secondary"),
                cursor="hand2",
            )
            link.pack(side="right")
            link.bind("<Button-1>", lambda _e: self._nav("logs"))
            row.bind("<Button-1>", lambda _e: self._nav("logs"))

    def _apply_db_performance_dashboard(self, metrics: dict | None = None) -> None:
        if not self._db_perf_refs:
            return
        if metrics is None:
            metrics = fetch_database_performance(self.connection)

        refs = self._db_perf_refs
        connected = bool(metrics.get("connected"))
        update_chart = refs.get("update_perf_chart")
        sparks = refs.get("sparklines") or {}

        conn_n = int(metrics.get("connections_num") or 0)
        latency_lbl = sparks.get("latency")
        if latency_lbl:
            if not connected:
                latency_lbl.configure(text="—")
            else:
                lat_ms = metrics.get("latency_ms")
                latency_lbl.configure(
                    text=f"{lat_ms:.0f} ms" if lat_ms is not None else "N/A"
                )
        connections_lbl = sparks.get("connections")
        if connections_lbl:
            connections_lbl.configure(text="—" if not connected else str(conn_n))
        slow_lbl = sparks.get("slow")
        if slow_lbl:
            if not connected or metrics.get("slow_num") is None:
                slow_lbl.configure(text="N/A" if connected else "—")
            else:
                slow_lbl.configure(text=str(metrics.get("slow_num")))
        throughput_lbl = sparks.get("throughput")
        if throughput_lbl:
            if not connected:
                throughput_lbl.configure(text="—")
            else:
                tps = metrics.get("throughput")
                label = str(metrics.get("throughput_label") or "").lower()
                if tps is None or (
                    "transaction" not in label and float(tps or 0) <= 0
                ):
                    throughput_lbl.configure(text="N/A")
                else:
                    throughput_lbl.configure(text=f"{float(tps):.0f}")

        if connected:
            self._db_samples.append({
                "label": datetime.now().strftime("%H:%M"),
                "latency": float(metrics.get("latency_ms") or 0),
                "connections": int(metrics.get("connections_num") or 0),
                "slow": int(metrics.get("slow_num") or 0),
                "throughput": float(metrics.get("throughput") or 0),
            })

        if not update_chart:
            return
        # Only plot real live samples — never invent a decorative series.
        if connected and len(self._db_samples) >= 2:
            update_chart(list(self._db_samples), message="")
        else:
            update_chart(
                [],
                message="Illustrative chart only – actual telemetry required.",
            )

    def _refresh_key_metrics(self) -> None:
        try:
            saved = load_saved_servers(self.owner_username)
            if not saved and self.owner_username:
                saved = load_saved_servers()
            saved_count = len(saved)

            active_name = ""
            active_engine = ""
            if self.connection:
                active_name = (self.connection.saved_name or self.connection.name or "").strip()
                active_engine = self.connection.db_type.value.upper()

            connected_count = 1 if self._is_actively_connected() else 0
            metrics = self._last_db_metrics or {}

            active_jobs, pending_jobs = self.monitoring.count_live_jobs()
            _bak_ok, bak_fail = self.monitoring.count_audit_today(
                actions=("backup",), user=self.owner_username,
            )
            _restore_ok, restore_fail = self.monitoring.count_audit_today(
                actions=("restore",), user=self.owner_username,
            )
            _connect_ok, connect_fail = self.monitoring.count_audit_today(
                actions=("connect",), user=self.owner_username,
            )
            alerts_n = bak_fail + restore_fail + connect_fail

            if connected_count:
                bucket = self._connection_health_bucket(metrics)
                if bucket == "critical":
                    system_status, system_secondary = "Critical", "Session needs attention"
                elif bucket == "warning":
                    system_status, system_secondary = "Warning", "Elevated load or degraded"
                else:
                    system_status, system_secondary = "Healthy", "Live session OK"
            else:
                system_status, system_secondary = "Offline", "No active connection"

            for key, value in {
                "linux_servers": str(saved_count),
                "databases": str(connected_count),
                "active_alerts": str(alerts_n),
                "failed_today": str(bak_fail),
                "active_jobs": str(active_jobs),
                "system_status": system_status,
            }.items():
                if key in self._metric_values:
                    color = self.theme.c("text_primary")
                    if key == "active_alerts" and alerts_n:
                        color = self.theme.c("error")
                    elif key == "failed_today" and bak_fail:
                        color = self.theme.c("error")
                    elif key == "system_status":
                        if system_status == "Healthy":
                            color = self.theme.c("success")
                        elif system_status == "Warning":
                            color = self.theme.c("warning")
                        elif system_status == "Critical":
                            color = self.theme.c("error")
                    self._metric_values[key].configure(text=value, text_color=color)

            if active_name:
                registered_secondary = f"Active: {active_name} · {active_engine}"
            elif saved_count:
                registered_secondary = f"{saved_count} registered · none active"
            else:
                registered_secondary = "No saved profiles"

            if saved_count:
                databases_secondary = f"{connected_count} of {saved_count} registered"
            else:
                databases_secondary = "Connected" if connected_count else "Not connected"

            sec_map = {
                "linux_servers": registered_secondary,
                "databases": databases_secondary,
                "active_alerts": "Needs attention" if alerts_n else "All clear",
                "failed_today": f"{_bak_ok} succeeded today",
                "active_jobs": f"{pending_jobs} queued" if pending_jobs else "None running",
                "system_status": system_secondary,
            }
            for key, text in sec_map.items():
                if key in self._metric_secondary:
                    self._metric_secondary[key].configure(text=text)
        except Exception:
            pass

    def _poll_database_metrics(self) -> None:
        if not self.winfo_exists():
            return
        if self._db_poll_busy:
            # A connect/disconnect arrived while a poll was in flight — rerun after.
            self._db_poll_pending = True
            return
        connection = self.connection
        token = id(connection) if connection is not None else 0
        self._db_poll_busy = True
        self._db_poll_pending = False
        self._db_poll_token = token

        def worker() -> None:
            try:
                metrics = fetch_database_performance(connection)
            except Exception:
                metrics = {"connected": False}
            try:
                self.after(0, lambda m=metrics, t=token: self._finish_db_poll(m, t))
            except RuntimeError:
                # Tk rejects after() off the main loop — never leave the poll latch stuck.
                self._db_poll_busy = False
                self._db_poll_result = (metrics, token)

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._drain_db_poll_result)

    def _drain_db_poll_result(self) -> None:
        """Apply metrics posted when a worker could not schedule after() itself."""
        if not self.winfo_exists():
            return
        result = self._db_poll_result
        if result is not None:
            self._db_poll_result = None
            metrics, token = result
            self._finish_db_poll(metrics, token)
            return
        if self._db_poll_busy:
            self.after(100, self._drain_db_poll_result)

    def _finish_db_poll(self, metrics: dict, token: int | None = None) -> None:
        self._db_poll_busy = False
        if not self._timers_active or not self.winfo_exists():
            return
        current = id(self.connection) if self.connection is not None else 0
        stale = token is not None and token != current
        if stale or self._db_poll_pending:
            self._db_poll_pending = False
            self._poll_database_metrics()
            if stale:
                return
        self._last_db_metrics = metrics
        self._apply_db_performance_dashboard(metrics)
        self._apply_command_center_panels(metrics)
        self._refresh_key_metrics()
        self._update_status_footer_from_metrics(metrics)

    def _update_status_footer_from_metrics(self, metrics: dict) -> None:
        updater = None
        widget = self.winfo_toplevel()
        if callable(getattr(widget, "update_status_footer", None)):
            updater = widget.update_status_footer
        if not callable(updater):
            return
        if self.connection:
            name = self.connection.saved_name or self.connection.name
            conn_status = f"● Connected · {name}"
        else:
            conn_status = "● Disconnected"

        version = str(metrics.get("version") or "").strip() or "—"
        uptime = str(metrics.get("uptime") or "—")
        if metrics.get("connected"):
            sessions = str(int(metrics.get("connections_num") or 0))
        else:
            sessions = "—"

        updater(
            connection_status=conn_status,
            version=version,
            uptime=uptime,
            sessions=sessions,
        )

    def _dashboard_visible(self) -> bool:
        try:
            return bool(self._timers_active and self.winfo_ismapped())
        except Exception:
            return False

    def _request_live_poll(self) -> None:
        if not self._timers_active or not self.winfo_exists():
            return
        if self.connection:
            self._poll_database_metrics()
        else:
            self._apply_command_center_panels()
            self._refresh_key_metrics()
            empty = fetch_database_performance(None)
            self._last_db_metrics = empty
            self._apply_db_performance_dashboard(empty)
            self._update_status_footer_from_metrics(empty)

    def _schedule_live_poll(self) -> None:
        if not self._timers_active or not self.winfo_exists():
            return
        if self._dashboard_visible():
            self._request_live_poll()
        self._poll_job = self.after(10000, self._schedule_live_poll)

    def resume(self) -> None:
        self._request_live_poll()

    def refresh(self, *, force: bool = False) -> None:
        del force
        try:
            self._refresh_key_metrics()

            if not self.connection:
                self._db_samples.clear()
                empty = fetch_database_performance(None)
                self._last_db_metrics = empty
                self._apply_db_performance_dashboard(empty)
                self._update_status_footer_from_metrics(empty)

            self._apply_command_center_panels(force=True)

            if self.sidebar_refresh:
                self.sidebar_refresh()

            if self.connection:
                self._poll_database_metrics()

            self.log("Dashboard refreshed", "SUCCESS")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
        finally:
            root = self.winfo_toplevel()
            if callable(getattr(root, "mark_last_updated", None)):
                root.mark_last_updated()
