"""Database Performance Analytics — Beacon-style metrics + dual-line chart."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.design_system import font
from desktop.theme import ThemeManager


def _multi_line_chart(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    height: int = 160,
    series_specs: list[tuple[str, str, str]],
) -> Callable[[list[dict] | None, str | None], None]:
    chart_shell = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=theme.radius("sm"),
        border_width=0,
    )
    chart_shell.pack(fill="both", expand=True)

    canvas = tk.Canvas(
        chart_shell,
        height=height,
        bg=theme.c("bg_elevated"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True, padx=8, pady=(8, 4))

    foot = ctk.CTkFrame(chart_shell, fg_color="transparent")
    foot.pack(fill="x", padx=10, pady=(0, 8))
    note = ctk.CTkLabel(
        foot,
        text="",
        font=font(theme, 9),
        text_color=theme.c("text_muted"),
        anchor="w",
    )
    note.pack(side="left")

    keys = [k for k, _, _ in series_specs]
    colors = [theme.c(c) for _, _, c in series_specs]
    last_series: list[dict] = []
    empty_message = "Illustrative chart only – actual telemetry required."
    redraw_job: str | None = None
    drawn_width = -1

    def update(series: list[dict] | None = None, message: str | None = None) -> None:
        nonlocal last_series, empty_message
        if series is not None:
            last_series = list(series)
        if message is not None:
            empty_message = message
        canvas.delete("all")
        w = max(canvas.winfo_width(), 480)
        h = height
        pad_l, pad_r, pad_t, pad_b = 12, 12, 12, 16
        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b

        grid = theme.c("border")
        for i in range(4):
            y = pad_t + chart_h * i / 3
            canvas.create_line(pad_l, y, w - pad_r, y, fill=grid, width=1)

        if not last_series:
            note.configure(text=empty_message or "Illustrative chart only – actual telemetry required.")
            return

        note.configure(text="")
        max_y = max(1.0, max(max(float(d.get(k, 0) or 0) for k in keys) for d in last_series))
        n = len(last_series)
        xs = [pad_l + (chart_w * i / max(n - 1, 1)) for i in range(n)]
        for key, color in zip(keys, colors):
            points: list[float] = []
            for i, pt in enumerate(last_series):
                val = float(pt.get(key, 0) or 0)
                y = pad_t + chart_h - (val / max_y) * chart_h
                points.extend([xs[i], y])
            if len(points) >= 4:
                width = 2.5 if key == keys[0] else 1.5
                canvas.create_line(*points, fill=color, width=width, smooth=True)

    def _on_configure(event=None) -> None:
        nonlocal redraw_job, drawn_width
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


def build_database_performance_section(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    on_view_all: Callable[[], None] | None = None,
) -> dict:
    """Beacon performance card: value-over-label metrics + dual-line chart."""
    refs: dict = {}

    shell = ctk.CTkFrame(
        parent,
        fg_color=theme.c("surface"),
        corner_radius=theme.radius("md"),
        border_width=1,
        border_color=theme.c("border"),
    )
    shell.pack(fill="both", expand=True, pady=(0, 10))
    body = ctk.CTkFrame(shell, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=18, pady=16)

    header = ctk.CTkFrame(body, fg_color="transparent")
    header.pack(fill="x", pady=(0, 14))
    ctk.CTkLabel(
        header,
        text="DATABASE PERFORMANCE ANALYTICS",
        font=font(theme, 13, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(side="left")
    if on_view_all:
        view_all = ctk.CTkLabel(
            header,
            text="View all →",
            font=font(theme, 12),
            text_color=theme.c("primary"),
            cursor="hand2",
        )
        view_all.pack(side="right")
        view_all.bind("<Button-1>", lambda _e: on_view_all())

    metrics = ctk.CTkFrame(body, fg_color="transparent")
    metrics.pack(fill="x", pady=(0, 14))
    for col in range(4):
        metrics.grid_columnconfigure(col, weight=1, uniform="perfkpi")

    sparks: dict[str, ctk.CTkLabel] = {}
    for i, (key, label) in enumerate(
        [
            ("latency", "Latency"),
            ("connections", "Connections"),
            ("slow", "Slow queries"),
            ("throughput", "TPS"),
        ]
    ):
        cell = ctk.CTkFrame(metrics, fg_color="transparent")
        cell.grid(row=0, column=i, sticky="w", padx=(0 if i == 0 else 8, 0))
        val = ctk.CTkLabel(
            cell,
            text="—",
            font=font(theme, 22, weight="bold"),
            text_color=theme.c("text_primary"),
            anchor="w",
        )
        val.pack(anchor="w")
        ctk.CTkLabel(
            cell,
            text=label,
            font=font(theme, 11),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w")
        sparks[key] = val
    refs["sparklines"] = sparks

    refs["update_perf_chart"] = _multi_line_chart(
        body,
        theme,
        height=160,
        series_specs=[
            ("latency", "Query latency", "primary"),
            ("connections", "Active connections", "text_muted"),
        ],
    )

    return refs
