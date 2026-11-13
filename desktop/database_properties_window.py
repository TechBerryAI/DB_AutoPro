"""Classic Windows-style Database Properties child window.

Looks and behaves like a traditional admin utility (pgAdmin / SSMS / WinSCP),
not a modern SaaS dashboard: rectangular tabs, property grids, dense tables.
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections import deque
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from backend.services.backup_service import BackupService
from backend.services.monitoring_service import MonitoringService
from desktop.connection import ConnectionResult, test_connection
from desktop.connection_store import SavedServer, load_saved_servers
from desktop.db_catalog import fetch_mongodb_database_overview, fetch_server_databases
from desktop.db_performance import fetch_database_performance
from desktop.execution_log import ExecutionLogStore
from desktop.theme import ThemeManager
from desktop.window_icon import schedule_window_icon

_OPEN_WINDOWS: dict[str, "DatabasePropertiesWindow"] = {}

_TAB_NAMES = (
    "General",
    "Connection",
    "Performance",
    "Tables & Collections",
    "Backup & Restore",
    "Execution Logs",
)

# Classic desktop chrome — slightly flatter / greyer than Beacon cards.
_R = 2


def open_database_properties(
    master,
    theme: ThemeManager,
    server: SavedServer,
    *,
    active_connection: ConnectionResult | None = None,
    log_store: ExecutionLogStore | None = None,
    on_disconnect: Callable[[], None] | None = None,
    on_navigate: Callable[[str], None] | None = None,
    on_connect: Callable[[ConnectionResult], None] | None = None,
) -> DatabasePropertiesWindow:
    """Open (or raise) the classic properties window for a saved server."""
    key = server.name.strip().lower()
    existing = _OPEN_WINDOWS.get(key)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.set_active_connection(active_connection)
                existing.lift()
                existing.focus_force()
                return existing
        except Exception:
            _OPEN_WINDOWS.pop(key, None)

    win = DatabasePropertiesWindow(
        master,
        theme,
        server,
        active_connection=active_connection,
        log_store=log_store,
        on_disconnect=on_disconnect,
        on_navigate=on_navigate,
        on_connect=on_connect,
    )
    _OPEN_WINDOWS[key] = win
    return win


def open_database_properties_by_name(
    master,
    theme: ThemeManager,
    name: str,
    *,
    owner_username: str | None = None,
    active_connection: ConnectionResult | None = None,
    log_store: ExecutionLogStore | None = None,
    on_disconnect: Callable[[], None] | None = None,
    on_navigate: Callable[[str], None] | None = None,
    on_connect: Callable[[ConnectionResult], None] | None = None,
) -> DatabasePropertiesWindow | None:
    servers = load_saved_servers(owner_username)
    if not servers:
        servers = load_saved_servers()
    match = next((s for s in servers if s.name == name), None)
    if match is None:
        return None
    return open_database_properties(
        master,
        theme,
        match,
        active_connection=active_connection,
        log_store=log_store,
        on_disconnect=on_disconnect,
        on_navigate=on_navigate,
        on_connect=on_connect,
    )


class DatabasePropertiesWindow(ctk.CTkToplevel):
    """Traditional multi-tab Database Properties dialog."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        server: SavedServer,
        *,
        active_connection: ConnectionResult | None = None,
        log_store: ExecutionLogStore | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
        on_connect: Callable[[ConnectionResult], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.theme = theme
        self.server = server
        self.log_store = log_store
        self.on_disconnect = on_disconnect
        self.on_navigate = on_navigate
        self.on_connect = on_connect
        self._active = active_connection
        self.monitoring = MonitoringService()
        self._alive = True
        self._poll_job: str | None = None
        self._perf_samples: deque = deque(maxlen=40)
        self._tab_buttons: dict[str, ctk.CTkButton] = {}
        self._tab_frames: dict[str, ctk.CTkFrame] = {}
        self._prop_labels: dict[str, ctk.CTkLabel] = {}
        self._conn_labels: dict[str, ctk.CTkLabel] = {}
        self._perf_labels: dict[str, ctk.CTkLabel] = {}
        self._status_bar: ctk.CTkLabel | None = None
        self._chart_update: Callable | None = None
        self._objects_host: ctk.CTkScrollableFrame | None = None
        self._backup_host: ctk.CTkScrollableFrame | None = None
        self._logs_host: ctk.CTkScrollableFrame | None = None
        self._log_filter = ctk.StringVar(value="ALL")
        self._busy_test = False
        self._current_tab = "General"

        title_name = server.name or "Database"
        self.title(f"{title_name} – Database Properties")
        self.configure(fg_color=self._c("bg"))
        schedule_window_icon(self)
        self.geometry("780x560")
        self.minsize(640, 440)
        self.resizable(True, True)
        if master is not None:
            try:
                self.transient(master)
            except Exception:
                pass
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_tabs()
        self._build_status_bar()
        self._show_tab("General")
        self.after(50, self.refresh_all)
        self._schedule_perf_poll()

    # ── classic palette helpers ──────────────────────────────────────────

    def _c(self, role: str) -> str:
        t = self.theme
        mapping = {
            "bg": t.c("bg_content"),
            "panel": t.c("bg_card"),
            "raised": t.c("bg_elevated"),
            "input": t.c("bg_input"),
            "border": t.c("border"),
            "text": t.c("text_primary"),
            "muted": t.c("text_secondary"),
            "dim": t.c("text_muted"),
            "accent": t.c("primary"),
            "ok": t.c("success"),
            "warn": t.c("warning"),
            "err": t.c("error"),
            "tab_idle": t.c("bg_elevated"),
            "tab_active": t.c("bg_card"),
        }
        return mapping.get(role, t.c("bg_content"))

    def _font(self, size: int = 12, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)

    def _mono(self, size: int = 11) -> ctk.CTkFont:
        return ctk.CTkFont(family="Consolas", size=size)

    # ── chrome ───────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(
            self, fg_color=self._c("raised"), corner_radius=0, height=34,
            border_width=0,
        )
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", fill="y", padx=8, pady=4)
        ctk.CTkLabel(
            left,
            text=self.server.name,
            font=self._font(12, "bold"),
            text_color=self._c("text"),
        ).pack(side="left")
        engine = (self.server.db_type or "").upper()
        ctk.CTkLabel(
            left,
            text=f"  |  {engine}",
            font=self._font(11),
            text_color=self._c("muted"),
        ).pack(side="left")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=6, pady=3)
        self._tool_btn(right, "Refresh", self.refresh_all).pack(side="left", padx=2)
        self._tool_btn(right, "Close", self._close).pack(side="left", padx=2)
        ctk.CTkFrame(self, height=1, fg_color=self._c("border"), corner_radius=0).grid(
            row=0, column=0, sticky="sew"
        )

    def _tool_btn(self, parent, text: str, command) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            width=72,
            height=24,
            corner_radius=_R,
            fg_color=self._c("panel"),
            hover_color=self._c("input"),
            border_width=1,
            border_color=self._c("border"),
            text_color=self._c("text"),
            font=self._font(11),
            command=command,
        )

    def _action_btn(
        self, parent, text: str, command, *, primary: bool = False, width: int = 120
    ) -> ctk.CTkButton:
        if primary:
            return ctk.CTkButton(
                parent,
                text=text,
                width=width,
                height=28,
                corner_radius=_R,
                fg_color=self._c("accent"),
                hover_color=self.theme.c("primary_hover"),
                text_color=self.theme.c("text_on_primary"),
                font=self._font(11),
                command=command,
            )
        return ctk.CTkButton(
            parent,
            text=text,
            width=width,
            height=28,
            corner_radius=_R,
            fg_color=self._c("panel"),
            hover_color=self._c("input"),
            border_width=1,
            border_color=self._c("border"),
            text_color=self._c("text"),
            font=self._font(11),
            command=command,
        )

    def _build_tabs(self) -> None:
        shell = ctk.CTkFrame(self, fg_color=self._c("bg"), corner_radius=0)
        shell.grid(row=1, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)

        strip = ctk.CTkFrame(shell, fg_color=self._c("raised"), corner_radius=0, height=30)
        strip.grid(row=0, column=0, sticky="ew")
        strip.grid_propagate(False)

        for name in _TAB_NAMES:
            btn = ctk.CTkButton(
                strip,
                text=name,
                height=28,
                width=118,
                corner_radius=0,
                fg_color=self._c("tab_idle"),
                hover_color=self._c("panel"),
                border_width=0,
                text_color=self._c("muted"),
                font=self._font(11),
                command=lambda n=name: self._show_tab(n),
            )
            btn.pack(side="left", padx=(0, 1), pady=0)
            self._tab_buttons[name] = btn

        ctk.CTkFrame(shell, height=1, fg_color=self._c("border"), corner_radius=0).grid(
            row=0, column=0, sticky="sew"
        )

        body = ctk.CTkFrame(shell, fg_color=self._c("panel"), corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self._body = body

        self._tab_frames["General"] = self._build_general_tab(body)
        self._tab_frames["Connection"] = self._build_connection_tab(body)
        self._tab_frames["Performance"] = self._build_performance_tab(body)
        self._tab_frames["Tables & Collections"] = self._build_objects_tab(body)
        self._tab_frames["Backup & Restore"] = self._build_backup_tab(body)
        self._tab_frames["Execution Logs"] = self._build_logs_tab(body)

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(
            self, fg_color=self._c("raised"), corner_radius=0, height=24, border_width=0
        )
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        ctk.CTkFrame(self, height=1, fg_color=self._c("border"), corner_radius=0).grid(
            row=2, column=0, sticky="new"
        )
        self._status_bar = ctk.CTkLabel(
            bar,
            text="Ready",
            font=self._font(10),
            text_color=self._c("muted"),
            anchor="w",
        )
        self._status_bar.pack(side="left", padx=8)

    def _set_status(self, text: str) -> None:
        if self._status_bar and self._status_bar.winfo_exists():
            self._status_bar.configure(text=text)

    def _show_tab(self, name: str) -> None:
        self._current_tab = name
        for key, frame in self._tab_frames.items():
            if key == name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_forget()
        for key, btn in self._tab_buttons.items():
            active = key == name
            btn.configure(
                fg_color=self._c("tab_active") if active else self._c("tab_idle"),
                text_color=self._c("text") if active else self._c("muted"),
                font=self._font(11, "bold" if active else "normal"),
            )
        if name == "Tables & Collections":
            self.after(10, self._load_objects)
        elif name == "Backup & Restore":
            self.after(10, self._load_backups)
        elif name == "Execution Logs":
            self.after(10, self._load_logs)
        elif name == "Performance":
            self.after(10, self._refresh_performance)

    # ── property grid ────────────────────────────────────────────────────

    def _section(self, parent, title: str) -> ctk.CTkFrame:
        box = ctk.CTkFrame(
            parent,
            fg_color=self._c("panel"),
            corner_radius=0,
            border_width=1,
            border_color=self._c("border"),
        )
        box.pack(fill="x", padx=10, pady=(10, 0))
        hdr = ctk.CTkFrame(box, fg_color=self._c("raised"), corner_radius=0, height=24)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr,
            text=title,
            font=self._font(11, "bold"),
            text_color=self._c("text"),
            anchor="w",
        ).pack(side="left", padx=8)
        body = ctk.CTkFrame(box, fg_color=self._c("panel"), corner_radius=0)
        body.pack(fill="both", expand=True, padx=0, pady=0)
        return body

    def _prop_grid(
        self, parent, keys: list[tuple[str, str]], store: dict[str, ctk.CTkLabel]
    ) -> None:
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_columnconfigure(3, weight=1)
        for i, (key, label) in enumerate(keys):
            row, col = divmod(i, 2)
            base_col = col * 2
            ctk.CTkLabel(
                parent,
                text=label,
                font=self._font(11),
                text_color=self._c("muted"),
                anchor="w",
                width=120,
            ).grid(row=row, column=base_col, sticky="w", padx=(10, 6), pady=5)
            val = ctk.CTkLabel(
                parent,
                text="—",
                font=self._font(11),
                text_color=self._c("text"),
                anchor="w",
            )
            val.grid(row=row, column=base_col + 1, sticky="ew", padx=(0, 12), pady=5)
            store[key] = val
            if col == 0:
                ctk.CTkFrame(
                    parent, width=1, fg_color=self._c("border"), corner_radius=0
                ).grid(row=row, column=2, sticky="ns", pady=2)

    def _set_props(self, store: dict[str, ctk.CTkLabel], values: dict[str, str]) -> None:
        for key, label in store.items():
            if label.winfo_exists():
                label.configure(text=str(values.get(key, "—") or "—"))

    # ── tabs ─────────────────────────────────────────────────────────────

    def _build_general_tab(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self._c("panel"), corner_radius=0)
        frame.grid_columnconfigure(0, weight=1)
        body = self._section(frame, "General")
        self._prop_grid(
            body,
            [
                ("name", "Database name"),
                ("engine", "Database engine"),
                ("version", "Database version"),
                ("host", "Hostname"),
                ("port", "Port"),
                ("status", "Connection status"),
                ("health", "Health status"),
                ("uptime", "Database uptime"),
            ],
            self._prop_labels,
        )
        return frame

    def _build_connection_tab(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self._c("panel"), corner_radius=0)
        body = self._section(frame, "Connection")
        self._prop_grid(
            body,
            [
                ("host", "Host"),
                ("port", "Port"),
                ("username", "Username"),
                ("auth", "Authentication method"),
                ("status", "Connection status"),
                ("endpoint", "Endpoint"),
            ],
            self._conn_labels,
        )
        note = ctk.CTkLabel(
            frame,
            text="Passwords and SSH credentials are never displayed.",
            font=self._font(10),
            text_color=self._c("dim"),
            anchor="w",
        )
        note.pack(fill="x", padx=12, pady=(8, 0))
        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.pack(fill="x", padx=10, pady=12)
        self._test_btn = self._action_btn(
            actions, "Test Connection", self._on_test_connection, primary=True, width=140
        )
        self._test_btn.pack(side="left", padx=(0, 8))
        self._disconnect_btn = self._action_btn(
            actions, "Disconnect", self._on_disconnect, width=110
        )
        self._disconnect_btn.pack(side="left")
        self._test_result = ctk.CTkLabel(
            frame,
            text="",
            font=self._font(11),
            text_color=self._c("muted"),
            anchor="w",
            wraplength=700,
            justify="left",
        )
        self._test_result.pack(fill="x", padx=12, pady=(0, 10))
        return frame

    def _build_performance_tab(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self._c("panel"), corner_radius=0)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        metrics = self._section(frame, "Performance metrics")
        self._prop_grid(
            metrics,
            [
                ("latency", "Query latency"),
                ("connections", "Active connections"),
                ("slow", "Slow queries"),
                ("tps", "Throughput"),
            ],
            self._perf_labels,
        )
        chart_box = self._section(frame, "Performance chart")
        chart_box.pack(fill="both", expand=True)
        self._chart_update = self._build_classic_chart(chart_box)
        return frame

    def _build_classic_chart(self, parent) -> Callable:
        shell = ctk.CTkFrame(parent, fg_color=self._c("input"), corner_radius=0)
        shell.pack(fill="both", expand=True, padx=8, pady=8)
        canvas = tk.Canvas(
            shell,
            height=180,
            bg=self._c("input"),
            highlightthickness=1,
            highlightbackground=self._c("border"),
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        samples: list[dict] = []

        def update(series: list[dict] | None = None, message: str | None = None) -> None:
            nonlocal samples
            if series is not None:
                samples = list(series)
            canvas.delete("all")
            w = max(canvas.winfo_width(), 400)
            h = max(canvas.winfo_height(), 160)
            pad_l, pad_r, pad_t, pad_b = 36, 12, 12, 20
            grid = self._c("border")
            for i in range(4):
                y = pad_t + (h - pad_t - pad_b) * i / 3
                canvas.create_line(pad_l, y, w - pad_r, y, fill=grid, width=1)
            if not samples:
                canvas.create_text(
                    w // 2,
                    h // 2,
                    text=message or "Connect to collect live performance samples.",
                    fill=self._c("dim"),
                    font=("Segoe UI", 10),
                )
                return
            keys = ("latency_ms", "connections_num")
            colors = (self._c("accent"), self.theme.c("metric_blue"))
            max_y = max(
                1.0,
                max(float(pt.get(k, 0) or 0) for pt in samples for k in keys),
            )
            n = len(samples)
            xs = [pad_l + ((w - pad_l - pad_r) * i / max(n - 1, 1)) for i in range(n)]
            for key, color in zip(keys, colors):
                pts: list[float] = []
                for i, pt in enumerate(samples):
                    val = float(pt.get(key, 0) or 0)
                    y = pad_t + (h - pad_t - pad_b) - (val / max_y) * (h - pad_t - pad_b)
                    pts.extend([xs[i], y])
                if len(pts) >= 4:
                    canvas.create_line(*pts, fill=color, width=2)
            canvas.create_text(
                pad_l - 4,
                pad_t,
                text=f"{max_y:.0f}",
                anchor="ne",
                fill=self._c("dim"),
                font=("Segoe UI", 8),
            )
            canvas.create_text(
                w - 8,
                h - 6,
                text="latency (ms)  ·  connections",
                anchor="se",
                fill=self._c("dim"),
                font=("Segoe UI", 8),
            )

        canvas.bind("<Configure>", lambda _e: update())
        return update

    def _build_objects_tab(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self._c("panel"), corner_radius=0)
        toolbar = ctk.CTkFrame(frame, fg_color="transparent")
        toolbar.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(
            toolbar,
            text="Databases / schemas / tables & collections",
            font=self._font(11, "bold"),
            text_color=self._c("text"),
        ).pack(side="left")
        self._action_btn(toolbar, "Refresh", self._load_objects, width=90).pack(
            side="right"
        )
        header = ctk.CTkFrame(
            frame, fg_color=self._c("raised"), corner_radius=0, height=26
        )
        header.pack(fill="x", padx=10, pady=(4, 0))
        header.pack_propagate(False)
        for text, w in (("Name", 280), ("Type", 120), ("Count / Size", 160)):
            ctk.CTkLabel(
                header,
                text=text,
                width=w,
                anchor="w",
                font=self._font(10, "bold"),
                text_color=self._c("muted"),
            ).pack(side="left", padx=6)
        self._objects_host = ctk.CTkScrollableFrame(
            frame, fg_color=self._c("input"), corner_radius=0
        )
        self._objects_host.pack(fill="both", expand=True, padx=10, pady=8)
        self._objects_loading = False
        return frame

    def _build_backup_tab(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self._c("panel"), corner_radius=0)
        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.pack(fill="x", padx=10, pady=10)
        self._action_btn(
            actions, "Backup Now", lambda: self._goto("backup"), primary=True, width=120
        ).pack(side="left", padx=(0, 8))
        self._action_btn(
            actions, "Restore", lambda: self._goto("restore"), width=100
        ).pack(side="left", padx=(0, 8))
        self._action_btn(
            actions, "View Backup History", self._load_backups, width=150
        ).pack(side="left")

        status = self._section(frame, "Backup status")
        self._backup_status = ctk.CTkLabel(
            status,
            text="—",
            font=self._font(11),
            text_color=self._c("text"),
            anchor="w",
            justify="left",
        )
        self._backup_status.pack(fill="x", padx=10, pady=8)

        hist_hdr = ctk.CTkFrame(
            frame, fg_color=self._c("raised"), corner_radius=0, height=26
        )
        hist_hdr.pack(fill="x", padx=10, pady=(8, 0))
        hist_hdr.pack_propagate(False)
        for text, w in (("Timestamp", 150), ("Name / Path", 320), ("Status", 100)):
            ctk.CTkLabel(
                hist_hdr,
                text=text,
                width=w,
                anchor="w",
                font=self._font(10, "bold"),
                text_color=self._c("muted"),
            ).pack(side="left", padx=6)
        self._backup_host = ctk.CTkScrollableFrame(
            frame, fg_color=self._c("input"), corner_radius=0
        )
        self._backup_host.pack(fill="both", expand=True, padx=10, pady=8)
        return frame

    def _build_logs_tab(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=self._c("panel"), corner_radius=0)
        toolbar = ctk.CTkFrame(frame, fg_color="transparent")
        toolbar.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(
            toolbar, text="Filter:", font=self._font(11), text_color=self._c("muted")
        ).pack(side="left")
        ctk.CTkOptionMenu(
            toolbar,
            variable=self._log_filter,
            values=["ALL", "INFO", "SUCCESS", "WARNING", "ERROR"],
            width=110,
            height=26,
            corner_radius=_R,
            fg_color=self._c("raised"),
            button_color=self._c("panel"),
            button_hover_color=self._c("input"),
            dropdown_fg_color=self._c("panel"),
            font=self._font(11),
            command=lambda _v: self._load_logs(),
        ).pack(side="left", padx=8)
        self._action_btn(toolbar, "Refresh", self._load_logs, width=90).pack(side="right")

        header = ctk.CTkFrame(
            frame, fg_color=self._c("raised"), corner_radius=0, height=26
        )
        header.pack(fill="x", padx=10)
        header.pack_propagate(False)
        for text, w in (
            ("Timestamp", 90),
            ("Operation", 120),
            ("Result", 90),
            ("Details", 360),
        ):
            ctk.CTkLabel(
                header,
                text=text,
                width=w,
                anchor="w",
                font=self._font(10, "bold"),
                text_color=self._c("muted"),
            ).pack(side="left", padx=6)
        self._logs_host = ctk.CTkScrollableFrame(
            frame, fg_color=self._c("input"), corner_radius=0
        )
        self._logs_host.pack(fill="both", expand=True, padx=10, pady=8)
        return frame

    # ── data / actions ───────────────────────────────────────────────────

    def set_active_connection(self, connection: ConnectionResult | None) -> None:
        self._active = connection
        self.refresh_all()

    def _session_connection(self) -> ConnectionResult | None:
        if self._active and self._matches_active(self._active):
            return self._active
        return None

    def _matches_active(self, connection: ConnectionResult) -> bool:
        saved = (connection.saved_name or connection.name or "").strip()
        return saved == self.server.name or (
            connection.host == self.server.host
            and int(connection.port) == int(self.server.port)
            and connection.db_type.value == self.server.db_type
        )

    def _profile_connection(self) -> ConnectionResult:
        live = self._session_connection()
        if live is not None:
            return live
        return self.server.to_connection_result()

    def _auth_method(self) -> str:
        parts: list[str] = []
        if self.server.ssh_enabled:
            parts.append("SSH tunnel")
        if self.server.db_type == DatabaseType.MONGODB.value:
            auth_db = (self.server.auth_database or "admin").strip() or "admin"
            parts.append(f"MongoDB auth ({auth_db})")
        elif self.server.username:
            parts.append("Username / password")
        else:
            parts.append("Default / integrated")
        return " · ".join(parts)

    def refresh_all(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        self._refresh_general()
        self._refresh_connection_tab()
        self._refresh_performance()
        self._set_status(
            f"Last refreshed {datetime.now().strftime('%H:%M:%S')}  ·  {self.server.name}"
        )

    def _refresh_general(self) -> None:
        live = self._session_connection()
        metrics: dict = {}
        if live is not None:
            try:
                metrics = fetch_database_performance(live)
            except Exception:
                metrics = {}
        connected = bool(metrics.get("connected")) if live else False
        if connected:
            load = float(metrics.get("load_pct") or 0)
            if load >= 85:
                health = "Critical"
            elif load >= 70:
                health = "Warning"
            else:
                health = "Healthy"
            status = "Connected"
        elif live is not None:
            status = "Session present (metrics unavailable)"
            health = "Unknown"
        else:
            status = "Registered (not connected)"
            health = "Offline"
        self._set_props(
            self._prop_labels,
            {
                "name": self.server.name,
                "engine": (self.server.db_type or "").upper(),
                "version": str(metrics.get("version") or "—"),
                "host": self.server.host,
                "port": str(self.server.port),
                "status": status,
                "health": health,
                "uptime": str(metrics.get("uptime") or "—"),
            },
        )

    def _refresh_connection_tab(self) -> None:
        live = self._session_connection()
        status = "Connected" if live is not None else "Not connected"
        endpoint = (
            live.endpoint_label
            if live is not None
            else f"{self.server.host}:{self.server.port}"
        )
        self._set_props(
            self._conn_labels,
            {
                "host": self.server.host,
                "port": str(self.server.port),
                "username": self.server.username or "(none)",
                "auth": self._auth_method(),
                "status": status,
                "endpoint": endpoint,
            },
        )
        if hasattr(self, "_disconnect_btn"):
            state = "normal" if live is not None else "disabled"
            try:
                self._disconnect_btn.configure(state=state)
            except Exception:
                pass

    def _refresh_performance(self) -> None:
        live = self._session_connection()
        if live is None:
            self._set_props(
                self._perf_labels,
                {
                    "latency": "Not connected",
                    "connections": "—",
                    "slow": "—",
                    "tps": "—",
                },
            )
            if self._chart_update:
                self._chart_update([], "Connect this database to collect live samples.")
            return

        self._perf_pending = None
        self._perf_loading = True

        def worker() -> None:
            try:
                metrics = fetch_database_performance(live)
            except Exception:
                metrics = {"connected": False}
            self._perf_pending = metrics
            try:
                self.after(0, self._drain_perf_pending)
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._drain_perf_pending)

    def _drain_perf_pending(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        pending = getattr(self, "_perf_pending", None)
        if pending is not None:
            self._perf_pending = None
            self._perf_loading = False
            self._apply_performance(pending)
            return
        if getattr(self, "_perf_loading", False):
            self.after(120, self._drain_perf_pending)

    def _apply_performance(self, metrics: dict) -> None:
        if not self._alive or not self.winfo_exists():
            return
        connected = bool(metrics.get("connected"))
        throughput = metrics.get("throughput")
        tps_label = "—"
        if connected and throughput not in (None, "", 0, 0.0):
            label = str(metrics.get("throughput_label") or "Activity")
            try:
                tps_label = f"{float(throughput):,.0f} ({label})"
            except (TypeError, ValueError):
                tps_label = f"{throughput} ({label})"
        self._set_props(
            self._perf_labels,
            {
                "latency": str(metrics.get("latency") or "—") if connected else "Unavailable",
                "connections": str(metrics.get("connections") or "—") if connected else "—",
                "slow": str(metrics.get("slow_queries") or "—") if connected else "—",
                "tps": tps_label,
            },
        )
        if connected:
            self._perf_samples.append(
                {
                    "latency_ms": float(metrics.get("latency_ms") or 0),
                    "connections_num": float(metrics.get("connections_num") or 0),
                }
            )
        if self._chart_update:
            self._chart_update(list(self._perf_samples))

    def _schedule_perf_poll(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        if self._current_tab == "Performance" and self._session_connection():
            self._refresh_performance()
        self._poll_job = self.after(10000, self._schedule_perf_poll)

    def _on_test_connection(self) -> None:
        if self._busy_test:
            return
        self._busy_test = True
        self._test_result.configure(text="Testing connection…", text_color=self._c("muted"))
        conn = self._profile_connection()

        def worker() -> None:
            try:
                ok, message = test_connection(conn)
            except Exception as exc:
                ok, message = False, str(exc)
            try:
                self.after(0, lambda: self._finish_test(ok, message))
            except RuntimeError:
                self._test_pending = (ok, message)
                self._busy_test = False

        threading.Thread(target=worker, daemon=True).start()
        self.after(150, self._drain_test_pending)

    def _drain_test_pending(self) -> None:
        pending = getattr(self, "_test_pending", None)
        if pending is not None:
            self._test_pending = None
            self._finish_test(*pending)

    def _finish_test(self, ok: bool, message: str) -> None:
        self._busy_test = False
        if not self._alive or not self.winfo_exists():
            return
        color = self._c("ok") if ok else self._c("err")
        self._test_result.configure(text=message, text_color=color)
        self._set_status("Connection test succeeded" if ok else "Connection test failed")
        self._refresh_general()
        self._refresh_connection_tab()

    def _on_disconnect(self) -> None:
        if self._session_connection() is None:
            return
        if self.on_disconnect:
            self.on_disconnect()
        self._active = None
        self._test_result.configure(
            text="Disconnected.", text_color=self._c("muted")
        )
        self.refresh_all()
        self._set_status("Disconnected")

    def _goto(self, key: str) -> None:
        if self.on_navigate:
            self.on_navigate(key)
        self._set_status(f"Opened {key} workspace")

    def _table_row(
        self, parent, cells: list[tuple[str, int]], *, muted: bool = False
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent", height=24)
        row.pack(fill="x", pady=1)
        color = self._c("dim") if muted else self._c("text")
        for text, width in cells:
            ctk.CTkLabel(
                row,
                text=text,
                width=width,
                anchor="w",
                font=self._mono(10) if width > 200 else self._font(11),
                text_color=color,
            ).pack(side="left", padx=6)

    def _load_objects(self) -> None:
        host = self._objects_host
        if host is None or not host.winfo_exists():
            return
        for child in host.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            host,
            text="Loading…",
            font=self._font(11),
            text_color=self._c("muted"),
        ).pack(anchor="w", padx=8, pady=8)
        conn = self._session_connection()
        self._objects_loading = True
        self._objects_pending = None

        def worker() -> None:
            rows: list[tuple[str, str, str]] = []
            error = ""
            try:
                if conn is None:
                    error = "Connect this database to inspect tables and collections."
                elif conn.db_type == DatabaseType.MONGODB:
                    databases = fetch_server_databases(conn)
                    target = (conn.database or self.server.database or "admin").strip()
                    for db in databases:
                        name = str(db.get("name") or "")
                        size = str(db.get("size") or "—")
                        rows.append((name, "Database", size))
                    try:
                        overview = fetch_mongodb_database_overview(conn, target)
                        for coll in overview.get("collections") or []:
                            rows.append(
                                (
                                    f"  {target} / {coll.get('name')}",
                                    "Collection",
                                    f"{int(coll.get('count') or 0):,} docs",
                                )
                            )
                    except Exception as exc:
                        rows.append((target, "Database", f"(collections unavailable: {exc})"))
                else:
                    from desktop.explorer.factory import get_explorer_provider

                    provider = get_explorer_provider(conn)
                    for summary in provider.list_databases():
                        rows.append((summary.name, "Database", summary.size or "—"))
                        try:
                            schemas = provider.list_schemas(summary.name)
                        except Exception:
                            schemas = []
                        for schema in schemas[:40]:
                            rows.append((f"  {schema.name}", "Schema", "—"))
                            try:
                                tables = provider.list_objects(
                                    summary.name, schema.name, "tables"
                                )
                            except Exception:
                                tables = []
                            for table in tables[:80]:
                                count = getattr(table, "row_count", None) or getattr(
                                    table, "size", None
                                ) or "—"
                                rows.append(
                                    (
                                        f"    {schema.name}.{table.name}",
                                        "Table",
                                        str(count),
                                    )
                                )
            except Exception as exc:
                error = str(exc)
            self._objects_pending = (rows, error)
            try:
                self.after(0, self._drain_objects_pending)
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._drain_objects_pending)

    def _drain_objects_pending(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        pending = getattr(self, "_objects_pending", None)
        if pending is not None:
            self._objects_pending = None
            self._objects_loading = False
            self._render_objects(*pending)
            return
        if getattr(self, "_objects_loading", False):
            self.after(120, self._drain_objects_pending)

    def _render_objects(self, rows: list[tuple[str, str, str]], error: str) -> None:
        host = self._objects_host
        if host is None or not host.winfo_exists():
            return
        for child in host.winfo_children():
            child.destroy()
        if error and not rows:
            ctk.CTkLabel(
                host, text=error, font=self._font(11), text_color=self._c("warn"), anchor="w"
            ).pack(fill="x", padx=8, pady=8)
            return
        if error:
            ctk.CTkLabel(
                host, text=error, font=self._font(10), text_color=self._c("warn"), anchor="w"
            ).pack(fill="x", padx=8, pady=(4, 2))
        if not rows:
            ctk.CTkLabel(
                host,
                text="No objects returned.",
                font=self._font(11),
                text_color=self._c("muted"),
            ).pack(anchor="w", padx=8, pady=8)
            return
        for name, kind, count in rows:
            self._table_row(
                host,
                [(name, 280), (kind, 120), (count, 160)],
                muted=kind in {"Schema"},
            )

    def _load_backups(self) -> None:
        host = self._backup_host
        if host is None or not host.winfo_exists():
            return
        for child in host.winfo_children():
            child.destroy()
        rows: list[tuple[str, str, str]] = []
        try:
            backups = BackupService().list_backups()
        except Exception:
            backups = []
        for item in backups[:40]:
            ts = (
                item.get("created_at")
                or item.get("timestamp")
                or item.get("mtime")
                or "—"
            )
            name = (
                item.get("name")
                or item.get("filename")
                or item.get("path")
                or "—"
            )
            status = item.get("status") or item.get("state") or "available"
            rows.append((str(ts), str(name), str(status)))
        last = rows[0][0] if rows else "—"
        if hasattr(self, "_backup_status"):
            self._backup_status.configure(
                text=(
                    f"Last backup: {last}\n"
                    f"Local catalog entries: {len(backups)}"
                )
            )
        if not rows:
            # Fall back to audit timeline for backup actions.
            for event in self.monitoring.get_recent_activity(limit=40):
                action = str(event.get("action") or "")
                if "backup" not in action.lower():
                    continue
                rows.append(
                    (
                        str(event.get("time") or "—"),
                        str(event.get("resource") or action),
                        str(event.get("status") or "—"),
                    )
                )
        if not rows:
            ctk.CTkLabel(
                host,
                text="No backup history found for this profile.",
                font=self._font(11),
                text_color=self._c("muted"),
            ).pack(anchor="w", padx=8, pady=8)
            return
        for ts, name, status in rows:
            self._table_row(host, [(ts, 150), (name, 320), (status, 100)])

    def _load_logs(self) -> None:
        host = self._logs_host
        if host is None or not host.winfo_exists():
            return
        for child in host.winfo_children():
            child.destroy()
        level = (self._log_filter.get() or "ALL").upper()
        rows: list[tuple[str, str, str, str]] = []

        if self.log_store is not None:
            for entry in reversed(self.log_store.entries[-200:]):
                if level != "ALL" and entry.level != level:
                    continue
                rows.append((entry.timestamp, "runtime", entry.level, entry.message))

        try:
            status_map = {
                "FAILED": "ERROR",
                "FAILURE": "ERROR",
                "ERROR": "ERROR",
                "SUCCESS": "SUCCESS",
                "OK": "SUCCESS",
                "WARNING": "WARNING",
                "WARN": "WARNING",
                "INFO": "INFO",
            }
            for event in self.monitoring.get_recent_activity(limit=80):
                raw_status = str(event.get("status") or "info").upper()
                mapped = status_map.get(raw_status, "INFO")
                if level != "ALL" and mapped != level:
                    continue
                rows.append(
                    (
                        str(event.get("time") or "—"),
                        str(event.get("action") or "event"),
                        str(event.get("status") or "—"),
                        str(event.get("resource") or event.get("user") or ""),
                    )
                )
        except Exception:
            pass

        if not rows:
            ctk.CTkLabel(
                host,
                text="No log entries match the current filter.",
                font=self._font(11),
                text_color=self._c("muted"),
            ).pack(anchor="w", padx=8, pady=8)
            return

        for ts, op, result, details in rows[:120]:
            self._table_row(
                host,
                [(ts, 90), (op, 120), (result, 90), (details[:120], 360)],
            )

    def _close(self) -> None:
        self._alive = False
        job = self._poll_job
        self._poll_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        key = self.server.name.strip().lower()
        _OPEN_WINDOWS.pop(key, None)
        self.destroy()
