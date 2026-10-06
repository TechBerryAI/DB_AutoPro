"""Database Performance dashboard widgets with charts."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.dashboard_widgets import analytics_card
from desktop.design_system import font
from desktop.theme import ThemeManager


def _multi_line_chart(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    height: int = 160,
    series_specs: list[tuple[str, str, str]],
) -> Callable[[list[dict]], None]:
    canvas = tk.Canvas(
        parent,
        height=height,
        bg=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True)

    legend = ctk.CTkFrame(parent, fg_color="transparent")
    legend.pack(fill="x", pady=(4, 0))
    for label, color_key in [(lbl, ck) for _, lbl, ck in series_specs]:
        row = ctk.CTkFrame(legend, fg_color="transparent")
        row.pack(side="left", padx=(0, 12))
        ctk.CTkFrame(row, width=8, height=8, corner_radius=4, fg_color=theme.c(color_key)).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(row, text=label, font=font(theme, 9), text_color=theme.c("text_muted")).pack(side="left")

    keys = [k for k, _, _ in series_specs]
    colors = [theme.c(c) for _, _, c in series_specs]
    last_series: list[dict] = []
    redraw_job: str | None = None
    drawn_width = -1

    def update(series: list[dict] | None = None) -> None:
        nonlocal last_series
        if series is not None:
            last_series = list(series)
        canvas.delete("all")
        if not last_series:
            canvas.create_text(
                120,
                height / 2,
                text="Connect a database",
                fill=theme.c("text_muted"),
                font=("Segoe UI", 10),
            )
            return
        w = max(canvas.winfo_width(), 640)
        h = height
        pad_l, pad_r, pad_t, pad_b = 28, 12, 12, 24
        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b
        max_y = max(1.0, max(max(d.get(k, 0) for k in keys) for d in last_series))
        n = len(last_series)
        xs = [pad_l + (chart_w * i / max(n - 1, 1)) for i in range(n)]
        for key, color in zip(keys, colors):
            points: list[float] = []
            for i, pt in enumerate(last_series):
                val = float(pt.get(key, 0))
                y = pad_t + chart_h - (val / max_y) * chart_h
                points.extend([xs[i], y])
                canvas.create_oval(xs[i] - 2, y - 2, xs[i] + 2, y + 2, fill=color, outline=color)
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2, smooth=True)
        for i, pt in enumerate(last_series):
            canvas.create_text(
                xs[i],
                h - 6,
                text=pt.get("label", ""),
                fill=theme.c("text_muted"),
                font=("Segoe UI", 7),
            )

    def _on_configure(event=None) -> None:
        nonlocal redraw_job, drawn_width
        if not last_series:
            return
        width = getattr(event, "width", 0) or 0
        if width <= 1 or width == drawn_width:
            return
        drawn_width = width
        if redraw_job is not None:
            try:
                canvas.after_cancel(redraw_job)
            except Exception:
                pass
        redraw_job = canvas.after(80, update)

    canvas.bind("<Configure>", _on_configure)
    return update


def _sparkline_row(parent: ctk.CTkFrame, theme: ThemeManager) -> dict[str, ctk.CTkLabel]:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(8, 0))
    refs: dict[str, ctk.CTkLabel] = {}
    for key, label, color in [
        ("latency", "Query Latency", "metric_purple"),
        ("connections", "Active Connections", "metric_blue"),
        ("slow", "Slow Queries", "metric_amber"),
        ("replication", "Replication Lag", "metric_green"),
    ]:
        cell = ctk.CTkFrame(
            row,
            fg_color=theme.c("bg_elevated"),
            corner_radius=theme.radius("sm"),
            border_width=1,
            border_color=theme.c("border_subtle"),
        )
        cell.pack(side="left", fill="x", expand=True, padx=2)
        ctk.CTkLabel(cell, text=label, font=font(theme, 9), text_color=theme.c("text_muted")).pack(anchor="w", padx=8, pady=(6, 0))
        val = ctk.CTkLabel(cell, text="—", font=font(theme, 13, weight="bold"), text_color=theme.c(color))
        val.pack(anchor="w", padx=8, pady=(2, 8))
        refs[key] = val
    return refs


def build_database_performance_section(parent: ctk.CTkFrame, theme: ThemeManager) -> dict:
    """Build Database Performance: full-width real-time chart + sparkline row.

    Used to also render a 5-tile KPI strip (status/uptime/connections/latency/
    activity), but every one of those numbers already appears in the "Database
    Activity" panel higher up the dashboard — this section's actual unique value is
    the real-time chart and sparklines, so the redundant tile strip was dropped
    (docs/ui_plan.md §6). dashboard.py's _apply_db_performance_dashboard() already
    guards each of these refs with `.get(...)` + `if lbl:`, so leaving those keys
    absent from `refs` is a no-op there, not a break.
    """
    refs: dict = {}

    # ── Full-width performance chart ──
    perf_card, perf_body = analytics_card(
        parent,
        theme,
        "Performance Metrics (Real-time)",
        accent=theme.c("metric_purple"),
    )
    perf_card.pack(fill="both", expand=True, pady=4)
    refs["update_perf_chart"] = _multi_line_chart(
        perf_body,
        theme,
        height=280,
        series_specs=[
            ("latency", "Latency (ms)", "metric_purple"),
            ("connections", "Connections", "metric_blue"),
            ("slow", "Slow queries", "metric_amber"),
            ("replication", "Repl. lag (s)", "metric_green"),
        ],
    )
    refs["sparklines"] = _sparkline_row(perf_body, theme)

    return refs