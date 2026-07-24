"""Database Performance dashboard widgets with charts."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.dashboard_widgets import analytics_card
from desktop.design_system import depth_card, font
from desktop.theme import ThemeManager


def _kpi_tile(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    icon: str,
    title: str,
    color_key: str,
) -> ctk.CTkLabel:
    if theme.is_light:
        shell, card = depth_card(parent, theme, radius="lg")
        shell.pack(side="left", fill="both", expand=True, padx=5)
    else:
        card = ctk.CTkFrame(
            parent,
            corner_radius=theme.radius("lg"),
            fg_color=theme.c("bg_glass"),
            border_width=0,
        )
        card.pack(side="left", fill="both", expand=True, padx=5)

    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=16, pady=14)

    top = ctk.CTkFrame(inner, fg_color="transparent")
    top.pack(fill="x")
    icon_bg = ctk.CTkFrame(top, width=28, height=28, corner_radius=8, fg_color=theme.c("bg_elevated"))
    icon_bg.pack(side="left")
    icon_bg.pack_propagate(False)
    ctk.CTkLabel(icon_bg, text=icon, font=font(theme, 14), text_color=theme.c(color_key)).place(
        relx=0.5, rely=0.5, anchor="center"
    )
    ctk.CTkLabel(
        top,
        text=title.upper(),
        font=font(theme, 9, weight="bold"),
        text_color=theme.c("text_muted"),
    ).pack(side="right")

    value = ctk.CTkLabel(
        inner,
        text="—",
        font=font(theme, 22, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value.pack(anchor="w", pady=(10, 2))

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
    last_sig: list[tuple | None] = [None]

    def update(series: list[dict]) -> None:
        sig = tuple(
            tuple(pt.get(k, 0) for k in keys) + (pt.get("label", ""),)
            for pt in series
        )
        if sig == last_sig[0]:
            return
        last_sig[0] = sig
        canvas.delete("all")
        if not series:
            canvas.create_text(80, height / 2, text="Connect a database", fill=theme.c("text_muted"), font=("Segoe UI", 10))
            return
        w = max(canvas.winfo_width(), 240)
        h = height
        pad_l, pad_r, pad_t, pad_b = 24, 8, 10, 22
        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b
        max_y = max(1.0, max(max(d.get(k, 0) for k in keys) for d in series))
        n = len(series)
        label_stride = max(1, n // 6)
        xs = [pad_l + (chart_w * i / max(n - 1, 1)) for i in range(n)]
        for key, color in zip(keys, colors):
            points: list[float] = []
            for i, pt in enumerate(series):
                val = float(pt.get(key, 0))
                y = pad_t + chart_h - (val / max_y) * chart_h
                points.extend([xs[i], y])
                canvas.create_oval(xs[i] - 2, y - 2, xs[i] + 2, y + 2, fill=color, outline=color)
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2, smooth=True)
        for i, pt in enumerate(series):
            if i % label_stride == 0 or i == n - 1:
                canvas.create_text(xs[i], h - 6, text=pt.get("label", ""), fill=theme.c("text_muted"), font=("Segoe UI", 7))

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
            border_width=0,
        )
        cell.pack(side="left", fill="x", expand=True, padx=2)
        ctk.CTkLabel(cell, text=label, font=font(theme, 9), text_color=theme.c("text_muted")).pack(anchor="w", padx=8, pady=(6, 0))
        val = ctk.CTkLabel(cell, text="—", font=font(theme, 13, weight="bold"), text_color=theme.c(color))
        val.pack(anchor="w", padx=8, pady=(2, 8))
        refs[key] = val
    return refs


def _backup_donut(parent: ctk.CTkFrame, theme: ThemeManager, *, height: int = 140) -> Callable[[dict], None]:
    canvas = tk.Canvas(
        parent,
        height=height,
        bg=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(side="left", fill="both", expand=True)

    legend = ctk.CTkFrame(parent, fg_color="transparent")
    legend.pack(side="left", fill="y", padx=(8, 0))
    legend_labels: list[ctk.CTkLabel] = []
    for name in ("Successful", "Failed", "Skipped"):
        lbl = ctk.CTkLabel(legend, text="", font=font(theme, 10), anchor="w")
        lbl.pack(anchor="w", pady=2)
        legend_labels.append(lbl)
    last_sig: list[tuple | None] = [None]

    def update(data: dict) -> None:
        ok = int(data.get("successful", 0))
        fail = int(data.get("failed", 0))
        skip = int(data.get("skipped", 0))
        total = ok + fail + skip
        rate = float(data.get("success_rate", 0))
        sig = (ok, fail, skip, round(rate, 1))
        if sig == last_sig[0]:
            return
        last_sig[0] = sig
        canvas.delete("all")
        cx, cy = height * 0.45, height / 2
        outer, inner = height * 0.38, height * 0.22
        if total <= 0:
            canvas.create_oval(cx - outer, cy - outer, cx + outer, cy + outer, fill=theme.c("bg_elevated"), outline="")
        else:
            start = 90
            for val, color in [(ok, theme.c("success")), (fail, theme.c("error")), (skip, theme.c("text_muted"))]:
                if val <= 0:
                    continue
                extent = -360 * (val / total)
                canvas.create_arc(cx - outer, cy - outer, cx + outer, cy + outer, start=start, extent=extent, fill=color, outline="")
                start += extent
        canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=theme.c("bg_card"), outline="")
        canvas.create_text(cx, cy - 4, text=f"{rate:.1f}%", font=("Segoe UI", 14, "bold"), fill=theme.c("text_primary"))
        canvas.create_text(cx, cy + 12, text="Success Rate", font=("Segoe UI", 8), fill=theme.c("text_muted"))
        for lbl, (name, count, color) in zip(
            legend_labels,
            [("Successful", ok, theme.c("success")), ("Failed", fail, theme.c("error")), ("Skipped", skip, theme.c("text_muted"))],
        ):
            pct = f"{count / total * 100:.1f}%" if total else "0%"
            lbl.configure(text=f"{name}  {count}  ({pct})", text_color=color)

    return update


def _backup_trend(parent: ctk.CTkFrame, theme: ThemeManager) -> Callable[[list[dict]], None]:
    return _multi_line_chart(
        parent,
        theme,
        height=100,
        series_specs=[
            ("successful", "Successful", "success"),
            ("failed", "Failed", "error"),
            ("skipped", "Skipped", "text_muted"),
        ],
    )


def _load_gauge(parent: ctk.CTkFrame, theme: ThemeManager, *, height: int = 120) -> Callable[[float], None]:
    canvas = tk.Canvas(
        parent,
        height=height,
        bg=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True)
    label = ctk.CTkLabel(parent, text="Current Load", font=font(theme, 10), text_color=theme.c("text_muted"))
    label.pack(pady=(0, 4))
    last_pct: list[float | None] = [None]

    def update(pct: float) -> None:
        rounded = round(pct)
        if last_pct[0] == rounded:
            return
        last_pct[0] = rounded
        canvas.delete("all")
        w = max(canvas.winfo_width(), 120)
        cx, cy = w / 2, height - 10
        r = min(w / 2 - 10, height - 20)
        canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=180, extent=180, fill=theme.c("bg_elevated"), outline="")
        color = theme.c("success") if pct < 60 else (theme.c("warning") if pct < 85 else theme.c("error"))
        canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=180, extent=-180 * (pct / 100), fill=color, outline="")
        canvas.create_text(cx, cy - r / 2, text=f"{pct:.0f}%", font=("Segoe UI", 16, "bold"), fill=theme.c("text_primary"))

    return update


def _conn_donut(parent: ctk.CTkFrame, theme: ThemeManager, *, height: int = 130) -> Callable[[dict], None]:
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
    legend_rows: list[tuple[ctk.CTkFrame, ctk.CTkLabel]] = []
    for _ in range(4):
        row = ctk.CTkFrame(legend, fg_color="transparent")
        row.pack(fill="x", pady=1)
        lbl = ctk.CTkLabel(row, text="", font=font(theme, 10), text_color=theme.c("text_secondary"))
        lbl.pack(side="left")
        legend_rows.append((row, lbl))
    last_sig: list[tuple | None] = [None]

    def update(dist: dict) -> None:
        specs = [
            ("active", "Active", "success"),
            ("idle", "Idle", "primary"),
            ("blocked", "Blocked", "warning"),
            ("waiting", "Waiting", "error"),
        ]
        sig = tuple(int(dist.get(k, 0)) for k, _, _ in specs)
        if sig == last_sig[0]:
            return
        last_sig[0] = sig
        canvas.delete("all")
        total = sum(int(dist.get(k, 0)) for k, _, _ in specs) or 1
        cx, cy = height * 0.42, height / 2
        outer, inner = height * 0.36, height * 0.22
        start = 90
        for key, _, color_key in specs:
            val = int(dist.get(key, 0))
            if val <= 0:
                continue
            extent = -360 * (val / total)
            canvas.create_arc(cx - outer, cy - outer, cx + outer, cy + outer, start=start, extent=extent, fill=theme.c(color_key), outline="")
            start += extent
        canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=theme.c("bg_card"), outline="")
        canvas.create_text(cx, cy - 2, text=str(total), font=("Segoe UI", 14, "bold"), fill=theme.c("text_primary"))
        canvas.create_text(cx, cy + 14, text="Total", font=("Segoe UI", 8), fill=theme.c("text_muted"))
        for (row, lbl), (key, name, color_key) in zip(legend_rows, specs):
            val = int(dist.get(key, 0))
            pct = f"{val / total * 100:.0f}%" if total else "0%"
            row.pack(fill="x", pady=1)
            lbl.configure(text=f"{name}  {val}  ({pct})")

    return update


def build_database_performance_section(parent: ctk.CTkFrame, theme: ThemeManager) -> dict:
    """Build Database Performance layout matching enterprise dashboard design."""
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

    # ── Middle row ──
    mid = ctk.CTkFrame(parent, fg_color="transparent")
    mid.pack(fill="x", pady=(0, 12))
    mid.grid_columnconfigure(0, weight=3)
    mid.grid_columnconfigure(1, weight=2)

    perf_card, perf_body = analytics_card(mid, theme, "Performance Metrics (Real-time)", accent=theme.c("metric_purple"))
    perf_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
    refs["update_perf_chart"] = _multi_line_chart(
        perf_body,
        theme,
        height=150,
        series_specs=[
            ("latency", "Latency (ms)", "metric_purple"),
            ("connections", "Connections", "metric_blue"),
            ("slow", "Slow queries", "metric_amber"),
            ("replication", "Repl. lag (s)", "metric_green"),
        ],
    )
    refs["sparklines"] = _sparkline_row(perf_body, theme)

    bak_card, bak_body = analytics_card(mid, theme, "Backup Analytics", accent=theme.c("metric_green"))
    bak_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)
    ctk.CTkLabel(bak_body, text="Last 7 Days", font=font(theme, 10), text_color=theme.c("text_muted")).pack(anchor="e")
    bak_charts = ctk.CTkFrame(bak_body, fg_color="transparent")
    bak_charts.pack(fill="x")
    refs["update_backup_donut"] = _backup_donut(bak_charts, theme)
    refs["update_backup_trend"] = _backup_trend(bak_body, theme)
    bak_footer = ctk.CTkFrame(bak_body, fg_color="transparent")
    bak_footer.pack(fill="x", pady=(8, 0))
    refs["bak_total"] = ctk.CTkLabel(bak_footer, text="Total: —", font=font(theme, 10), text_color=theme.c("metric_purple"))
    refs["bak_total"].pack(side="left", padx=4)
    refs["bak_failed"] = ctk.CTkLabel(bak_footer, text="Failed: —", font=font(theme, 10), text_color=theme.c("error"))
    refs["bak_failed"].pack(side="left", padx=4)
    refs["bak_storage"] = ctk.CTkLabel(bak_footer, text="Storage: —", font=font(theme, 10), text_color=theme.c("primary"))
    refs["bak_storage"].pack(side="left", padx=4)

    # ── Bottom row ──
    bot = ctk.CTkFrame(parent, fg_color="transparent")
    bot.pack(fill="x")
    for c in range(3):
        bot.grid_columnconfigure(c, weight=1, uniform="db_bot")

    slow_card, slow_body = analytics_card(bot, theme, "Top Slow Queries", accent=theme.c("metric_amber"))
    slow_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)
    hdr = ctk.CTkFrame(slow_body, fg_color="transparent")
    hdr.pack(fill="x")
    ctk.CTkLabel(hdr, text="Query", font=font(theme, 9, weight="bold"), text_color=theme.c("text_muted"), anchor="w").pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(hdr, text="Elapsed ms", font=font(theme, 9, weight="bold"), text_color=theme.c("text_muted"), width=50).pack(side="left")
    ctk.CTkLabel(hdr, text="Cnt", font=font(theme, 9, weight="bold"), text_color=theme.c("text_muted"), width=30, anchor="e").pack(side="right")
    refs["slow_frame"] = ctk.CTkFrame(slow_body, fg_color="transparent")
    refs["slow_frame"].pack(fill="both", expand=True, pady=(6, 0))

    load_card, load_body = analytics_card(bot, theme, "Database Load", accent=theme.c("metric_blue"))
    load_card.grid(row=0, column=1, sticky="nsew", padx=3, pady=4)
    refs["update_load"] = _load_gauge(load_body, theme)
    refs["load_cpu"] = ctk.CTkLabel(load_body, text="Connection pool: —", font=font(theme, 10), text_color=theme.c("text_muted"))
    refs["load_cpu"].pack(anchor="w", pady=2)
    refs["load_mem"] = ctk.CTkLabel(load_body, text="Slow queries: —", font=font(theme, 10), text_color=theme.c("text_muted"))
    refs["load_mem"].pack(anchor="w", pady=2)

    conn_card, conn_body = analytics_card(bot, theme, "Connection Distribution", accent=theme.c("metric_purple"))
    conn_card.grid(row=0, column=2, sticky="nsew", padx=(6, 0), pady=4)
    refs["update_conn_donut"] = _conn_donut(conn_body, theme)

    return refs
