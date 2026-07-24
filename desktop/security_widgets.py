"""Security & Alerts dashboard widgets with charts."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.dashboard_widgets import analytics_card
from desktop.design_system import font, status_chip
from desktop.theme import ThemeManager

_SEVERITY_COLORS = {
    "critical": "error",
    "warning": "warning",
    "info": "primary",
    "success": "success",
}

_SEVERITY_ICONS = {
    "critical": "🛡",
    "warning": "⚠",
    "info": "ℹ",
}


def security_posture_row(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
    tone: str,
) -> ctk.CTkLabel:
    """Posture metric with colored accent bar and value chip."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=8)

    accent_color = theme.c(_SEVERITY_COLORS.get(tone, "text_muted"))
    ctk.CTkFrame(row, width=4, height=36, corner_radius=2, fg_color=accent_color).pack(side="left", padx=(0, 12))

    ctk.CTkLabel(
        row,
        text=label,
        font=font(theme, 12),
        text_color=theme.c("text_secondary"),
        anchor="w",
    ).pack(side="left", fill="x", expand=True)

    chip = status_chip(row, theme, text="—", tone=tone if tone in ("error", "warning", "success", "primary") else "neutral")
    chip.pack(side="right")
    return chip


def alerts_donut_chart(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    height: int = 180,
) -> Callable[[dict], None]:
    """Donut chart for critical / warning / info alert breakdown."""
    canvas = tk.Canvas(
        parent,
        height=height,
        bg=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True, pady=(4, 0))

    legend = ctk.CTkFrame(parent, fg_color="transparent")
    legend.pack(fill="x", pady=(8, 0))

    def update(data: dict) -> None:
        canvas.delete("all")
        for child in legend.winfo_children():
            child.destroy()

        critical = int(data.get("critical", 0))
        warning = int(data.get("warning", 0))
        info = int(data.get("info", 0))
        total = int(data.get("total", critical + warning + info))

        canvas.update_idletasks()
        w = max(canvas.winfo_width(), 160)
        h = height
        cx, cy = w * 0.38, h / 2
        outer = min(w, h) * 0.38
        inner = outer * 0.58

        slices = [
            (critical, theme.c("error")),
            (warning, theme.c("warning")),
            (info, theme.c("primary")),
        ]
        values = [v for v, _ in slices if v > 0]
        if not values:
            canvas.create_oval(cx - outer, cy - outer, cx + outer, cy + outer, fill=theme.c("bg_elevated"), outline="")
            canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=theme.c("bg_card"), outline="")
            canvas.create_text(cx, cy - 6, text="0", fill=theme.c("text_primary"), font=("Segoe UI", 18, "bold"))
            canvas.create_text(cx, cy + 14, text="Total Alerts", fill=theme.c("text_muted"), font=("Segoe UI", 9))
        else:
            start = 90
            total_val = sum(values) or 1
            for value, color in slices:
                if value <= 0:
                    continue
                extent = -360 * (value / total_val)
                canvas.create_arc(
                    cx - outer, cy - outer, cx + outer, cy + outer,
                    start=start, extent=extent, fill=color, outline="",
                )
                start += extent
            canvas.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=theme.c("bg_card"), outline="")
            canvas.create_text(cx, cy - 6, text=str(total), fill=theme.c("text_primary"), font=("Segoe UI", 18, "bold"))
            canvas.create_text(cx, cy + 14, text="Total Alerts", fill=theme.c("text_muted"), font=("Segoe UI", 9))

        specs = [
            ("Critical", critical, theme.c("error")),
            ("Warning", warning, theme.c("warning")),
            ("Info", info, theme.c("primary")),
        ]
        for name, count, color in specs:
            pct = f"{int(count / total * 100)}%" if total else "0%"
            row = ctk.CTkFrame(legend, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkFrame(row, width=8, height=8, corner_radius=4, fg_color=color).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(row, text=f"{name}  {count}  ({pct})", font=font(theme, 11), text_color=theme.c("text_secondary"), anchor="w").pack(side="left")

    return update


def alerts_trend_chart(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    height: int = 180,
) -> Callable[[list[dict]], None]:
    """Multi-line trend chart for alert severity over time."""
    canvas = tk.Canvas(
        parent,
        height=height,
        bg=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True, pady=(4, 0))

    legend = ctk.CTkFrame(parent, fg_color="transparent")
    legend.pack(fill="x", pady=(6, 0))
    for label, key in [("Critical", "error"), ("Warning", "warning"), ("Info", "primary")]:
        row = ctk.CTkFrame(legend, fg_color="transparent")
        row.pack(side="left", padx=(0, 14))
        ctk.CTkFrame(row, width=8, height=8, corner_radius=4, fg_color=theme.c(key)).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(row, text=label, font=font(theme, 10), text_color=theme.c("text_muted")).pack(side="left")

    def update(series: list[dict]) -> None:
        canvas.delete("all")
        if not series:
            return

        canvas.update_idletasks()
        w = max(canvas.winfo_width(), 280)
        h = height
        pad_l, pad_r, pad_t, pad_b = 28, 12, 14, 28
        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b

        max_y = max(
            1,
            max((d.get("critical", 0) for d in series), default=0),
            max((d.get("warning", 0) for d in series), default=0),
            max((d.get("info", 0) for d in series), default=0),
        )
        if max_y < 5:
            max_y = 5

        for i in range(5):
            y = pad_t + chart_h - (chart_h * i / 4)
            canvas.create_line(pad_l, y, w - pad_r, y, fill=theme.c("border_subtle"))
            canvas.create_text(pad_l - 4, y, text=str(int(max_y * i / 4)), anchor="e", fill=theme.c("text_muted"), font=("Segoe UI", 8))

        n = len(series)
        if n == 1:
            xs = [pad_l + chart_w / 2]
        else:
            xs = [pad_l + (chart_w * i / (n - 1)) for i in range(n)]

        lines = [
            ("critical", theme.c("error")),
            ("warning", theme.c("warning")),
            ("info", theme.c("primary")),
        ]

        for key, color in lines:
            points: list[float] = []
            for i, day in enumerate(series):
                val = float(day.get(key, 0))
                x = xs[i]
                y = pad_t + chart_h - (val / max_y) * chart_h
                points.extend([x, y])
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2, smooth=True)

        for i, day in enumerate(series):
            canvas.create_text(xs[i], h - 8, text=day.get("label", ""), fill=theme.c("text_muted"), font=("Segoe UI", 8))

    return update


def security_alert_item(parent: ctk.CTkFrame, theme: ThemeManager, alert: dict) -> None:
    """Single recent security alert row."""
    severity = str(alert.get("severity", "info"))
    color_key = _SEVERITY_COLORS.get(severity, "primary")
    color = theme.c(color_key)

    row = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=theme.radius("sm"),
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    row.pack(fill="x", pady=4)

    inner = ctk.CTkFrame(row, fg_color="transparent")
    inner.pack(fill="x", padx=12, pady=10)

    ctk.CTkLabel(inner, text=_SEVERITY_ICONS.get(severity, "•"), font=font(theme, 14), text_color=color).pack(side="left", padx=(0, 10))

    text_col = ctk.CTkFrame(inner, fg_color="transparent")
    text_col.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(
        text_col,
        text=str(alert.get("title", "Security event")),
        font=font(theme, 12, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
        wraplength=360,
        justify="left",
    ).pack(anchor="w")
    detail = str(alert.get("detail", ""))
    if detail:
        ctk.CTkLabel(
            text_col,
            text=detail,
            font=font(theme, 10),
            text_color=theme.c("text_muted"),
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

    ctk.CTkLabel(
        inner,
        text=str(alert.get("time", "")),
        font=font(theme, 10, weight="bold"),
        text_color=color,
    ).pack(side="right", padx=(8, 0))


def top_risk_row(parent: ctk.CTkFrame, theme: ThemeManager, risk: dict) -> None:
    """Table row for top security risks."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=5)

    ctk.CTkLabel(
        row,
        text=str(risk.get("risk", "—")),
        font=font(theme, 12),
        text_color=theme.c("text_primary"),
        anchor="w",
        width=200,
    ).pack(side="left", fill="x", expand=True)

    sev_key = str(risk.get("severity_key", "warning"))
    tone = {"critical": "error", "warning": "warning"}.get(sev_key, "primary")
    status_chip(row, theme, text=str(risk.get("severity", "—")), tone=tone).pack(side="left", padx=8)

    ctk.CTkLabel(
        row,
        text=str(risk.get("count", 0)),
        font=font(theme, 12, weight="bold"),
        text_color=theme.c("text_primary"),
        width=40,
    ).pack(side="right")


def build_security_section(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
) -> dict:
    """Build full Security & Alerts layout; returns update callbacks and chip refs."""
    refs: dict = {}

    top_grid = ctk.CTkFrame(parent, fg_color="transparent")
    top_grid.pack(fill="x", pady=(0, 12))
    for col in range(3):
        top_grid.grid_columnconfigure(col, weight=1, uniform="sec_top")

    posture_card, posture_content = analytics_card(top_grid, theme, "Security Posture", accent=theme.c("error"))
    posture_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
    refs["failed_logins"] = security_posture_row(posture_content, theme, "Failed DB Connections (Today)", "error")
    refs["alerts_chip"] = security_posture_row(posture_content, theme, "Security Alerts", "warning")
    refs["compliance"] = security_posture_row(posture_content, theme, "Compliance Status", "success")

    overview_card, overview_content = analytics_card(top_grid, theme, "Alerts Overview (Today)", accent=theme.c("warning"))
    overview_card.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
    refs["update_donut"] = alerts_donut_chart(overview_content, theme)

    trend_card, trend_content = analytics_card(top_grid, theme, "Alerts Trend", accent=theme.c("primary"))
    trend_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=4)
    ctk.CTkLabel(
        trend_content,
        text="Last 7 Days",
        font=font(theme, 10),
        text_color=theme.c("text_muted"),
    ).pack(anchor="e")
    refs["update_trend"] = alerts_trend_chart(trend_content, theme)

    bottom_grid = ctk.CTkFrame(parent, fg_color="transparent")
    bottom_grid.pack(fill="x")
    bottom_grid.grid_columnconfigure(0, weight=1, uniform="sec_bot")
    bottom_grid.grid_columnconfigure(1, weight=1, uniform="sec_bot")

    recent_card, recent_content = analytics_card(bottom_grid, theme, "Recent Security Alerts", accent=theme.c("error"))
    recent_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
    refs["recent_frame"] = ctk.CTkFrame(recent_content, fg_color="transparent")
    refs["recent_frame"].pack(fill="both", expand=True)

    risks_card, risks_content = analytics_card(bottom_grid, theme, "Top Security Risks (Today)", accent=theme.c("warning"))
    risks_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)

    header = ctk.CTkFrame(risks_content, fg_color="transparent")
    header.pack(fill="x", pady=(0, 6))
    ctk.CTkLabel(header, text="Risk", font=font(theme, 10, weight="bold"), text_color=theme.c("text_muted"), anchor="w").pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(header, text="Severity", font=font(theme, 10, weight="bold"), text_color=theme.c("text_muted"), width=90).pack(side="left")
    ctk.CTkLabel(header, text="Count", font=font(theme, 10, weight="bold"), text_color=theme.c("text_muted"), width=40, anchor="e").pack(side="right")
    refs["risks_frame"] = ctk.CTkFrame(risks_content, fg_color="transparent")
    refs["risks_frame"].pack(fill="both", expand=True)

    return refs
