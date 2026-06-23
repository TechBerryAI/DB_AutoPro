"""Enterprise operations dashboard with KPIs, monitoring widgets, and activity timeline."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.connectors.base import get_connector_from_info
from backend.services.monitoring_service import MonitoringService
from desktop.theme import ThemeManager
from desktop.ui_components import (
    gauge_metric,
    health_row,
    kpi_card,
    mini_bar_chart,
    panel_card,
    progress_metric,
    quick_action_button,
    section_header,
    set_kpi_trend,
    timeline_item,
)
from desktop.views.base_view import BaseView


class DashboardView(BaseView):
    """Operations dashboard with health, KPIs, monitoring, and quick actions."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        navigate_callback: Callable[[str], None] | None = None,
        status_callback: Callable | None = None,
        database_list_callback: Callable | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.monitoring = MonitoringService()
        self.navigate_callback = navigate_callback
        self.status_callback = status_callback
        self.database_list_callback = database_list_callback
        self._kpi_values: dict[str, ctk.CTkLabel] = {}
        self._kpi_trends: dict[str, ctk.CTkLabel] = {}
        self._health_labels: dict[str, ctk.CTkLabel] = {}
        self._metric_bars: dict[str, tuple] = {}
        self._gauges: dict[str, tuple] = {}
        self._status_dot: ctk.CTkLabel | None = None
        self._status_text: ctk.CTkLabel | None = None
        self._activity_frame: ctk.CTkFrame | None = None
        self._chart_updater: Callable | None = None
        self._db_version = "—"
        self._active_sessions = "—"
        self._build_ui()
        if kwargs.pop("initial_refresh", True):
            self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)

        # Header
        header_row = ctk.CTkFrame(scroll, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 16))
        header_row.grid_columnconfigure(0, weight=1)

        header_left = ctk.CTkFrame(header_row, fg_color="transparent")
        header_left.grid(row=0, column=0, sticky="w")
        section_header(
            header_left,
            self.theme,
            "Operations Dashboard",
            "Real-time overview of database health, backups, and system resources",
        )

        refresh_btn = ctk.CTkButton(
            header_row,
            text="↻  Refresh Metrics",
            width=140,
            height=36,
            corner_radius=8,
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            text_color=self.theme.c("text_on_primary"),
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.refresh,
        )
        refresh_btn.grid(row=0, column=1, sticky="e")

        # Quick Actions
        actions_card = panel_card(scroll, self.theme, "Quick Actions")
        actions_card.pack(fill="x", pady=(0, 16))
        actions_body = ctk.CTkFrame(actions_card, fg_color="transparent")
        actions_body.pack(fill="x", padx=16, pady=(0, 16))

        actions = [
            ("Backup Now", "↓", lambda: self._nav("backup"), True),
            ("Restore", "↑", lambda: self._nav("restore"), False),
            ("Run Patch", "⚡", lambda: self._nav("patch"), False),
            ("Health Check", "◎", self._run_health_check, False),
            ("Refresh Metrics", "↻", self.refresh, False),
        ]
        for i, (label, icon, cmd, primary) in enumerate(actions):
            btn = quick_action_button(
                actions_body, self.theme, text=label, icon=icon, command=cmd, primary=primary
            )
            btn.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0), sticky="ew")
            actions_body.grid_columnconfigure(i, weight=1)

        # Database Health
        health_card = panel_card(scroll, self.theme, "Database Health")
        health_card.pack(fill="x", pady=(0, 16))

        status_frame = ctk.CTkFrame(health_card, fg_color="transparent")
        status_frame.pack(fill="x", padx=20, pady=(0, 8))
        status_frame.pack_propagate(False)
        status_right = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_right.pack(side="right")
        self._status_dot = ctk.CTkLabel(status_right, text="●", font=ctk.CTkFont(size=14))
        self._status_dot.pack(side="left", padx=(0, 6))
        self._status_text = ctk.CTkLabel(status_right, text="Checking…", font=ctk.CTkFont(size=12, weight="bold"))
        self._status_text.pack(side="left")

        health_body = ctk.CTkFrame(health_card, fg_color="transparent")
        health_body.pack(fill="x", padx=16, pady=(0, 16))
        health_body.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        for col, (key, label) in enumerate([
            ("connection", "Connection"),
            ("uptime", "Engine Version"),
            ("last_backup", "Last Backup"),
            ("response", "Response Time"),
            ("storage", "Backup Storage"),
        ]):
            cell = ctk.CTkFrame(health_body, fg_color=self.theme.c("bg_elevated"), corner_radius=8)
            cell.grid(row=0, column=col, padx=(0 if col == 0 else 6, 0), sticky="nsew")
            ctk.CTkLabel(
                cell, text=label.upper(),
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=self.theme.c("text_muted"),
            ).pack(anchor="w", padx=12, pady=(10, 2))
            lbl = ctk.CTkLabel(
                cell, text="—",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=self.theme.c("text_primary"),
                wraplength=140, justify="left",
            )
            lbl.pack(anchor="w", padx=12, pady=(0, 12))
            self._health_labels[key] = lbl

        # KPI grid
        ctk.CTkLabel(
            scroll, text="KEY METRICS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self.theme.c("text_muted"), anchor="w",
        ).pack(anchor="w", pady=(0, 10))

        kpi_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, 16))
        for col in range(3):
            kpi_grid.grid_columnconfigure(col, weight=1)

        kpis = [
            ("total_backups", "↓", "Total Backups", self.theme.c("primary")),
            ("successful_backups", "✓", "Successful", self.theme.c("success")),
            ("patch_count", "⚡", "Patches Applied", self.theme.c("warning")),
            ("storage_used_mb", "▣", "Storage Used", self.theme.c("primary")),
            ("database_connections", "◎", "Connections", self.theme.c("success")),
            ("pending_operations", "◷", "Pending Ops", self.theme.c("warning")),
        ]
        for i, (key, icon, title, accent) in enumerate(kpis):
            row, col = divmod(i, 3)
            card, val_lbl, trend_lbl = kpi_card(kpi_grid, self.theme, icon=icon, title=title, accent=accent)
            card.grid(row=row, column=col, padx=(0 if col == 0 else 8, 0), pady=(0 if row == 0 else 8, 8), sticky="nsew")
            self._kpi_values[key] = val_lbl
            self._kpi_trends[key] = trend_lbl

        # Monitoring row: gauges + backup chart
        monitor_row = ctk.CTkFrame(scroll, fg_color="transparent")
        monitor_row.pack(fill="x", pady=(0, 16))
        monitor_row.grid_columnconfigure((0, 1, 2), weight=1)

        gauge_card = panel_card(monitor_row, self.theme, "Live Monitoring")
        gauge_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        gauge_body = ctk.CTkFrame(gauge_card, fg_color="transparent")
        gauge_body.pack(fill="x", padx=14, pady=(0, 14))
        self._gauges["cpu"] = gauge_metric(gauge_body, self.theme, label="CPU Usage", color=self.theme.c("primary"))
        self._gauges["memory"] = gauge_metric(gauge_body, self.theme, label="Memory Usage", color=self.theme.c("warning"))
        self._gauges["disk"] = gauge_metric(gauge_body, self.theme, label="Disk Utilization", color=self.theme.c("success"))
        self._gauges["connections"] = gauge_metric(
            gauge_body, self.theme, label="Database Connections", color=self.theme.c("primary")
        )

        chart_card = panel_card(monitor_row, self.theme, "Backup History (7 days)")
        chart_card.grid(row=0, column=1, sticky="nsew", padx=8)
        chart_body = ctk.CTkFrame(chart_card, fg_color="transparent")
        chart_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        _, self._chart_updater = mini_bar_chart(chart_body, self.theme, title="", height=120)

        activity_card = panel_card(monitor_row, self.theme, "Recent Activity")
        activity_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        self._activity_frame = ctk.CTkFrame(activity_card, fg_color="transparent")
        self._activity_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # System resources + storage gauges
        bottom = ctk.CTkFrame(scroll, fg_color="transparent")
        bottom.pack(fill="x", pady=(0, 8))
        bottom.grid_columnconfigure((0, 1), weight=1)

        sys_card = panel_card(bottom, self.theme, "System Resources")
        sys_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        metrics_body = ctk.CTkFrame(sys_card, fg_color="transparent")
        metrics_body.pack(fill="x", padx=16, pady=(0, 18))
        self._metric_bars["cpu"] = progress_metric(
            metrics_body, self.theme, label="CPU Usage", icon="◉", color=self.theme.c("primary")
        )
        self._metric_bars["memory"] = progress_metric(
            metrics_body, self.theme, label="Memory", icon="▣", color=self.theme.c("warning")
        )
        self._metric_bars["disk"] = progress_metric(
            metrics_body, self.theme, label="Disk", icon="◫", color=self.theme.c("success")
        )
        self._metric_bars["storage"] = progress_metric(
            metrics_body, self.theme, label="Backup Storage", icon="↓", color=self.theme.c("primary")
        )

        env_card = panel_card(bottom, self.theme, "Environment")
        env_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        env_body = ctk.CTkFrame(env_card, fg_color="transparent")
        env_body.pack(fill="x", padx=16, pady=(0, 18))
        self._health_labels["platform"] = health_row(env_body, self.theme, "Platform")
        self._health_labels["environment"] = health_row(env_body, self.theme, "Environment")
        self._health_labels["db_type"] = health_row(env_body, self.theme, "Database Type")

    def _nav(self, key: str) -> None:
        if self.navigate_callback:
            self.navigate_callback(key)
        self.log(f"Quick action: navigate to {key}")

    def _run_health_check(self) -> None:
        if not self.connection:
            self.log("No active connection for health check", "WARNING")
            return
        try:
            info = self.connection.to_connection_info()
            connector = get_connector_from_info(self.connection.db_type, info)
            health = connector.health_check()
            status = health.get("status", "unknown")
            latency = health.get("latency_ms")
            msg = f"Health check: {status}"
            if latency is not None:
                msg += f" ({latency:.0f} ms)"
            level = "SUCCESS" if status == "healthy" else ("WARNING" if status == "degraded" else "ERROR")
            self.log(msg, level)
            self._set_health_status(status, latency)
        except Exception as exc:
            self.log(f"Health check failed: {exc}", "ERROR")

    def _set_health_status(self, status: str, latency_ms: float | None = None) -> None:
        if not self._status_dot or not self._status_text:
            return
        if status == "healthy":
            self._status_dot.configure(text_color=self.theme.c("success"))
            text = "Healthy"
            if latency_ms is not None:
                text += f"  ·  {latency_ms:.0f} ms"
            self._status_text.configure(text=text, text_color=self.theme.c("success"))
        elif status == "degraded":
            self._status_dot.configure(text_color=self.theme.c("warning"))
            self._status_text.configure(text="Degraded", text_color=self.theme.c("warning"))
        else:
            self._status_dot.configure(text_color=self.theme.c("error"))
            self._status_text.configure(text="Unhealthy", text_color=self.theme.c("error"))

    def _update_metric_bar(self, key: str, percent: float, detail: str) -> None:
        if key not in self._metric_bars:
            return
        bar, value_lbl, pct_lbl = self._metric_bars[key]
        pct = max(0.0, min(100.0, percent))
        bar.set(pct / 100.0)
        value_lbl.configure(text=detail)
        pct_lbl.configure(text=f"{pct:.1f}% utilized")

    def _update_gauge(self, key: str, percent: float, detail: str) -> None:
        if key not in self._gauges:
            return
        bar, value_lbl = self._gauges[key]
        pct = max(0.0, min(100.0, percent))
        bar.set(pct / 100.0)
        value_lbl.configure(text=detail)

    def _update_activity(self, events: list[dict]) -> None:
        if not self._activity_frame:
            return
        for child in self._activity_frame.winfo_children():
            child.destroy()
        if not events:
            ctk.CTkLabel(
                self._activity_frame,
                text="No recent activity",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
            ).pack(anchor="w", pady=8)
            return
        for event in events[:8]:
            timeline_item(self._activity_frame, self.theme, event)

    def _query_active_sessions(self) -> str:
        if not self.connection:
            return "—"
        try:
            info = self.connection.to_connection_info()
            connector = get_connector_from_info(self.connection.db_type, info)
            db_type = self.connection.db_type.value
            if db_type == "postgresql":
                rows = connector.execute_query(
                    "SELECT count(*) AS cnt FROM pg_stat_activity WHERE pid <> pg_backend_pid()"
                )
                return str(rows[0]["cnt"]) if rows else "—"
            if db_type == "mysql":
                rows = connector.execute_query("SHOW STATUS LIKE 'Threads_connected'")
                return str(rows[0]["Value"]) if rows else "—"
            if db_type == "mongodb":
                client = connector._get_client()
                try:
                    status = client.admin.command("serverStatus")
                    current = status.get("connections", {}).get("current")
                    return str(current) if current is not None else "—"
                finally:
                    client.close()
        except Exception:
            pass
        return "—"

    def _live_connection_count(self) -> int | None:
        sessions = self._query_active_sessions()
        if sessions == "—":
            return None
        try:
            return int(sessions)
        except ValueError:
            return None

    def _backup_stats_context(self) -> dict:
        from backend.models.schemas import DatabaseType
        from desktop.connection_store import load_saved_servers

        profile = self.get_active_profile()
        local_dir: str | None = None
        remote_dir = "/mnt/backup"
        list_remote = False
        db_type: DatabaseType | None = None

        if self.connection:
            db_type = self.connection.db_type
            lookup = self.connection.saved_name or self.connection.name
            for server in load_saved_servers():
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

    def refresh(self) -> None:
        try:
            ctx = self._backup_stats_context()
            active_connections = self._live_connection_count()
            stats = self.monitoring.get_dashboard_stats(
                **ctx,
                active_connections=active_connections,
            )
            trends = self.monitoring.get_kpi_trends(
                **ctx,
                active_connections=active_connections,
            )
            storage_pct = self.monitoring.get_storage_usage_percent(**ctx)

            mapping = {
                "total_backups": str(stats.total_backups),
                "successful_backups": str(stats.successful_backups),
                "patch_count": str(stats.patch_count),
                "storage_used_mb": f"{stats.storage_used_mb:.1f} MB",
                "database_connections": str(stats.database_connections),
                "pending_operations": str(stats.pending_operations),
            }
            for key, value in mapping.items():
                if key in self._kpi_values:
                    self._kpi_values[key].configure(text=value)
                if key in self._kpi_trends and key in trends:
                    set_kpi_trend(self._kpi_trends[key], self.theme, trends[key])

            if stats.failed_backups:
                self._kpi_trends["total_backups"].configure(
                    text=f"↓  {stats.failed_backups} failed this period",
                    text_color=self.theme.c("error"),
                )
            elif stats.successful_backups and stats.total_backups:
                success_rate = round(
                    (stats.successful_backups / (stats.successful_backups + stats.failed_backups)) * 100
                    if (stats.successful_backups + stats.failed_backups) > 0
                    else 100.0
                )
                self._kpi_trends["successful_backups"].configure(
                    text=f"↑  {success_rate:.0f}% success rate",
                    text_color=self.theme.c("success"),
                )
            elif stats.successful_backups and not stats.total_backups:
                self._kpi_trends["successful_backups"].configure(
                    text=f"↑  {stats.successful_backups} completed",
                    text_color=self.theme.c("success"),
                )

            conn_status = "● Disconnected"
            if self.connection:
                self._health_labels["connection"].configure(
                    text=f"{self.connection.name}\n{self.connection.host}:{self.connection.port}"
                )
                self._health_labels["db_type"].configure(text=self.connection.db_type.value.upper())
                conn_status = f"● Connected · {self.connection.database}"
                try:
                    info = self.connection.to_connection_info()
                    connector = get_connector_from_info(self.connection.db_type, info)
                    health = connector.health_check()
                    status = health.get("status", "unhealthy")
                    latency = health.get("latency_ms")
                    version = health.get("version", "—")
                    if isinstance(version, str) and len(version) > 48:
                        version = version.split(",")[0][:48]
                    self._db_version = str(version)[:32]
                    self._set_health_status(status, latency)
                    self._health_labels["uptime"].configure(text=str(version))
                    if latency is not None:
                        color = self.theme.c("success") if latency < 100 else self.theme.c("warning")
                        self._health_labels["response"].configure(text=f"{latency:.0f} ms", text_color=color)
                    self._active_sessions = self._query_active_sessions()
                except Exception as exc:
                    self._set_health_status("unhealthy")
                    self._health_labels["uptime"].configure(text="Unavailable")
                    self._health_labels["response"].configure(text="—")
                    self.log(f"Database health check failed: {exc}", "ERROR")

            if self.database_list_callback:
                self.database_list_callback()

            if stats.last_backup:
                self._health_labels["last_backup"].configure(
                    text=stats.last_backup.strftime("%Y-%m-%d %H:%M")
                )
            else:
                self._health_labels["last_backup"].configure(text="No backups yet")

            self._health_labels["storage"].configure(text=f"{stats.storage_used_mb:.1f} MB")

            metrics = self.monitoring.get_system_metrics()
            cpu = metrics["cpu_percent"]
            mem = metrics["memory"]
            disk = metrics["disk"]

            self._update_metric_bar("cpu", cpu, f"{cpu:.1f}%")
            self._update_metric_bar("memory", mem["percent"], f"{mem['used_mb']:.0f} / {mem['total_mb']:.0f} MB")
            self._update_metric_bar("disk", disk["percent"], f"{disk['used_gb']:.1f} / {disk['total_gb']:.1f} GB")
            self._update_metric_bar("storage", storage_pct, f"{stats.storage_used_mb:.1f} MB used")

            self._update_gauge("cpu", cpu, f"{cpu:.1f}%")
            self._update_gauge("memory", mem["percent"], f"{mem['percent']:.1f}%")
            self._update_gauge("disk", disk["percent"], f"{disk['percent']:.1f}%")
            conn_count = stats.database_connections
            conn_pct = min(100.0, conn_count * 20) if conn_count else 0
            self._update_gauge("connections", conn_pct, str(conn_count))

            if self._chart_updater:
                self._chart_updater(self.monitoring.get_backup_history_chart(**ctx))

            self._update_activity(self.monitoring.get_recent_activity())

            settings = self.monitoring.settings
            self._health_labels["platform"].configure(text=metrics.get("platform", "—")[:40])
            self._health_labels["environment"].configure(text=settings.environment.upper())

            now = datetime.now().strftime("%H:%M:%S")
            if self.status_callback:
                self.status_callback(
                    connection_status=conn_status,
                    version=self._db_version,
                    uptime=self._db_version[:20] if self._db_version != "—" else "—",
                    sessions=self._active_sessions,
                    last_refresh=now,
                )

            self.log("Dashboard metrics refreshed", "SUCCESS")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
