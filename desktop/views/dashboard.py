"""Enterprise operations dashboard for DB Automation Pro."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService
from desktop.connection_store import load_saved_servers
from desktop.db_catalog import fetch_server_databases
from desktop.db_performance import fetch_database_performance
from desktop.dashboard_widgets import (
    analytics_card,
    host_stat_row,
    metric_inline,
    panel_grid,
    resource_gauge,
    section_block,
    stat_line,
)
from desktop.design_system import status_chip
from desktop.employee_store import EmployeeSession
from desktop.infrastructure_health import InfrastructureHealthPanel
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
        initial_refresh = kwargs.pop("initial_refresh", True)
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
        self._clock_job: str | None = None
        self._db_poll_busy = False
        self._db_metrics: dict[str, ctk.CTkLabel] = {}
        self._backup_metrics: dict[str, ctk.CTkLabel] = {}
        self._security_chips: dict[str, ctk.CTkLabel] = {}
        self._health_panel: InfrastructureHealthPanel | None = None
        self._build_ui()
        if initial_refresh:
            self.refresh()
        self._schedule_clock_tick()
        self._schedule_system_poll()

    def destroy(self) -> None:
        for job_attr in ("_poll_job", "_clock_job"):
            job = getattr(self, job_attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_attr, None)
        super().destroy()

    def _schedule_clock_tick(self) -> None:
        if not self.winfo_exists():
            return
        if self._datetime_label:
            self._datetime_label.configure(text=_datetime_text())
        self._clock_job = self.after(1000, self._schedule_clock_tick)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=self.theme.c("bg_elevated"),
            scrollbar_button_hover_color=self.theme.c("primary"),
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=28, pady=(20, 16))
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
        km_body = section_block(scroll, self.theme, "Key Metrics", subtitle="Operational overview across infrastructure and jobs")
        grid = ctk.CTkFrame(km_body, fg_color="transparent")
        grid.pack(fill="x")
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="metric")

        tiles = [
            ("linux_servers", "⬡", "metric_blue", "metric_blue_bg", "Linux Servers", "Registered", "Registered SSH targets", "SSH"),
            ("databases", "◎", "metric_purple", "metric_purple_bg", "Databases", "Offline", "PostgreSQL, MySQL, Oracle, SQL Server", "Multi-DB"),
            ("active_jobs", "▣", "metric_blue", "metric_blue_bg", "Active Jobs", "Live", "Currently running", "Live"),
            ("success_today", "✓", "metric_green", "metric_green_bg", "Success Today", "Success", "Completed successfully", "Today"),
            ("failed_today", "!", "metric_red", "metric_red_bg", "Failed Jobs", "OK", "Requires review", "Clear"),
            ("pending_jobs", "◷", "metric_amber", "metric_amber_bg", "Pending Jobs", "Queue", "Queued or in progress", "Queue"),
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
        infra_body = section_block(scroll, self.theme, "Infrastructure Monitoring", subtitle="Real-time host resource utilization")
        infra_grid = panel_grid(infra_body, self.theme, columns=2)
        infra_card, infra_content = analytics_card(infra_grid, self.theme, "System Resources", accent=self.theme.c("primary"))
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

        net_card, net_content = analytics_card(infra_grid, self.theme, "Host Status", accent=self.theme.c("success"))
        net_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)

        status_row = ctk.CTkFrame(net_content, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            status_row,
            text="Local monitoring agent",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).pack(side="left")
        self._host_status_chip = status_chip(status_row, self.theme, text="ACTIVE", tone="success")
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
        self._health_panel.update_from_connection(self.connection is not None)

        # ── Database Performance ──
        db_body = section_block(scroll, self.theme, "Database Performance", subtitle="Connection health and query metrics")
        db_grid = panel_grid(db_body, self.theme, columns=2)
        db_card, db_content = analytics_card(db_grid, self.theme, "Performance Metrics", accent=self.theme.c("metric_purple"))
        db_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
        for label, key in [
            ("Query Latency", "latency"),
            ("Active Connections", "connections"),
            ("Slow Queries", "slow"),
            ("Replication Lag", "replication"),
        ]:
            self._db_metrics[key] = metric_inline(db_content, self.theme, label)

        # ── Backup Analytics ──
        bak_card, bak_content = analytics_card(db_grid, self.theme, "Backup Analytics", accent=self.theme.c("metric_green"))
        bak_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)
        for label, key in [
            ("Backup Success Rate", "success_rate"),
            ("Failed Backups", "failed"),
            ("Storage Consumed", "storage"),
        ]:
            self._backup_metrics[key] = metric_inline(bak_content, self.theme, label)

        # ── Security & Alerts ──
        sec_body = section_block(scroll, self.theme, "Security & Alerts", subtitle="Authentication events and compliance posture")
        sec_grid = panel_grid(sec_body, self.theme, columns=2)
        sec_card, sec_content = analytics_card(sec_grid, self.theme, "Security Posture", accent=self.theme.c("error"))
        sec_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
        for label, key, tone in [
            ("Failed Login Attempts", "failed_logins", "error"),
            ("Security Alerts", "alerts", "warning"),
            ("Compliance Status", "compliance", "success"),
        ]:
            self._security_chips[key] = stat_line(sec_content, self.theme, label, tone=tone)

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

    def _apply_db_metrics(self, metrics: dict) -> None:
        if not metrics.get("connected"):
            offline = {
                "latency": "Connect a database",
                "connections": "Offline",
                "slow": "—",
                "replication": "N/A",
            }
            for key, text in offline.items():
                lbl = self._db_metrics.get(key)
                if lbl:
                    lbl.configure(text=text)
            return

        mapping = {
            "latency": metrics.get("latency", "—"),
            "connections": f"{metrics.get('connections', '0')} active",
            "slow": str(metrics.get("slow_queries", "0")),
            "replication": str(metrics.get("replication_lag", "N/A")),
        }
        for key, text in mapping.items():
            lbl = self._db_metrics.get(key)
            if lbl:
                lbl.configure(text=text)

    def _apply_backup_metrics(
        self,
        *,
        backup_success: int,
        backup_failed: int,
        ctx: dict,
    ) -> None:
        total_bak = backup_success + backup_failed
        if total_bak:
            rate = f"{int(backup_success / total_bak * 100)}%"
        else:
            rate = "No runs today"

        self._backup_metrics.get("success_rate", ctk.CTkLabel()).configure(text=rate)
        self._backup_metrics.get("failed", ctk.CTkLabel()).configure(text=str(backup_failed))

        storage = "0.0 MB"
        try:
            stats = self.monitoring.get_dashboard_stats(
                profile=ctx.get("profile", "default"),
                db_type=ctx.get("db_type"),
                list_remote=ctx.get("list_remote", False),
                remote_dir=ctx.get("remote_dir"),
                local_dir=ctx.get("local_dir"),
            )
            storage = f"{stats.storage_used_mb:.1f} MB"
        except Exception:
            pass
        self._backup_metrics.get("storage", ctk.CTkLabel()).configure(text=storage)

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
            if self.winfo_exists():
                self.after(0, lambda: self._finish_db_poll(metrics))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_db_poll(self, metrics: dict) -> None:
        self._db_poll_busy = False
        self._apply_db_metrics(metrics)

    def _schedule_system_poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            sys_m = self.monitoring.get_system_metrics()
            self._apply_system_metrics(sys_m)
        except Exception:
            pass
        self._poll_database_metrics()
        try:
            ctx = self._backup_stats_context()
            backup_success, backup_failed = self.monitoring.count_audit_today(
                actions=("backup", "restore"),
                db_type=ctx.get("db_type"),
                user=self.owner_username,
            )
            self._apply_backup_metrics(
                backup_success=backup_success,
                backup_failed=backup_failed,
                ctx=ctx,
            )
        except Exception:
            pass
        if self._health_panel:
            self._health_panel.update_from_connection(self.connection is not None)
        self._poll_job = self.after(3000, self._schedule_system_poll)

    def _update_badge(self, key: str, text: str, fg: str, text_color: str) -> None:
        badge = self._metric_badges.get(key)
        if badge:
            badge.configure(text=text, fg_color=fg, text_color=text_color)

    def refresh(self) -> None:
        try:
            if self._datetime_label:
                self._datetime_label.configure(text=_datetime_text())

            ctx = self._backup_stats_context()
            db_type = ctx.get("db_type")
            linux_servers = len(load_saved_servers(self.owner_username))

            db_count = 0
            if self.connection:
                try:
                    db_count = len(fetch_server_databases(self.connection))
                except Exception:
                    db_count = 0

            active_jobs, pending_jobs = self.monitoring.count_live_jobs()
            backup_success, backup_failed = self.monitoring.count_audit_today(
                actions=("backup", "restore"), db_type=db_type, user=self.owner_username,
            )
            connect_success, connect_failed = self.monitoring.count_audit_today(
                actions=("connect",), user=self.owner_username,
            )
            success_today = backup_success + connect_success
            failed_today = backup_failed + connect_failed

            for key, value in {
                "linux_servers": str(linux_servers),
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

            # System gauges (live host metrics)
            try:
                sys_m = self.monitoring.get_system_metrics()
                self._apply_system_metrics(sys_m)
            except Exception:
                pass

            # DB performance (async live query; backup metrics below stay synchronous)
            if self.connection:
                self._poll_database_metrics()
            else:
                self._apply_db_metrics({"connected": False})

            # Backup analytics (audit log + backup inventory)
            self._apply_backup_metrics(
                backup_success=backup_success,
                backup_failed=backup_failed,
                ctx=ctx,
            )

            # Security
            self._security_chips.get("failed_logins", ctk.CTkLabel()).configure(text=str(connect_failed))
            self._security_chips.get("alerts", ctk.CTkLabel()).configure(text=str(failed_today))
            self._security_chips.get("compliance", ctk.CTkLabel()).configure(text="Compliant")

            if self._health_panel:
                self._health_panel.update_from_connection(self.connection is not None)

            if self.sidebar_refresh:
                self.sidebar_refresh()

            self.log("Dashboard refreshed", "SUCCESS")
        except Exception as exc:
            self.log(f"Dashboard refresh failed: {exc}", "ERROR")
