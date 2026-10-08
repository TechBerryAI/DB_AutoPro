"""Enterprise Database Operations Command Center dashboard."""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService
from desktop.connection_store import load_saved_servers
from desktop.db_catalog import fetch_server_databases
from desktop.db_performance import fetch_database_performance
from desktop.dashboard_widgets import host_stat_row, section_block
from desktop.db_performance_widgets import build_database_performance_section
from desktop.design_system import font, status_chip
from desktop.employee_store import EmployeeSession
from desktop.ui_helpers import detail_panel, make_clickable
from desktop.infrastructure_health import InfrastructureHealthPanel
from desktop.ops_widgets import (
    KPI_ICONS,
    activity_item,
    alert_item_compact,
    command_center_header,
    compact_kpi_card,
    compact_section,
    empty_state,
    health_overview_visual,
    panel_card,
    resource_progress_row,
    threshold_color,
)
from desktop.security_widgets import build_security_section, security_alert_item, top_risk_row
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _role_label(role) -> str:
    labels = {"admin": "Administrator", "operator": "Operator", "viewer": "Viewer"}
    return labels.get(role.value, role.value.title())


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name[:2] if name else "U").upper()


def _format_gb(value_gb: float) -> str:
    if value_gb >= 100:
        return f"{value_gb:.0f} GB"
    return f"{value_gb:.1f} GB"


def _gauge_color(theme: ThemeManager, pct: float, base: str) -> str:
    _ = base
    return threshold_color(theme, pct)


class DashboardView(BaseView):
    """Enterprise Database Operations Command Center."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        navigate_callback: Callable[[str], None] | None = None,
        employee: EmployeeSession | None = None,
        sidebar_refresh: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        initial_refresh = kwargs.pop("initial_refresh", True)
        super().__init__(master, theme, log_callback, employee=employee, **kwargs)
        self.monitoring = MonitoringService()
        self.navigate_callback = navigate_callback
        self.sidebar_refresh = sidebar_refresh
        self._metric_values: dict[str, ctk.CTkLabel] = {}
        self._metric_secondary: dict[str, ctk.CTkLabel] = {}
        self._gauges: dict[str, tuple[ctk.CTkProgressBar, ctk.CTkLabel, ctk.CTkLabel]] = {}
        self._host_labels: dict[str, ctk.CTkLabel] = {}
        self._host_status_chip: ctk.CTkLabel | None = None
        self._poll_job: str | None = None
        self._db_poll_busy = False
        self._sys_poll_busy = False
        self._db_perf_refs: dict = {}
        self._db_samples: deque = deque(maxlen=24)
        self._last_sys_metrics: dict = {}
        self._security_refs: dict = {}
        self._health_panel: InfrastructureHealthPanel | None = None
        self._last_db_metrics: dict = {}
        self._timers_active = True
        self._db_health_counts: dict[str, ctk.CTkLabel] = {}
        self._db_health_rows: ctk.CTkFrame | None = None
        self._health_visual_host: ctk.CTkFrame | None = None
        self._health_update: Callable[[dict[str, int]], None] | None = None
        self._alerts_list: ctk.CTkFrame | None = None
        self._activity_list: ctk.CTkFrame | None = None
        self._jobs_list: ctk.CTkFrame | None = None
        self._backup_labels: dict[str, ctk.CTkLabel] = {}
        self._activity_metric_labels: dict[str, ctk.CTkLabel] = {}
        self._conn_limit_label: ctk.CTkLabel | None = None
        self._panel_fingerprint: dict[str, str] = {}
        self._db_list_cache: tuple[float, list] | None = None
        self._build_ui()
        if initial_refresh:
            self.refresh()
        self._schedule_system_poll()

    def destroy(self) -> None:
        self._timers_active = False
        self._db_poll_busy = False
        self._sys_poll_busy = False
        if self._health_panel is not None:
            try:
                self._health_panel.destroy()
            except Exception:
                pass
            self._health_panel = None
        job = getattr(self, "_poll_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._poll_job = None
        super().destroy()

    def _content_changed(self, key: str, fingerprint: str, *, force: bool = False) -> bool:
        """Return True when panel widgets must be rebuilt (content actually changed)."""
        if not force and self._panel_fingerprint.get(key) == fingerprint:
            return False
        self._panel_fingerprint[key] = fingerprint
        return True

    def _cached_server_databases(self) -> list:
        """Last inventory fetched off the UI thread. Never opens a connection here."""
        if self._db_list_cache:
            return self._db_list_cache[1]
        return []

    def _nav(self, key: str, **extra) -> None:
        """Same path as sidebar navigation so the active nav item highlights."""
        if not self.navigate_callback:
            return
        if extra:
            self.navigate_callback(key, view_extra=extra)
        else:
            self.navigate_callback(key)

    def _nav_kpi(self, key: str) -> None:
        """Stat cards deep-link into workspace pages (not detail dialogs)."""
        routes = {
            "linux_servers": ("servers", {}),
            "databases": ("databases", {}),
            "healthy_dbs": ("databases", {}),
            "active_jobs": ("logs", {"log_filter": "jobs"}),
            "active_alerts": ("logs", {"log_filter": "alerts"}),
            "failed_today": ("backup", {}),
        }
        target = routes.get(key)
        if not target:
            return
        page, extra = target
        self._nav(page, **extra)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=self.theme.c("bg_elevated"),
            scrollbar_button_hover_color=self.theme.c("primary"),
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(12, 0))
        scroll.grid_columnconfigure(0, weight=1)
        self._build_command_center(scroll)

    def _build_command_center(self, scroll: ctk.CTkScrollableFrame) -> None:
        name = self.employee.full_name if self.employee else "User"
        role = _role_label(self.employee.role) if self.employee else "Operator"

        command_center_header(
            scroll,
            self.theme,
            greeting=f"{_greeting()}, {name}",
            role=role,
            initials=_initials(name),
            on_refresh=self.refresh,
        )

        self._build_kpi_row(scroll)
        self._build_health_alerts_row(scroll)
        self._build_infra_activity_row(scroll)
        self._build_backup_jobs_row(scroll)
        self._build_recent_activity(scroll)
        self._build_legacy_detail_sections(scroll)

    def _build_kpi_row(self, scroll: ctk.CTkScrollableFrame) -> None:
        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 16))
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="kpi3")

        tiles = [
            ("linux_servers", "Saved Servers", "No active connection"),
            ("databases", "Connected Databases", "Active connections"),
            ("healthy_dbs", "Healthy", "Passing health checks"),
            ("active_jobs", "Active Jobs", "Running automation"),
            ("active_alerts", "Active Alerts", "Requires attention"),
            ("failed_today", "Failed Backups", "Failed today"),
        ]
        for i, (key, label, secondary) in enumerate(tiles):
            r, c = divmod(i, 3)
            card, val, sec = compact_kpi_card(
                grid,
                self.theme,
                key=key,
                label=label,
                secondary=secondary,
            )
            card.grid(
                row=r,
                column=c,
                sticky="nsew",
                padx=(0 if c == 0 else 6, 0),
                pady=(0 if r == 0 else 6, 0),
            )
            self._metric_values[key] = val
            self._metric_secondary[key] = sec
            make_clickable(
                card,
                self.theme,
                lambda k=key: self._nav_kpi(k),
                hover_border=self.theme.c("primary"),
            )

    # ------------------------------------------------------------------
    # KPI card -> detail dialog
    # ------------------------------------------------------------------

    def _metric_detail_rows(self, key: str) -> list[tuple[str, str]]:
        """Real data backing a KPI card, as (label, value) rows."""
        try:
            if key == "linux_servers":
                saved = load_saved_servers(self.owner_username) or load_saved_servers()
                return [
                    (s.name, f"{s.db_type} · {s.host}:{s.port}")
                    for s in sorted(saved, key=lambda x: x.name.lower())
                ]

            if key in ("databases", "healthy_dbs"):
                saved = load_saved_servers(self.owner_username) or load_saved_servers()
                active = ""
                if self._is_actively_connected():
                    active = (self.connection.saved_name or self.connection.name or "").strip()
                out: list[tuple[str, str]] = []
                for s in sorted(saved, key=lambda x: x.name.lower()):
                    if active and s.name == active:
                        bucket = self._connection_health_bucket(self._last_db_metrics or {})
                        out.append((s.name, f"Connected · {bucket.title()}"))
                    else:
                        out.append((s.name, "Registered"))
                if self._is_actively_connected() and active and not any(
                    s.name == active for s in saved
                ):
                    bucket = self._connection_health_bucket(self._last_db_metrics or {})
                    out.insert(0, (active, f"Connected · {bucket.title()}"))
                return out

            if key == "active_jobs":
                active, pending = self.monitoring.count_live_jobs()
                return [("Running now", str(active)), ("Pending", str(pending))]

            if key == "active_alerts":
                sec = self.monitoring.get_security_dashboard_data(
                    user=self.owner_username, days=1
                )
                posture = sec.get("posture") or {}
                return [
                    (str(k).replace("_", " ").title(), str(v))
                    for k, v in posture.items()
                ]

            if key == "failed_today":
                ctx = self._backup_stats_context()
                db_type = ctx.get("db_type")
                ok, failed = self.monitoring.count_audit_today(
                    actions=("backup", "restore"),
                    db_type=db_type,
                    user=self.owner_username,
                )
                c_ok, c_failed = self.monitoring.count_audit_today(
                    actions=("connect",), user=self.owner_username
                )
                return [
                    ("Backup / restore failed", str(failed)),
                    ("Backup / restore succeeded", str(ok)),
                    ("Connection failures", str(c_failed)),
                    ("Connections succeeded", str(c_ok)),
                ]
        except Exception as exc:  # detail view must never break the dashboard
            return [("Could not load details", str(exc))]
        return []

    def _open_metric_detail(self, key: str, label: str) -> None:
        value_lbl = self._metric_values.get(key)
        sec_lbl = self._metric_secondary.get(key)
        value_text = value_lbl.cget("text") if value_lbl is not None else "—"
        secondary_text = sec_lbl.cget("text") if sec_lbl is not None else ""
        accent = self.theme.c(KPI_ICONS.get(key, ("", "primary", ""))[1])
        rows = self._metric_detail_rows(key)

        def body(container: ctk.CTkFrame) -> None:
            head = ctk.CTkFrame(container, fg_color="transparent")
            head.pack(fill="x", padx=8, pady=(4, 12))
            ctk.CTkLabel(
                head,
                text=value_text,
                font=font(self.theme, 34, weight="bold"),
                text_color=accent,
                anchor="w",
            ).pack(anchor="w")
            if secondary_text:
                ctk.CTkLabel(
                    head,
                    text=secondary_text,
                    font=font(self.theme, 12),
                    text_color=self.theme.c("text_muted"),
                    anchor="w",
                ).pack(anchor="w", pady=(2, 0))

            if not rows:
                ctk.CTkLabel(
                    container,
                    text="Nothing to show yet.",
                    font=font(self.theme, 13),
                    text_color=self.theme.c("text_muted"),
                ).pack(anchor="w", padx=8, pady=(4, 8))
                return

            for name, detail in rows:
                row = ctk.CTkFrame(
                    container,
                    fg_color=self.theme.c("bg_elevated"),
                    corner_radius=self.theme.radius("sm"),
                )
                row.pack(fill="x", padx=8, pady=3)
                inner = ctk.CTkFrame(row, fg_color="transparent")
                inner.pack(fill="x", padx=12, pady=9)
                ctk.CTkLabel(
                    inner,
                    text=str(detail),
                    font=font(self.theme, 12),
                    text_color=self.theme.c("text_secondary"),
                    anchor="e",
                ).pack(side="right", padx=(12, 0))
                ctk.CTkLabel(
                    inner,
                    text=str(name),
                    font=font(self.theme, 13, weight="bold"),
                    text_color=self.theme.c("text_primary"),
                    anchor="w",
                ).pack(side="left", fill="x", expand=True)

        detail_panel(
            self,
            self.theme,
            title=label,
            subtitle="Live detail from your current environment",
            body=body,
            width=560,
            accent=accent,
        )

    def _build_health_alerts_row(self, scroll: ctk.CTkScrollableFrame) -> None:
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=(0, 20))
        row.grid_columnconfigure(0, weight=65)
        row.grid_columnconfigure(1, weight=35)

        left_wrap = ctk.CTkFrame(row, fg_color="transparent")
        left_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_body = compact_section(
            left_wrap,
            self.theme,
            "Database Health Overview",
            subtitle="Operational status across infrastructure",
            action_text="View databases →",
            action_command=lambda: self._nav("databases"),
        )
        health_shell, health_content = panel_card(left_body, self.theme)
        health_shell.pack(fill="both", expand=True)

        self._health_visual_host = ctk.CTkFrame(health_content, fg_color="transparent")
        self._health_visual_host.pack(fill="x")
        self._db_health_counts, self._health_update = health_overview_visual(
            self._health_visual_host,
            self.theme,
        )

        self._db_health_rows = ctk.CTkFrame(health_content, fg_color="transparent")
        self._db_health_rows.pack(fill="x", pady=(12, 0))

        right_wrap = ctk.CTkFrame(row, fg_color="transparent")
        right_wrap.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_body = compact_section(
            right_wrap,
            self.theme,
            "Active Alerts",
            subtitle="Critical failures and warnings",
            action_text="View alerts →",
            action_command=lambda: self._nav("logs", log_filter="alerts"),
        )
        alerts_shell, alerts_content = panel_card(right_body, self.theme)
        alerts_shell.pack(fill="both", expand=True)
        self._alerts_list = ctk.CTkFrame(alerts_content, fg_color="transparent")
        self._alerts_list.pack(fill="both", expand=True)

    def _build_infra_activity_row(self, scroll: ctk.CTkScrollableFrame) -> None:
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=(0, 14))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_body = compact_section(
            left,
            self.theme,
            "Resource Utilization",
            subtitle="CPU, memory, disk and connections",
        )
        infra_shell, infra_content = panel_card(left_body, self.theme)
        infra_shell.pack(fill="both", expand=True)

        status_row = ctk.CTkFrame(infra_content, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            status_row, text="Local agent", font=font(self.theme, 11), text_color=self.theme.c("text_muted")
        ).pack(side="left")
        self._host_status_chip = status_chip(status_row, self.theme, text="—", tone="neutral")
        self._host_status_chip.pack(side="right")

        for label, key in [
            ("CPU Usage", "cpu"),
            ("Memory Usage", "ram"),
            ("Disk Usage", "storage"),
            ("Network Traffic", "network"),
        ]:
            self._gauges[key] = resource_progress_row(infra_content, self.theme, label)

        conn_row = ctk.CTkFrame(infra_content, fg_color="transparent")
        conn_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(
            conn_row, text="Active Connections", font=font(self.theme, 11), text_color=self.theme.c("text_secondary")
        ).pack(side="left")
        self._conn_limit_label = ctk.CTkLabel(
            conn_row, text="— / —", font=font(self.theme, 12, weight="bold"), text_color=self.theme.c("text_primary")
        )
        self._conn_limit_label.pack(side="right")

        host_grid = ctk.CTkFrame(infra_content, fg_color="transparent")
        host_grid.pack(fill="x", pady=(8, 0))
        for label, key in [("Hostname", "hostname"), ("OS", "os"), ("Uptime", "uptime")]:
            self._host_labels[key] = host_stat_row(host_grid, self.theme, label)

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_body = compact_section(
            right,
            self.theme,
            "Database Activity",
            subtitle="Sessions, queries and throughput on the active connection",
        )
        act_shell, act_content = panel_card(right_body, self.theme)
        act_shell.pack(fill="both", expand=True)

        metrics_grid = ctk.CTkFrame(act_content, fg_color="transparent")
        metrics_grid.pack(fill="x")
        for col in range(2):
            metrics_grid.grid_columnconfigure(col, weight=1)

        activity_specs = [
            ("active_sessions", "Active Sessions"),
            ("blocked_sessions", "Blocked Sessions"),
            ("long_queries", "Long Running Queries"),
            ("slow_queries", "Slow Queries"),
            ("tps", "Transactions/sec"),
            ("connections", "Total Connections"),
        ]
        for i, (key, label) in enumerate(activity_specs):
            r, c = divmod(i, 2)
            cell = ctk.CTkFrame(
                metrics_grid,
                fg_color=self.theme.c("bg_elevated"),
                corner_radius=8,
                border_width=1,
                border_color=self.theme.c("border_subtle"),
            )
            cell.grid(row=r, column=c, sticky="ew", padx=(0 if c == 0 else 4, 0), pady=(0 if r == 0 else 6, 0))
            inner = ctk.CTkFrame(cell, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=8)
            ctk.CTkLabel(inner, text=label, font=font(self.theme, 10), text_color=self.theme.c("text_muted"), anchor="w").pack(
                anchor="w"
            )
            val = ctk.CTkLabel(
                inner, text="—", font=font(self.theme, 18, weight="bold"), text_color=self.theme.c("text_primary"), anchor="w"
            )
            val.pack(anchor="w")
            self._activity_metric_labels[key] = val

    def _build_backup_jobs_row(self, scroll: ctk.CTkScrollableFrame) -> None:
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=(0, 14))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_body = compact_section(
            left,
            self.theme,
            "Backup & Recovery Status",
            subtitle="Today's backup outcomes from the audit log",
            action_text="Open backup →",
            action_command=lambda: self._nav("backup"),
        )
        bak_shell, bak_content = panel_card(left_body, self.theme)
        bak_shell.pack(fill="both", expand=True)

        stats = ctk.CTkFrame(bak_content, fg_color="transparent")
        stats.pack(fill="x")
        for key, label in [("successful", "Successful"), ("failed", "Failed"), ("running", "Running")]:
            cell = ctk.CTkFrame(stats, fg_color=self.theme.c("bg_elevated"), corner_radius=8)
            cell.pack(side="left", fill="x", expand=True, padx=(0 if key == "successful" else 4, 0))
            ctk.CTkLabel(cell, text=label, font=font(self.theme, 10), text_color=self.theme.c("text_muted")).pack(pady=(6, 0))
            val = ctk.CTkLabel(cell, text="0", font=font(self.theme, 20, weight="bold"), text_color=self.theme.c("text_primary"))
            val.pack(pady=(0, 6))
            self._backup_labels[key] = val

        for key, label in [("last", "Last Successful"), ("next", "Next Scheduled")]:
            line = ctk.CTkFrame(bak_content, fg_color="transparent")
            line.pack(fill="x", pady=(8, 0))
            ctk.CTkLabel(line, text=label, font=font(self.theme, 11), text_color=self.theme.c("text_secondary")).pack(side="left")
            val = ctk.CTkLabel(line, text="—", font=font(self.theme, 11, weight="bold"), text_color=self.theme.c("text_primary"))
            val.pack(side="right")
            self._backup_labels[key] = val

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_body = compact_section(
            right,
            self.theme,
            "Running Automation Jobs",
            subtitle="In-memory backup and restore operations",
            action_text="View jobs →",
            action_command=lambda: self._nav("logs", log_filter="jobs"),
        )
        jobs_shell, jobs_content = panel_card(right_body, self.theme)
        jobs_shell.pack(fill="both", expand=True)
        self._jobs_list = ctk.CTkFrame(jobs_content, fg_color="transparent")
        self._jobs_list.pack(fill="both", expand=True)

    def _build_recent_activity(self, scroll: ctk.CTkScrollableFrame) -> None:
        body = compact_section(
            scroll,
            self.theme,
            "Recent Activity",
            subtitle="Latest connect, backup and restore events",
            action_text="Open logs →",
            action_command=lambda: self._nav("logs"),
        )
        shell, content = panel_card(body, self.theme)
        shell.pack(fill="x")
        self._activity_list = ctk.CTkFrame(content, fg_color="transparent")
        self._activity_list.pack(fill="x")

    def _build_legacy_detail_sections(self, scroll: ctk.CTkScrollableFrame) -> None:
        """Detail panels below the command-center rows.

        "Service Status" (InfrastructureHealthPanel) used to render here too, but every
        one of its 4 rows (saved profiles, connection, backup, replication) already has a
        home above — the KPI row, the "Database Health Overview" panel, and the
        "Backup & Recovery Status" panel — so it was pure duplication with zero unique
        data. Dropped rather than kept redundant (docs/ui_plan.md §6).
        """
        db_body = section_block(
            scroll,
            self.theme,
            "Database Performance",
            subtitle="Connection health and query metrics in real-time",
        )
        self._db_perf_refs = build_database_performance_section(db_body, self.theme)

        sec_body = section_block(
            scroll,
            self.theme,
            "Security Analytics",
            subtitle="Database connection failures and backup/restore events from audit log",
        )
        self._security_refs = build_security_section(sec_body, self.theme)


    def _apply_command_center_panels(self, db_metrics: dict | None = None, *, force: bool = False) -> None:
        """Refresh command-center panels from live MonitoringService / connection data."""
        metrics = db_metrics if db_metrics is not None else self._last_db_metrics
        self._apply_db_health_overview(metrics, force=force)
        self._apply_active_alerts_panel(force=force)
        self._apply_database_activity_panel(metrics)
        self._apply_backup_recovery_panel()
        self._apply_automation_jobs_panel(force=force)
        self._apply_recent_activity_panel(force=force)

    def _is_actively_connected(self, metrics: dict | None = None) -> bool:
        """True only when a live session exists (not merely a saved profile)."""
        if not self.connection:
            return False
        m = metrics if metrics is not None else (self._last_db_metrics or {})
        if "connected" in m:
            return bool(m.get("connected"))
        return True

    def _connection_health_bucket(self, metrics: dict) -> str:
        """Return healthy|warning|critical for the active connected session."""
        status = str(metrics.get("status", "")).lower()
        load = float(metrics.get("load_pct") or 0)
        if load >= 85 or status in {"critical", "error", "down", "offline"}:
            return "critical"
        if load >= 70 or status in {"warning", "degraded"}:
            return "warning"
        return "healthy"

    def _apply_db_health_overview(self, metrics: dict, *, force: bool = False) -> None:
        """Health counts are for *connected* profiles only; others stay Registered."""
        saved = load_saved_servers(self.owner_username)
        if not saved and self.owner_username:
            saved = load_saved_servers()
        registered = len(saved)
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

        connected_count = 1 if connected else 0
        registered_only = max(0, registered - connected_count)
        total = max(registered, connected_count)

        count_map = {
            "total": total,
            "healthy": healthy,
            "warning": warning,
            "critical": critical,
            "offline": registered_only,  # gray segment = registered / not connected
        }

        show_empty = not connected and not saved
        if self._health_visual_host:
            if show_empty:
                if self._health_visual_host.winfo_manager():
                    self._health_visual_host.pack_forget()
            else:
                if not self._health_visual_host.winfo_manager():
                    self._health_visual_host.pack(fill="x")
                if self._health_update:
                    self._health_update(count_map)

        if not self._db_health_rows:
            return

        rows: list[tuple[str, str, str]] = []
        active_saved = ""
        if self.connection:
            active_saved = (self.connection.saved_name or self.connection.name or "").strip()
            label = f"{self.connection.db_type.value.upper()}  {self.connection.name}"
            if critical:
                rows.append((label, "Critical", "error"))
            elif warning:
                rows.append((label, "Warning", "warning"))
            elif connected:
                rows.append((label, "Healthy", "success"))
            else:
                rows.append((label, "Registered", "neutral"))
        for server in saved:
            if active_saved and server.name == active_saved:
                continue
            name = f"{server.db_type.upper()}  {server.name}"
            rows.append((name, "Registered", "neutral"))

        fingerprint = f"empty={show_empty}|{rows[:8]}|{count_map}"
        if not self._content_changed("health_rows", fingerprint, force=force):
            return

        for child in self._db_health_rows.winfo_children():
            child.destroy()

        if show_empty:
            empty_state(
                self._db_health_rows,
                self.theme,
                icon="database",
                title="No databases connected",
                message="Connect a database to start monitoring health, performance and backup status.",
                action_text="+ Connect Database",
                action_command=lambda: self._nav("servers"),
                tone="info",
            )
            return

        for title, status, tone in rows[:8]:
            line = ctk.CTkFrame(self._db_health_rows, fg_color="transparent")
            line.pack(fill="x", pady=3)
            ctk.CTkLabel(
                line,
                text=title,
                font=font(self.theme, 12),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left")
            colors = {
                "success": self.theme.c("success"),
                "warning": self.theme.c("warning"),
                "error": self.theme.c("error"),
                "neutral": self.theme.c("text_muted"),
            }
            ctk.CTkLabel(
                line,
                text=status,
                font=font(self.theme, 12, weight="bold"),
                text_color=colors.get(tone, self.theme.c("text_muted")),
            ).pack(side="right")

    def _apply_active_alerts_panel(self, *, force: bool = False) -> None:
        if not self._alerts_list:
            return
        try:
            data = self.monitoring.get_security_dashboard_data(user=self.owner_username, days=7)
        except Exception:
            data = {}
        alerts = data.get("recent_alerts") or []
        fingerprint = repr(
            [(a.get("severity"), a.get("title"), a.get("detail"), a.get("time")) for a in alerts[:5]]
        ) or "empty"
        if not self._content_changed("alerts", fingerprint, force=force):
            return

        for child in self._alerts_list.winfo_children():
            child.destroy()
        if not alerts:
            empty_state(
                self._alerts_list,
                self.theme,
                icon="shield-check",
                title="Everything looks healthy",
                message="No active database alerts detected.",
                tone="success",
            )
            return
        for alert in alerts[:5]:
            alert_item_compact(
                self._alerts_list,
                self.theme,
                severity=str(alert.get("severity", "info")),
                title=str(alert.get("title", "Alert")),
                detail=str(alert.get("detail", "")),
                time_text=str(alert.get("time", "")),
            )

    def _apply_database_activity_panel(self, metrics: dict) -> None:
        connected = bool(metrics.get("connected"))
        conn_n = int(metrics.get("connections_num") or 0) if connected else 0
        max_c = metrics.get("max_connections") if connected else None
        slow = int(metrics.get("slow_num") or 0) if connected else 0
        tput = metrics.get("throughput") if connected else None
        blocked = int(metrics.get("blocked_sessions") or 0) if connected else 0
        long_q = int(metrics.get("long_running") or metrics.get("long_queries") or 0) if connected else 0

        values = {
            "active_sessions": str(conn_n) if connected else "—",
            "blocked_sessions": str(blocked) if connected else "—",
            "long_queries": str(long_q) if connected else "—",
            "slow_queries": str(slow) if connected else "—",
            "tps": f"{float(tput):.0f}" if connected and tput is not None else "—",
            "connections": (
                f"{conn_n}" if connected else "—"
            ),
        }
        for key, text in values.items():
            lbl = self._activity_metric_labels.get(key)
            if lbl:
                lbl.configure(text=text)

        if self._conn_limit_label:
            if connected:
                self._conn_limit_label.configure(text=f"{conn_n} / {max_c or '—'}")
            else:
                self._conn_limit_label.configure(text="— / —")

    def _apply_backup_recovery_panel(self) -> None:
        try:
            ok, failed = self.monitoring.count_audit_today(
                actions=("backup",),
                user=self.owner_username,
            )
        except Exception:
            ok, failed = 0, 0
        running, _pending = self.monitoring.count_live_jobs()
        mapping = {
            "successful": str(ok),
            "failed": str(failed),
            "running": str(running),
            "last": "—" if ok == 0 else "Today",
            "next": "Scheduled / Manual",
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
            lbl.configure(text=text, text_color=color)

    def _apply_automation_jobs_panel(self, *, force: bool = False) -> None:
        if not self._jobs_list:
            return
        running, pending = self.monitoring.count_live_jobs()
        fingerprint = f"{running}:{pending}"
        if not self._content_changed("jobs", fingerprint, force=force):
            return

        for child in self._jobs_list.winfo_children():
            child.destroy()
        if running == 0 and pending == 0:
            empty_state(
                self._jobs_list,
                self.theme,
                icon="briefcase",
                title="No active jobs",
                message="Backup and restore jobs will appear here when running.",
                tone="info",
            )
            return
        if running:
            row = ctk.CTkFrame(self._jobs_list, fg_color=self.theme.c("bg_elevated"), corner_radius=8)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=8)
            ctk.CTkLabel(
                inner,
                text="Backup / Restore",
                font=font(self.theme, 12, weight="bold"),
                text_color=self.theme.c("text_primary"),
            ).pack(anchor="w")
            ctk.CTkLabel(
                inner,
                text=f"Running · {running} active",
                font=font(self.theme, 11),
                text_color=self.theme.c("primary"),
            ).pack(anchor="w")
            bar = ctk.CTkProgressBar(
                inner,
                height=6,
                progress_color=self.theme.c("primary"),
                fg_color=self.theme.c("bg_card"),
            )
            bar.pack(fill="x", pady=(6, 0))
            bar.set(0.65)
        if pending:
            ctk.CTkLabel(
                self._jobs_list,
                text=f"{pending} job(s) pending in queue",
                font=font(self.theme, 11),
                text_color=self.theme.c("warning"),
                anchor="w",
            ).pack(anchor="w", pady=(6, 0))

    def _apply_recent_activity_panel(self, *, force: bool = False) -> None:
        if not self._activity_list:
            return
        try:
            events = self.monitoring.get_recent_activity(limit=8)
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
            empty_state(
                self._activity_list,
                self.theme,
                icon="activity",
                title="No recent activity",
                message="Connect, backup and restore events will show up here.",
                tone="info",
            )
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
            activity_item(
                self._activity_list,
                self.theme,
                time_text=str(event.get("time", "")),
                title=str(event.get("action", "event")).replace("_", " ").title(),
                detail=str(event.get("resource", "") or event.get("user", "")),
                tone=tone,
            )


    def _apply_db_performance_dashboard(self, metrics: dict | None = None) -> None:
        if not self._db_perf_refs:
            return
        if metrics is None:
            metrics = fetch_database_performance(self.connection)

        refs = self._db_perf_refs
        connected = bool(metrics.get("connected"))

        if connected:
            self._db_samples.append({
                "label": datetime.now().strftime("%H:%M"),
                "latency": float(metrics.get("latency_ms") or 0),
                "connections": int(metrics.get("connections_num") or 0),
                "slow": int(metrics.get("slow_num") or 0),
                "replication": float(metrics.get("replication_sec") or 0),
            })

        status_lbl = refs.get("kpi_status")
        if status_lbl:
            if connected:
                status_lbl.configure(text="Healthy", text_color=self.theme.c("success"))
            else:
                status_lbl.configure(text="Offline", text_color=self.theme.c("error"))

        uptime_lbl = refs.get("kpi_uptime")
        if uptime_lbl:
            uptime_lbl.configure(text=str(metrics.get("uptime", "—")))

        conn_n = int(metrics.get("connections_num") or 0)
        max_c = metrics.get("max_connections")
        conn_lbl = refs.get("kpi_connections")
        if conn_lbl:
            conn_lbl.configure(text=str(conn_n))
            if hasattr(conn_lbl, "_subtitle"):
                conn_lbl._subtitle.configure(text=f"Active / {max_c or '—'} Max")  # type: ignore[attr-defined]

        lat_lbl = refs.get("kpi_latency")
        if lat_lbl:
            lat_ms = metrics.get("latency_ms")
            lat_lbl.configure(text=f"{lat_ms:.1f} ms" if lat_ms is not None else "—")

        tput_lbl = refs.get("kpi_throughput")
        if tput_lbl:
            tput = metrics.get("throughput")
            tput_lbl.configure(text=f"{float(tput):.0f}" if connected and tput is not None else "—")
            if hasattr(tput_lbl, "_subtitle"):
                label = str(metrics.get("throughput_label") or "Lifetime counter")
                tput_lbl._subtitle.configure(text=label)  # type: ignore[attr-defined]

        update_chart = refs.get("update_perf_chart")
        if update_chart:
            update_chart(list(self._db_samples))

        sparks = refs.get("sparklines") or {}
        if sparks:
            # NOTE: `.get(key, ctk.CTkLabel())` looks like a safe fallback but
            # isn't — Python evaluates the default argument eagerly, so
            # ctk.CTkLabel() was being constructed (and crashing, missing its
            # required `master`) on every call regardless of whether the key
            # existed. Only configure a label when it's actually present.
            latency_lbl = sparks.get("latency")
            if latency_lbl:
                latency_lbl.configure(
                    text=metrics.get("latency", "Connect a database") if not connected else metrics.get("latency", "—")
                )
            connections_lbl = sparks.get("connections")
            if connections_lbl:
                connections_lbl.configure(text="Offline" if not connected else str(conn_n))
            slow_lbl = sparks.get("slow")
            if slow_lbl:
                slow_lbl.configure(text="—" if not connected else str(metrics.get("slow_num", "—")))
            replication_lbl = sparks.get("replication")
            if replication_lbl:
                replication_lbl.configure(text=str(metrics.get("replication_lag", "N/A")))

    def _apply_security_dashboard(self) -> None:
        if not self._security_refs:
            return
        try:
            data = self.monitoring.get_security_dashboard_data(user=self.owner_username, days=7)
        except Exception:
            return

        posture = data.get("posture") or {}
        refs = self._security_refs

        failed = refs.get("failed_logins")
        if failed:
            failed.configure(text=str(posture.get("failed_logins", 0)))

        alerts_chip = refs.get("alerts_chip")
        if alerts_chip:
            alerts_chip.configure(text=str(posture.get("security_alerts", 0)))

        compliance = refs.get("compliance")
        if compliance:
            comp_text = posture.get("compliance", "Compliant")
            compliance.configure(text=comp_text)
            if comp_text == "Compliant":
                compliance.configure(
                    fg_color=self.theme.c("success_muted"),
                    text_color=self.theme.c("success"),
                )
            elif comp_text == "Needs Attention":
                compliance.configure(
                    fg_color=self.theme.c("warning_muted"),
                    text_color=self.theme.c("warning"),
                )
            else:
                compliance.configure(
                    fg_color=self.theme.c("error_muted"),
                    text_color=self.theme.c("error"),
                )

        update_donut = refs.get("update_donut")
        if update_donut:
            update_donut(data.get("overview") or {})

        update_trend = refs.get("update_trend")
        if update_trend:
            update_trend(data.get("trend") or [])

        recent_frame = refs.get("recent_frame")
        if recent_frame:
            alerts = data.get("recent_alerts") or []
            fingerprint = repr(
                [(a.get("severity"), a.get("title"), a.get("time")) for a in alerts]
            ) or "empty"
            if self._content_changed("security_alerts", fingerprint):
                for child in recent_frame.winfo_children():
                    child.destroy()
                if not alerts:
                    ctk.CTkLabel(
                        recent_frame,
                        text="No security alerts recorded.",
                        font=ctk.CTkFont(size=12),
                        text_color=self.theme.c("text_muted"),
                    ).pack(anchor="w", pady=8)
                else:
                    for alert in alerts:
                        security_alert_item(recent_frame, self.theme, alert)

        risks_frame = refs.get("risks_frame")
        if risks_frame:
            risks = data.get("top_risks") or []
            fingerprint = repr([(r.get("title"), r.get("score")) for r in risks]) or "empty"
            if self._content_changed("security_risks", fingerprint):
                for child in risks_frame.winfo_children():
                    child.destroy()
                if not risks:
                    ctk.CTkLabel(
                        risks_frame,
                        text="No risks detected today.",
                        font=ctk.CTkFont(size=12),
                        text_color=self.theme.c("text_muted"),
                    ).pack(anchor="w", pady=8)
                else:
                    for risk in risks:
                        top_risk_row(risks_frame, self.theme, risk)

    def _apply_health_dashboard(self, db_metrics: dict | None = None) -> None:
        if not self._health_panel:
            return
        metrics = db_metrics if db_metrics is not None else self._last_db_metrics
        saved = load_saved_servers(self.owner_username)
        if not saved and self.owner_username:
            saved = load_saved_servers()
        saved_count = len(saved)

        # App session is the source of truth for "are we connected?"
        # Probe metrics refine health quality; they should not flip the row to
        # "Not Connected" while the footer still shows Connected · mongodb.
        session_active = bool(self.connection)
        probe_ok = bool(metrics.get("connected")) if metrics else False

        if session_active:
            servers = f"{saved_count} Saved · 1 Active" if saved_count else "1 Active"
        elif saved_count:
            servers = f"{saved_count} Saved"
        else:
            servers = "None Saved"

        if session_active:
            try:
                db_count = len(self._cached_server_databases())
            except Exception:
                db_count = 0
            name = self.connection.saved_name or self.connection.name
            engine = self.connection.db_type.value.upper()
            suffix = f" · {db_count} DB{'s' if db_count != 1 else ''}" if db_count else ""
            if probe_ok:
                databases = f"Connected · {engine} {name}{suffix}"
            else:
                databases = f"Connected · {engine} {name} (checking…)"
        else:
            databases = "Not Connected"

        backup_success, backup_failed = self.monitoring.count_audit_today(
            actions=("backup",),
            user=self.owner_username,
        )
        if backup_failed > 0:
            backup = f"{backup_failed} Failed"
        elif backup_success > 0:
            backup = f"{backup_success} Successful"
        else:
            backup = "No Backups Today"

        if not session_active:
            replication = "N/A"
        else:
            replication = str(metrics.get("replication_lag") or "N/A")

        self._health_panel.update_health(
            servers=servers,
            databases=databases,
            backup=backup,
            replication=replication,
        )

    def _refresh_key_metrics(self) -> None:
        try:
            ctx = self._backup_stats_context()
            db_type = ctx.get("db_type")
            # Prefer user-scoped list; fall back to all profiles so legacy rows still count.
            saved = load_saved_servers(self.owner_username)
            if not saved and self.owner_username:
                saved = load_saved_servers()
            saved_count = len(saved)

            active_name = ""
            active_engine = ""
            if self.connection:
                active_name = (self.connection.saved_name or self.connection.name or "").strip()
                active_engine = self.connection.db_type.value.upper()

            # Connected = live session only; registered = saved profiles.
            connected_count = 1 if self._is_actively_connected() else 0
            metrics = self._last_db_metrics or {}
            healthy = 0
            if connected_count:
                healthy = 1 if self._connection_health_bucket(metrics) == "healthy" else 0

            active_jobs, pending_jobs = self.monitoring.count_live_jobs()
            backup_success, backup_failed = self.monitoring.count_audit_today(
                actions=("backup", "restore"), db_type=db_type, user=self.owner_username,
            )
            connect_success, connect_failed = self.monitoring.count_audit_today(
                actions=("connect",), user=self.owner_username,
            )
            success_today = backup_success + connect_success
            failed_today = backup_failed + connect_failed

            try:
                sec = self.monitoring.get_security_dashboard_data(user=self.owner_username, days=1)
                alerts_n = int((sec.get("posture") or {}).get("security_alerts", 0))
            except Exception:
                alerts_n = failed_today

            bak_ok, bak_fail = self.monitoring.count_audit_today(
                actions=("backup",), user=self.owner_username,
            )

            for key, value in {
                "linux_servers": str(saved_count),
                "databases": str(connected_count),
                "healthy_dbs": str(healthy),
                "active_jobs": str(active_jobs),
                "active_alerts": str(alerts_n),
                "failed_today": str(bak_fail),
            }.items():
                if key in self._metric_values:
                    self._metric_values[key].configure(text=value)

            if active_name:
                saved_secondary = f"Active: {active_name} · {active_engine}"
            elif saved_count:
                saved_secondary = f"{saved_count} registered · none active"
            else:
                saved_secondary = "No saved profiles"

            if saved_count:
                databases_secondary = f"{connected_count} of {saved_count} registered"
            else:
                databases_secondary = "Connected" if connected_count else "Not connected"

            if healthy:
                healthy_secondary = "Passing health checks"
            elif connected_count:
                healthy_secondary = "Needs attention"
            else:
                healthy_secondary = "None online"

            sec_map = {
                "linux_servers": saved_secondary,
                "databases": databases_secondary,
                "healthy_dbs": healthy_secondary,
                "active_jobs": f"{pending_jobs} queued" if pending_jobs else "None running",
                "active_alerts": "Needs attention" if alerts_n else "All clear",
                "failed_today": f"{bak_ok} succeeded today",
            }
            for key, text in sec_map.items():
                if key in self._metric_secondary:
                    self._metric_secondary[key].configure(text=text)
        except Exception:
            pass

    def _backup_stats_context(self) -> dict:
        profile = self.get_active_profile()
        local_dir: str | None = None
        remote_dir = "/mnt/backup"
        list_remote = False
        db_type: DatabaseType | None = None

        if self.connection:
            db_type = self.connection.db_type
            lookup = self.connection.saved_name or self.connection.name
            for server in load_saved_servers(self.owner_username):
                if server.name == lookup and server.db_type == db_type.value:
                    if server.local_output_dir:
                        local_dir = server.local_output_dir
                    if server.remote_backup_dir:
                        remote_dir = server.remote_backup_dir
                    break
            if self.connection.remote_backup_dir:
                remote_dir = self.connection.remote_backup_dir
            if db_type == DatabaseType.MONGODB and self.connection.ssh_enabled:
                list_remote = True

        return {
            "profile": profile,
            "db_type": db_type,
            "list_remote": list_remote,
            "remote_dir": remote_dir,
            "local_dir": local_dir,
        }

    def _set_gauge(
        self,
        key: str,
        *,
        pct: float,
        value: str,
        detail: str,
        color_key: str,
    ) -> None:
        gauge = self._gauges.get(key)
        if not gauge:
            return
        bar, val_lbl, detail_lbl = gauge
        bar.set(min(1.0, max(0.0, pct / 100)))
        bar.configure(progress_color=_gauge_color(self.theme, pct, color_key))
        val_lbl.configure(text=value)
        detail_lbl.configure(text=detail)

    def _apply_system_metrics(self, sys_m: dict) -> None:
        self._last_sys_metrics = sys_m
        cpu = float(sys_m.get("cpu_percent", 0))
        mem = sys_m.get("memory") or {}
        mem_pct = float(mem.get("percent", 0))
        mem_used_gb = float(mem.get("used_mb", 0)) / 1024
        mem_total_gb = float(mem.get("total_mb", 0)) / 1024

        disk = sys_m.get("disk") or {}
        disk_pct = float(disk.get("percent", 0))
        disk_used = float(disk.get("used_gb", 0))
        disk_total = float(disk.get("total_gb", 0))
        disk_path = str(disk.get("path", "")).rstrip("\\/")

        net = sys_m.get("network") or {}
        net_pct = float(net.get("throughput_pct", 0))
        sent_label = net.get("sent_label", "0 B/s")
        recv_label = net.get("recv_label", "0 B/s")
        total_label = net.get("total_label", "0 B/s")

        cpu_count = int(sys_m.get("cpu_count", 0))
        cpu_freq = sys_m.get("cpu_freq_mhz")
        cpu_detail = f"{cpu_count} logical cores"
        if cpu_freq:
            cpu_detail += f" · {cpu_freq:.0f} MHz"

        host = sys_m.get("host") or {}
        hostname = host.get("hostname", "—")
        os_name = host.get("os", "—")
        uptime = host.get("uptime_label", "—")
        processes = str(host.get("process_count", "—"))

        self._set_gauge(
            "cpu",
            pct=cpu,
            value=f"{cpu:.0f}%",
            detail=cpu_detail,
            color_key="metric_blue",
        )
        self._set_gauge(
            "ram",
            pct=mem_pct,
            value=f"{mem_pct:.0f}%",
            detail=f"{_format_gb(mem_used_gb)} / {_format_gb(mem_total_gb)}",
            color_key="metric_purple",
        )
        self._set_gauge(
            "storage",
            pct=disk_pct,
            value=f"{disk_pct:.0f}%",
            detail=f"{_format_gb(disk_used)} / {_format_gb(disk_total)} on {disk_path or 'system'}",
            color_key="metric_amber",
        )
        self._set_gauge(
            "network",
            pct=net_pct,
            value=total_label,
            detail=f"↑ {sent_label}   ↓ {recv_label}",
            color_key="metric_green",
        )

        host_values = {
            "hostname": hostname,
            "os": os_name,
            "uptime": uptime,
            "cpu": f"{cpu_count} cores" + (f" @ {cpu_freq:.0f} MHz" if cpu_freq else ""),
            "memory": f"{_format_gb(mem_total_gb)} total",
            "disk": f"{_format_gb(disk_total)} on {disk_path or 'system'}",
            "processes": processes,
        }
        for key, text in host_values.items():
            lbl = self._host_labels.get(key)
            if lbl:
                lbl.configure(text=text)

        if self._host_status_chip:
            stress = max(cpu, mem_pct, disk_pct)
            if stress >= 90:
                self._host_status_chip.configure(
                    text="CRITICAL",
                    fg_color=self.theme.c("error_muted"),
                    text_color=self.theme.c("error"),
                )
            elif stress >= 75:
                self._host_status_chip.configure(
                    text="ELEVATED",
                    fg_color=self.theme.c("warning_muted"),
                    text_color=self.theme.c("warning"),
                )
            else:
                self._host_status_chip.configure(
                    text="HEALTHY",
                    fg_color=self.theme.c("success_muted"),
                    text_color=self.theme.c("success"),
                )

    def _poll_database_metrics(self) -> None:
        if self._db_poll_busy or not self.winfo_exists():
            return
        connection = self.connection
        self._db_poll_busy = True

        def worker() -> None:
            try:
                metrics = fetch_database_performance(connection)
            except Exception:
                metrics = {"connected": False}
            rows: list = []
            if connection is not None:
                try:
                    rows = fetch_server_databases(connection)
                except Exception:
                    rows = []
            try:
                self.after(0, lambda m=metrics, r=rows: self._finish_db_poll(m, r))
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_db_poll(self, metrics: dict, db_rows: list | None = None) -> None:
        self._db_poll_busy = False
        if not self._timers_active or not self.winfo_exists():
            return
        if db_rows is not None:
            import time

            self._db_list_cache = (time.monotonic(), db_rows)
        self._last_db_metrics = metrics
        self._apply_db_performance_dashboard(metrics)
        self._apply_health_dashboard(metrics)
        self._apply_command_center_panels(metrics)
        self._refresh_key_metrics()
        self._update_status_footer_from_metrics(metrics)

    def _update_status_footer_from_metrics(self, metrics: dict) -> None:
        """Keep the shell footer in sync with the same poll as the dashboard."""
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

    def _request_system_metrics(self) -> None:
        if self._sys_poll_busy:
            return
        self._sys_poll_busy = True
        monitoring = self.monitoring

        def worker() -> None:
            sys_m = None
            try:
                sys_m = monitoring.get_system_metrics()
            except Exception:
                pass
            try:
                self.after(0, lambda m=sys_m: self._finish_system_poll(m))
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_system_poll(self, sys_m: dict | None) -> None:
        self._sys_poll_busy = False
        if not self._timers_active or not self.winfo_exists() or not sys_m:
            return
        self._apply_system_metrics(sys_m)

    def _request_live_poll(self) -> None:
        """Refresh gauges and database stats without blocking the UI thread."""
        if not self._timers_active or not self.winfo_exists():
            return
        self._request_system_metrics()
        if self.connection:
            self._poll_database_metrics()
        else:
            self._apply_command_center_panels()
            self._refresh_key_metrics()
            if self._health_panel:
                self._apply_health_dashboard()
        self._apply_security_dashboard()

    def _schedule_system_poll(self) -> None:
        if not self._timers_active or not self.winfo_exists():
            return
        # Hidden pages must not keep querying the database or the host.
        if self._dashboard_visible():
            self._request_live_poll()
        self._poll_job = self.after(10000, self._schedule_system_poll)

    def resume(self) -> None:
        """Restart live updates when the dashboard is shown again."""
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
                self._apply_health_dashboard(empty)

            self._apply_security_dashboard()
            self._apply_command_center_panels()

            if self.sidebar_refresh:
                self.sidebar_refresh()

            self._request_system_metrics()
            if self.connection:
                self._poll_database_metrics()

            self.log("Dashboard refreshed", "SUCCESS")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
        finally:
            root = self.winfo_toplevel()
            if callable(getattr(root, "mark_last_updated", None)):
                root.mark_last_updated()
