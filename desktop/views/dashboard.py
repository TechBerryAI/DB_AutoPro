"""Enterprise operations dashboard for DB Automation Pro."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import (
    DashboardConnectionScope,
    MonitoringService,
    empty_backup_analytics,
    empty_security_dashboard,
)
from desktop.connection_store import load_saved_servers
from desktop.db_catalog import fetch_server_databases
from desktop.db_performance import fetch_database_performance
from desktop.dashboard_widgets import (
    analytics_card,
    host_stat_row,
    panel_grid,
    resource_gauge,
    section_block,
)
from desktop.db_performance_widgets import build_database_performance_section
from desktop.design_system import font, status_chip
from desktop.employee_store import EmployeeSession
from desktop.infrastructure_health import InfrastructureHealthPanel
from desktop.security_widgets import build_security_section, security_alert_item, top_risk_row
from desktop.theme import ThemeManager
from desktop.ui_components import home_greeting_card, key_metric_tile
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


def _datetime_text() -> str:
    return datetime.now().strftime("%A, %d %B %Y - %H:%M:%S")


def _format_gb(value_gb: float) -> str:
    if value_gb >= 100:
        return f"{value_gb:.0f} GB"
    return f"{value_gb:.1f} GB"


# Poll intervals (ms) — tuned to reduce UI thread load
CLOCK_MS = 2000
FAST_POLL_MS = 10000
SLOW_POLL_MS = 45000
DB_POLL_MS = 10000
COUNTS_CACHE_TTL = 30.0
UI_QUEUE_MS = 50


def _gauge_color(theme: ThemeManager, pct: float, base: str) -> str:
    if pct >= 90:
        return theme.c("error")
    if pct >= 75:
        return theme.c("warning")
    return theme.c(base)


class DashboardView(BaseView):
    """Enterprise operations dashboard."""

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
        initial_refresh = kwargs.pop("initial_refresh", False)
        super().__init__(master, theme, log_callback, employee=employee, **kwargs)
        self.monitoring = MonitoringService()
        self.navigate_callback = navigate_callback
        self.sidebar_refresh = sidebar_refresh
        self._metric_values: dict[str, ctk.CTkLabel] = {}
        self._metric_badges: dict[str, ctk.CTkLabel] = {}
        self._datetime_label: ctk.CTkLabel | None = None
        self._gauges: dict[str, tuple[ctk.CTkProgressBar, ctk.CTkLabel, ctk.CTkLabel]] = {}
        self._host_labels: dict[str, ctk.CTkLabel] = {}
        self._host_status_chip: ctk.CTkLabel | None = None
        self._poll_job: str | None = None
        self._slow_poll_job: str | None = None
        self._db_poll_job: str | None = None
        self._clock_job: str | None = None
        self._bootstrap_job: str | None = None
        self._db_poll_busy = False
        self._slow_poll_busy = False
        self._is_active = False
        self._sys_metrics_sig: tuple | None = None
        self._chart_sig: tuple | None = None
        self._last_load_pct: float | None = None
        self._db_count_cache: int | None = None
        self._saved_count_cache: int | None = None
        self._counts_cache_ts = 0.0
        self._slow_rows_sig: tuple | None = None
        self._recent_alerts_sig: tuple | None = None
        self._top_risks_sig: tuple | None = None
        self._db_perf_refs: dict = {}
        self._db_samples: deque = deque(maxlen=24)
        self._last_sys_metrics: dict = {}
        self._security_refs: dict = {}
        self._health_panel: InfrastructureHealthPanel | None = None
        self._last_db_metrics: dict = {}
        self._timers_active = True
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._ui_queue_job: str | None = None
        self._build_ui()
        if initial_refresh:
            self.refresh()

    def set_active(self, active: bool) -> None:
        """Pause or resume live polling when this view is hidden or shown."""
        if active == self._is_active:
            return
        self._is_active = active
        if self._health_panel is not None:
            self._health_panel.set_active(active)
        if not active:
            self._cancel_timer_jobs()
            return
        if self._bootstrap_job is not None:
            try:
                self.after_cancel(self._bootstrap_job)
            except Exception:
                pass
        self._bootstrap_job = self.after(1, self._bootstrap_live_data)
        self._start_ui_queue_pump()
        self._schedule_clock_tick()
        self._schedule_fast_poll()
        self._schedule_slow_poll()
        if self.connection:
            self._schedule_db_poll()
            self._apply_health_dashboard()
        else:
            empty = {"connected": False}
            self._last_db_metrics = empty
            self._db_samples.clear()
            self._chart_sig = None
            self._apply_db_performance_dashboard(empty)
            self._apply_health_dashboard(empty)
            self._apply_security_data(empty_security_dashboard())
            self._apply_backup_data(empty_backup_analytics())

    def _bootstrap_live_data(self) -> None:
        """First paint when the dashboard becomes visible — avoids work while hidden."""
        self._bootstrap_job = None
        if not self._is_active or not self.winfo_exists():
            return
        self.refresh(include_sidebar=False)

    def _cancel_timer_jobs(self) -> None:
        for attr in ("_poll_job", "_slow_poll_job", "_db_poll_job", "_clock_job", "_bootstrap_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._db_poll_busy = False
        self._slow_poll_busy = False
        self._stop_ui_queue_pump(drain=True)

    def _start_ui_queue_pump(self) -> None:
        if self._ui_queue_job is None and self._timers_active and self.winfo_exists():
            self._pump_ui_queue()

    def _stop_ui_queue_pump(self, *, drain: bool = False) -> None:
        job = self._ui_queue_job
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._ui_queue_job = None
        if drain:
            while True:
                try:
                    self._ui_queue.get_nowait()
                except queue.Empty:
                    break

    def _pump_ui_queue(self) -> None:
        self._ui_queue_job = None
        if not self._timers_active or not self.winfo_exists():
            return
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if self._timers_active and self.winfo_exists():
                    callback()
            except Exception:
                pass
        if self._timers_active and self.winfo_exists():
            try:
                self._ui_queue_job = self.after(UI_QUEUE_MS, self._pump_ui_queue)
            except Exception:
                self._ui_queue_job = None

    def _enqueue_ui(self, callback: Callable[[], None]) -> None:
        """Schedule UI work on the main thread (safe to call from worker threads)."""
        if not self._timers_active or not self._is_active:
            return
        self._ui_queue.put(callback)

    def destroy(self) -> None:
        self._timers_active = False
        self._is_active = False
        self._db_poll_busy = False
        self._slow_poll_busy = False
        if self._health_panel is not None:
            try:
                self._health_panel.destroy()
            except Exception:
                pass
            self._health_panel = None
        self._cancel_timer_jobs()
        super().destroy()

    def _schedule_clock_tick(self) -> None:
        if not self._timers_active or not self._is_active or not self.winfo_exists():
            return
        if self._datetime_label and self._datetime_label.winfo_exists():
            self._datetime_label.configure(text=_datetime_text())
        try:
            self._clock_job = self.after(CLOCK_MS, self._schedule_clock_tick)
        except Exception:
            self._clock_job = None

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=self.theme.c("bg_elevated"),
            scrollbar_button_hover_color=self.theme.c("primary"),
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=32, pady=(24, 20))
        scroll.grid_columnconfigure(0, weight=1)

        self._build_overview_page(scroll)
        self._build_health_page(scroll)

    def _build_overview_page(self, scroll: ctk.CTkScrollableFrame) -> None:
        name = self.employee.full_name if self.employee else "User"
        role = _role_label(self.employee.role) if self.employee else "Operator"

        _, self._datetime_label = home_greeting_card(
            scroll,
            self.theme,
            greeting=f"{_greeting()}, {name}",
            role=role,
            initials=_initials(name),
            datetime_text=_datetime_text(),
            on_refresh=self.refresh,
        )

        # ── Key Metrics ──
        km_body = section_block(scroll, self.theme, "Key Metrics", subtitle="Saved profiles, active connection, and today's job activity")
        grid = ctk.CTkFrame(km_body, fg_color="transparent")
        grid.pack(fill="x")
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="metric")

        tiles = [
            ("linux_servers", "⬡", "metric_blue", "metric_blue_bg", "Saved Servers", "Profiles", "Registered database profiles", "Saved"),
            ("databases", "◎", "metric_purple", "metric_purple_bg", "Databases", "Offline", "On connected server", "Multi-DB"),
            ("active_jobs", "▣", "metric_blue", "metric_blue_bg", "Active Jobs", "Live", "Backup/restore running now", "Live"),
            ("success_today", "✓", "metric_green", "metric_green_bg", "Success Today", "Today", "Connect, backup, restore OK", "Today"),
            ("failed_today", "!", "metric_red", "metric_red_bg", "Failed Today", "OK", "Connect, backup, restore errors", "Today"),
            ("pending_jobs", "◷", "metric_amber", "metric_amber_bg", "Pending Jobs", "Queue", "Queued backup/restore jobs", "Queue"),
        ]
        for i, spec in enumerate(tiles):
            key, icon, ck, bgk, title, badge, subtitle, link = spec
            row, col = divmod(i, 3)
            card, val_lbl, badge_lbl = key_metric_tile(
                grid, self.theme,
                icon=icon,
                icon_color=self.theme.c(ck),
                icon_bg=self.theme.c(bgk),
                title=title, badge=badge,
                badge_fg=self.theme.c(bgk), badge_text=self.theme.c(ck),
                subtitle=subtitle, footer_link=link,
            )
            card.grid(row=row, column=col, padx=(0 if col == 0 else 10, 0), pady=(0 if row == 0 else 10, 0), sticky="nsew")
            self._metric_values[key] = val_lbl
            self._metric_badges[key] = badge_lbl

        # ── Infrastructure Monitoring ──
        infra_body = section_block(
            scroll,
            self.theme,
            "Infrastructure Monitoring",
            subtitle="This workstation — local CPU, memory, disk, and network (via psutil)",
        )
        infra_grid = panel_grid(infra_body, self.theme, columns=2)
        infra_card, infra_content = analytics_card(infra_grid, self.theme, "Local System Resources", accent=self.theme.c("primary"))
        infra_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
        for label, color_key, key, icon in [
            ("CPU Usage", "metric_blue", "cpu", "◉"),
            ("RAM Usage", "metric_purple", "ram", "▦"),
            ("Storage Usage", "metric_amber", "storage", "▤"),
            ("Network Traffic", "metric_green", "network", "⇅"),
        ]:
            self._gauges[key] = resource_gauge(
                infra_content,
                self.theme,
                label,
                self.theme.c(color_key),
                icon=icon,
            )

        net_card, net_content = analytics_card(infra_grid, self.theme, "This Machine", accent=self.theme.c("success"))
        net_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)

        status_row = ctk.CTkFrame(net_content, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            status_row,
            text="Local agent (this PC)",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(side="left")
        self._host_status_chip = status_chip(status_row, self.theme, text="—", tone="neutral")
        self._host_status_chip.pack(side="right")

        for label, key in [
            ("Hostname", "hostname"),
            ("Operating System", "os"),
            ("Processor", "cpu"),
            ("Memory", "memory"),
            ("Disk", "disk"),
            ("Uptime", "uptime"),
            ("Processes", "processes"),
        ]:
            self._host_labels[key] = host_stat_row(net_content, self.theme, label)

    def _build_health_page(self, scroll: ctk.CTkScrollableFrame) -> None:
        health_body = section_block(
            scroll,
            self.theme,
            "Infrastructure Health",
            subtitle="Live connection and service status across your environment",
        )
        self._health_panel = InfrastructureHealthPanel(health_body, self.theme)
        self._health_panel.pack(fill="x")
        self._apply_health_dashboard()

        # ── Database Performance ──
        db_body = section_block(
            scroll,
            self.theme,
            "Database Performance",
            subtitle="Connection health and query metrics in real-time",
        )
        self._db_perf_refs = build_database_performance_section(db_body, self.theme)

        # ── Security & Alerts ──
        sec_body = section_block(scroll, self.theme, "Security & Alerts", subtitle="Database connection failures and backup/restore events from audit log")
        self._security_refs = build_security_section(sec_body, self.theme)

    def _apply_db_performance_dashboard(self, metrics: dict | None = None) -> None:
        if not self._timers_active or not self.winfo_exists() or not self._db_perf_refs:
            return
        if metrics is None:
            metrics = fetch_database_performance(self.connection)

        refs = self._db_perf_refs
        session_connected = self.connection is not None
        metrics_live = bool(metrics.get("connected"))

        if metrics_live and session_connected:
            self._db_samples.append({
                "label": datetime.now().strftime("%H:%M:%S"),
                "latency": float(metrics.get("latency_ms") or 0),
                "connections": int(metrics.get("connections_num") or 0),
                "slow": int(metrics.get("slow_num") or 0),
                "replication": float(metrics.get("replication_sec") or 0),
            })

        status_lbl = refs.get("kpi_status")
        if status_lbl:
            if metrics_live:
                status_lbl.configure(text="Healthy", text_color=self.theme.c("success"))
            elif session_connected:
                status_lbl.configure(text="Connected", text_color=self.theme.c("success"))
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
            tput_lbl.configure(text=f"{float(tput):.0f}" if metrics_live and tput is not None else "—")
            if hasattr(tput_lbl, "_subtitle"):
                label = str(metrics.get("throughput_label") or "Lifetime counter")
                tput_lbl._subtitle.configure(text=label)  # type: ignore[attr-defined]

        update_chart = refs.get("update_perf_chart")
        if update_chart:
            chart_sig = tuple(
                (s.get("latency"), s.get("connections"), s.get("slow"), s.get("replication"), s.get("label"))
                for s in self._db_samples
            )
            if chart_sig != self._chart_sig:
                self._chart_sig = chart_sig
                update_chart(list(self._db_samples))

        sparks = refs.get("sparklines") or {}
        latency_spark = sparks.get("latency")
        if latency_spark:
            latency_spark.configure(
                text=metrics.get("latency", "Connect a database") if not session_connected else metrics.get("latency", "—")
            )
        connections_spark = sparks.get("connections")
        if connections_spark:
            connections_spark.configure(text="Offline" if not session_connected else str(conn_n))
        slow_spark = sparks.get("slow")
        if slow_spark:
            slow_spark.configure(text="—" if not metrics_live else str(metrics.get("slow_num", "—")))
        replication_spark = sparks.get("replication")
        if replication_spark:
            replication_spark.configure(text=str(metrics.get("replication_lag", "N/A")))

    def _update_slow_query_rows(
        self,
        slow_frame: ctk.CTkFrame | None,
        metrics: dict,
        connected: bool,
    ) -> None:
        if not slow_frame:
            return
        rows = metrics.get("slow_query_rows") or []
        empty_msg = "No slow queries detected." if connected else "Connect a database."
        sig: tuple = (empty_msg,) if not rows else tuple(
            (str(r.get("query", ""))[:48], r.get("avg_ms"), r.get("count")) for r in rows
        )
        if sig == self._slow_rows_sig:
            return
        self._slow_rows_sig = sig
        for child in slow_frame.winfo_children():
            child.destroy()
        if not rows:
            ctk.CTkLabel(
                slow_frame,
                text=empty_msg,
                font=ctk.CTkFont(size=11),
                text_color=self.theme.c("text_muted"),
            ).pack(anchor="w", pady=6)
            return
        for row in rows:
            r = ctk.CTkFrame(slow_frame, fg_color="transparent")
            r.pack(fill="x", pady=2)
            ctk.CTkLabel(
                r,
                text=str(row.get("query", ""))[:48],
                font=font(self.theme, 10),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                r,
                text=str(row.get("avg_ms", 0)),
                font=font(self.theme, 10, weight="bold"),
                text_color=self.theme.c("text_secondary"),
                width=50,
            ).pack(side="left")
            ctk.CTkLabel(
                r,
                text=str(row.get("count", 1)),
                font=font(self.theme, 10, weight="bold"),
                text_color=self.theme.c("text_secondary"),
                width=30,
                anchor="e",
            ).pack(side="right")

    def _apply_backup_data(self, bak: dict, *, storage_mb: float = 0.0) -> None:
        if not self._db_perf_refs:
            return
        refs = self._db_perf_refs
        donut = refs.get("update_backup_donut")
        if donut:
            donut(bak)
        trend_fn = refs.get("update_backup_trend")
        if trend_fn:
            trend_fn(bak.get("trend") or [])
        if refs.get("bak_total"):
            refs["bak_total"].configure(text=f"Total Backups: {bak.get('total', 0)}")
        if refs.get("bak_failed"):
            refs["bak_failed"].configure(text=f"Failed: {bak.get('failed', 0)}")
        if refs.get("bak_storage"):
            gb = bak.get("storage_gb", 0)
            refs["bak_storage"].configure(
                text=f"Storage: {gb:.1f} GB" if gb else f"Storage: {storage_mb:.1f} MB"
            )

    def _apply_backup_analytics(self) -> None:
        if not self._db_perf_refs or not self._is_active:
            return
        scope = self._connection_scope()
        if scope is None:
            self._apply_backup_data(empty_backup_analytics())
            return
        ctx = self._backup_stats_context()
        storage_mb = 0.0
        try:
            stats = self.monitoring.get_dashboard_stats(
                profile=ctx.get("profile", "default"),
                db_type=ctx.get("db_type"),
                list_remote=ctx.get("list_remote", False),
                remote_dir=ctx.get("remote_dir"),
                local_dir=ctx.get("local_dir"),
            )
            storage_mb = stats.storage_used_mb
        except Exception:
            pass
        try:
            bak = self.monitoring.get_backup_analytics_data(
                user=self.owner_username,
                storage_mb=storage_mb,
                scope=scope,
            )
        except Exception:
            bak = empty_backup_analytics()
        self._apply_backup_data(bak, storage_mb=storage_mb)

    def _apply_key_metrics_from_snapshot(
        self,
        active_jobs: int,
        pending_jobs: int,
        backup_success: int,
        backup_failed: int,
        connect_success: int,
        connect_failed: int,
        *,
        counts: tuple[int, int],
    ) -> None:
        saved_count, db_count = counts
        success_today = backup_success + connect_success
        failed_today = backup_failed + connect_failed
        for key, value in {
            "linux_servers": str(saved_count),
            "databases": str(db_count),
            "active_jobs": str(active_jobs),
            "success_today": str(success_today),
            "failed_today": str(failed_today),
            "pending_jobs": str(pending_jobs),
        }.items():
            if key in self._metric_values:
                self._metric_values[key].configure(text=value)

        if failed_today > 0:
            self._update_badge("failed_today", "Alert", self.theme.c("metric_red_bg"), self.theme.c("metric_red"))
        else:
            self._update_badge("failed_today", "OK", self.theme.c("metric_green_bg"), self.theme.c("metric_green"))

        if self.connection:
            self._update_badge("databases", "Connected", self.theme.c("metric_purple_bg"), self.theme.c("metric_purple"))
        else:
            self._update_badge("databases", "Offline", self.theme.c("bg_elevated"), self.theme.c("text_muted"))

    def _apply_security_dashboard(self) -> None:
        if not self._security_refs:
            return
        scope = self._connection_scope()
        if scope is None:
            self._apply_security_data(empty_security_dashboard())
            return
        try:
            data = self.monitoring.get_security_dashboard_data(
                user=self.owner_username,
                days=7,
                scope=scope,
            )
        except Exception:
            return
        self._apply_security_data(data)

    def _apply_security_data(self, data: dict) -> None:
        if not self._security_refs:
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
            sig: tuple = ("none",) if not alerts else tuple(
                (a.get("action"), a.get("status"), a.get("resource"), a.get("time")) for a in alerts
            )
            if sig != self._recent_alerts_sig:
                self._recent_alerts_sig = sig
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
            sig_risks: tuple = ("none",) if not risks else tuple(
                (r.get("label"), r.get("severity"), r.get("detail")) for r in risks
            )
            if sig_risks != getattr(self, "_top_risks_sig", None):
                self._top_risks_sig = sig_risks
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

    def _cached_counts(self) -> tuple[int, int]:
        now = time.monotonic()
        if now - self._counts_cache_ts < COUNTS_CACHE_TTL and self._saved_count_cache is not None:
            return self._saved_count_cache, self._db_count_cache or 0
        saved_count = len(load_saved_servers(self.owner_username))
        db_count = 0
        if self.connection:
            try:
                db_count = len(fetch_server_databases(self.connection))
            except Exception:
                db_count = 0
        self._saved_count_cache = saved_count
        self._db_count_cache = db_count
        self._counts_cache_ts = now
        return saved_count, db_count

    def invalidate_counts_cache(self) -> None:
        self._counts_cache_ts = 0.0
        self._saved_count_cache = None
        self._db_count_cache = None

    def _apply_health_dashboard(self, db_metrics: dict | None = None) -> None:
        if not self._health_panel:
            return
        metrics = db_metrics if db_metrics is not None else self._last_db_metrics
        saved_count, db_count = self._cached_counts()
        session_connected = self.connection is not None
        connected = session_connected

        if connected:
            servers = f"{saved_count} Saved · 1 Connected" if saved_count else "1 Connected"
        elif saved_count:
            servers = f"{saved_count} Saved"
        else:
            servers = "None Saved"

        if connected:
            suffix = f" ({db_count} DB{'s' if db_count != 1 else ''})" if db_count else ""
            databases = f"Connected{suffix}"
        else:
            databases = "Not Connected"

        if connected:
            backup_success, backup_failed = self.monitoring.count_audit_today(
                actions=("backup",),
                user=self.owner_username,
                scope=self._connection_scope(),
            )
        else:
            backup_success, backup_failed = 0, 0
        if backup_failed > 0:
            backup = f"{backup_failed} Failed"
        elif backup_success > 0:
            backup = f"{backup_success} Successful"
        else:
            backup = "No Backups Today"

        if not connected:
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
            scope = self._connection_scope()
            saved_count, db_count = self._cached_counts()

            active_jobs, pending_jobs = self.monitoring.count_live_jobs()
            if scope:
                backup_success, backup_failed = self.monitoring.count_audit_today(
                    actions=("backup", "restore"), db_type=db_type, user=self.owner_username, scope=scope,
                )
                connect_success, connect_failed = self.monitoring.count_audit_today(
                    actions=("connect",), user=self.owner_username, scope=scope,
                )
            else:
                backup_success = backup_failed = connect_success = connect_failed = 0
            success_today = backup_success + connect_success
            failed_today = backup_failed + connect_failed

            for key, value in {
                "linux_servers": str(saved_count),
                "databases": str(db_count),
                "active_jobs": str(active_jobs),
                "success_today": str(success_today),
                "failed_today": str(failed_today),
                "pending_jobs": str(pending_jobs),
            }.items():
                if key in self._metric_values:
                    self._metric_values[key].configure(text=value)

            if failed_today > 0:
                self._update_badge("failed_today", "Alert", self.theme.c("metric_red_bg"), self.theme.c("metric_red"))
            else:
                self._update_badge("failed_today", "OK", self.theme.c("metric_green_bg"), self.theme.c("metric_green"))

            if self.connection:
                self._update_badge("databases", "Connected", self.theme.c("metric_purple_bg"), self.theme.c("metric_purple"))
            else:
                self._update_badge("databases", "Offline", self.theme.c("bg_elevated"), self.theme.c("text_muted"))
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

    def _connection_scope(self) -> DashboardConnectionScope | None:
        if not self.connection:
            return None
        ctx = self._backup_stats_context()
        conn = self.connection
        return DashboardConnectionScope(
            resource=conn.saved_name or conn.name or f"{conn.host}:{conn.port}",
            host=conn.endpoint_host,
            db_type=conn.db_type,
            profile=ctx.get("profile", "default"),
            local_dir=ctx.get("local_dir"),
            database=getattr(conn, "database", None),
        )

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

        host = sys_m.get("host") or {}
        sig = (
            round(cpu),
            round(mem_pct),
            round(disk_pct),
            round(net_pct),
            total_label,
            sent_label,
            recv_label,
            host.get("hostname"),
            host.get("uptime_label"),
            str(host.get("process_count", "")),
        )
        if sig == self._sys_metrics_sig:
            return
        self._sys_metrics_sig = sig
        self._last_sys_metrics = sys_m

        cpu_count = int(sys_m.get("cpu_count", 0))
        cpu_freq = sys_m.get("cpu_freq_mhz")
        cpu_detail = f"{cpu_count} logical cores"
        if cpu_freq:
            cpu_detail += f" · {cpu_freq:.0f} MHz"

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
            "cpu": f"{cpu_count} cores" + (f" @ {cpu_freq:.0f} MHz" if cpu_freq else ""),
            "memory": f"{_format_gb(mem_total_gb)} total",
            "disk": f"{_format_gb(disk_total)} on {disk_path or 'system'}",
            "uptime": uptime,
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
            self._enqueue_ui(lambda m=metrics: self._finish_db_poll(m))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_db_poll(self, metrics: dict) -> None:
        self._db_poll_busy = False
        if not self._timers_active or not self._is_active or not self.winfo_exists():
            return
        self._last_db_metrics = metrics
        self._apply_db_performance_dashboard(metrics)
        self._apply_health_dashboard(metrics)

    def _schedule_fast_poll(self) -> None:
        if not self._timers_active or not self._is_active or not self.winfo_exists():
            return
        try:
            sys_m = self.monitoring.get_system_metrics()
            self._apply_system_metrics(sys_m)
        except Exception:
            pass
        try:
            self._poll_job = self.after(FAST_POLL_MS, self._schedule_fast_poll)
        except Exception:
            self._poll_job = None

    def _schedule_slow_poll(self) -> None:
        if not self._timers_active or not self._is_active or not self.winfo_exists():
            return
        if not self._slow_poll_busy:
            self._slow_poll_busy = True
            username = self.owner_username
            ctx = self._backup_stats_context()
            conn = self.connection
            scope = self._connection_scope()

            def work() -> None:
                payload: dict = {"ctx": ctx, "username": username, "error": None}
                try:
                    saved_count = len(load_saved_servers(username))
                    db_count = 0
                    if conn:
                        try:
                            db_count = len(fetch_server_databases(conn))
                        except Exception:
                            db_count = 0
                    db_type = ctx.get("db_type")
                    active_jobs, pending_jobs = self.monitoring.count_live_jobs()
                    if scope:
                        backup_success, backup_failed = self.monitoring.count_audit_today(
                            actions=("backup", "restore"), db_type=db_type, user=username, scope=scope,
                        )
                        connect_success, connect_failed = self.monitoring.count_audit_today(
                            actions=("connect",), user=username, scope=scope,
                        )
                    else:
                        backup_success = backup_failed = connect_success = connect_failed = 0
                    payload["counts"] = (saved_count, db_count)
                    payload["jobs"] = (active_jobs, pending_jobs, backup_success, backup_failed, connect_success, connect_failed)
                    payload["security"] = (
                        self.monitoring.get_security_dashboard_data(user=username, days=7, scope=scope)
                        if scope
                        else empty_security_dashboard()
                    )
                    storage_mb = 0.0
                    if scope:
                        try:
                            stats = self.monitoring.get_dashboard_stats(
                                profile=ctx.get("profile", "default"),
                                db_type=ctx.get("db_type"),
                                list_remote=ctx.get("list_remote", False),
                                remote_dir=ctx.get("remote_dir"),
                                local_dir=ctx.get("local_dir"),
                            )
                            storage_mb = stats.storage_used_mb
                        except Exception:
                            pass
                    payload["backup"] = (
                        self.monitoring.get_backup_analytics_data(
                            user=username,
                            storage_mb=storage_mb,
                            scope=scope,
                        )
                        if scope
                        else empty_backup_analytics()
                    )
                    payload["storage_mb"] = storage_mb
                except Exception as exc:
                    payload["error"] = str(exc)

                def apply() -> None:
                    self._slow_poll_busy = False
                    if not self._timers_active or not self._is_active or not self.winfo_exists():
                        return
                    if payload.get("error"):
                        return
                    counts = payload.get("counts")
                    if counts:
                        self._saved_count_cache, self._db_count_cache = counts
                        self._counts_cache_ts = time.monotonic()
                    jobs = payload.get("jobs")
                    if jobs:
                        self._apply_key_metrics_from_snapshot(*jobs, counts=counts or (0, 0))
                    security = payload.get("security")
                    if security is not None:
                        self._apply_security_data(security)
                    backup = payload.get("backup")
                    if backup is not None:
                        self._apply_backup_data(backup, storage_mb=payload.get("storage_mb", 0.0))

                self._enqueue_ui(apply)

            threading.Thread(target=work, daemon=True).start()
        try:
            self._slow_poll_job = self.after(SLOW_POLL_MS, self._schedule_slow_poll)
        except Exception:
            self._slow_poll_job = None

    def _schedule_db_poll(self) -> None:
        if not self._timers_active or not self._is_active or not self.winfo_exists():
            return
        if self.connection:
            self._poll_database_metrics()
        try:
            self._db_poll_job = self.after(DB_POLL_MS, self._schedule_db_poll)
        except Exception:
            self._db_poll_job = None

    def _update_badge(self, key: str, text: str, fg: str, text_color: str) -> None:
        badge = self._metric_badges.get(key)
        if badge:
            badge.configure(text=text, fg_color=fg, text_color=text_color)

    def refresh(self, *, include_sidebar: bool = True) -> None:
        try:
            self.invalidate_counts_cache()
            if self._datetime_label:
                self._datetime_label.configure(text=_datetime_text())

            self._refresh_key_metrics()

            try:
                sys_m = self.monitoring.get_system_metrics()
                self._apply_system_metrics(sys_m)
            except Exception:
                pass

            if self.connection:
                self._poll_database_metrics()
            else:
                self._db_samples.clear()
                empty = fetch_database_performance(None)
                self._last_db_metrics = empty
                self._apply_db_performance_dashboard(empty)
                self._apply_health_dashboard(empty)

            self._apply_security_dashboard()
            self._apply_backup_analytics()

            if include_sidebar and self.sidebar_refresh:
                self.sidebar_refresh()

            self.log("Dashboard refreshed", "SUCCESS")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
