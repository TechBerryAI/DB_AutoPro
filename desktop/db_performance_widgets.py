"""Database Performance dashboard widgets with charts."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.dashboard_widgets import analytics_card
from desktop.design_system import font
from desktop.theme import ThemeManager


def _kpi_tile(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    icon: str,
    title: str,
    color_key: str,
) -> ctk.CTkLabel:
    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("md"),
        fg_color=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    card.pack(side="left", fill="both", expand=True, padx=4)

    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=14, pady=12)

    top = ctk.CTkFrame(inner, fg_color="transparent")
    top.pack(fill="x")
    ctk.CTkLabel(top, text=icon, font=font(theme, 16), text_color=theme.c(color_key)).pack(side="left")
    ctk.CTkLabel(
        top,
        text=title,
        font=font(theme, 10, weight="bold"),
        text_color=theme.c("text_muted"),
    ).pack(side="right")

    value = ctk.CTkLabel(
        inner,
        text="—",
        font=font(theme, 18, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value.pack(anchor="w", pady=(8, 2))

    subtitle = ctk.CTkLabel(
        inner,
        text="",
        font=font(theme, 10),
        text_color=theme.c("text_muted"),
        anchor="w",
    )
    subtitle.pack(anchor="w")
    value._subtitle = subtitle  # type: ignore[attr-defined]
    return value


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
    last_sig: list[tuple | None] = [None]

    def update(series: list[dict] | None = None) -> None:
        nonlocal last_series
        if series is not None:
            sig = tuple(
                tuple(pt.get(k, 0) for k in keys) + (pt.get("label", ""),)
                for pt in series
            )
            if sig == last_sig[0]:
                return
            last_sig[0] = sig
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
        canvas.update_idletasks()
        w = max(canvas.winfo_width(), 640)
        h = height
        pad_l, pad_r, pad_t, pad_b = 28, 12, 12, 24
        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b
        max_y = max(1.0, max(max(d.get(k, 0) for k in keys) for d in last_series))
        n = len(last_series)
        label_stride = max(1, n // 6)
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
            if i % label_stride == 0 or i == n - 1:
                canvas.create_text(
                    xs[i],
                    h - 6,
                    text=pt.get("label", ""),
                    fill=theme.c("text_muted"),
                    font=("Segoe UI", 7),
                )

    def _on_configure(_event=None) -> None:
        if last_series:
            # Force redraw on resize even when series data is unchanged.
            last_sig[0] = None
            update(last_series)

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
    """Build Database Performance: KPI strip + full-width real-time chart."""
    refs: dict = {}

    # ── KPI row ──
    kpi_row = ctk.CTkFrame(parent, fg_color="transparent")
    kpi_row.pack(fill="x", pady=(0, 12))
    refs["kpi_status"] = _kpi_tile(kpi_row, theme, icon="🛡", title="Status", color_key="success")
    refs["kpi_status"]._subtitle.configure(text="Connection health")  # type: ignore[attr-defined]
    refs["kpi_uptime"] = _kpi_tile(kpi_row, theme, icon="◷", title="Uptime", color_key="metric_blue")
    refs["kpi_uptime"]._subtitle.configure(text="Database server")  # type: ignore[attr-defined]
    refs["kpi_connections"] = _kpi_tile(kpi_row, theme, icon="◎", title="Total Connections", color_key="metric_purple")
    refs["kpi_connections"]._subtitle.configure(text="Active / Max")  # type: ignore[attr-defined]
    refs["kpi_latency"] = _kpi_tile(kpi_row, theme, icon="◉", title="DB Response Time", color_key="metric_amber")
    refs["kpi_latency"]._subtitle.configure(text="Round-trip ping")  # type: ignore[attr-defined]
    refs["kpi_throughput"] = _kpi_tile(kpi_row, theme, icon="⇅", title="Activity", color_key="primary")
    refs["kpi_throughput"]._subtitle.configure(text="Lifetime counter")  # type: ignore[attr-defined]

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