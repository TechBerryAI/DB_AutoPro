"""Dashboard section widgets for the enterprise operations view."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.design_system import font, status_chip
from desktop.theme import ThemeManager


def section_block(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    title: str,
    *,
    subtitle: str = "",
) -> ctk.CTkFrame:
    """Titled dashboard section with inner content area."""
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", pady=(0, 20))

    header = ctk.CTkFrame(wrap, fg_color="transparent")
    header.pack(fill="x", pady=(0, 12))
    ctk.CTkFrame(header, width=4, height=22, corner_radius=2, fg_color=theme.c("primary")).pack(side="left", padx=(0, 10))
    col = ctk.CTkFrame(header, fg_color="transparent")
    col.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(col, text=title, font=font(theme, 18, weight="bold"), text_color=theme.c("text_primary"), anchor="w").pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(col, text=subtitle, font=font(theme, 12), text_color=theme.c("text_muted"), anchor="w").pack(anchor="w", pady=(2, 0))

    body = ctk.CTkFrame(wrap, fg_color="transparent")
    body.pack(fill="x")
    body._section_body = body  # type: ignore[attr-defined]
    return body


def panel_grid(parent: ctk.CTkFrame, theme: ThemeManager, columns: int = 2) -> ctk.CTkFrame:
    grid = ctk.CTkFrame(parent, fg_color="transparent")
    grid.pack(fill="x")
    for c in range(columns):
        grid.grid_columnconfigure(c, weight=1, uniform="dash_col")
    return grid


def analytics_card(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    title: str,
    *,
    accent: str | None = None,
) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("lg"),
        fg_color=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    if accent:
        ctk.CTkFrame(card, height=3, corner_radius=0, fg_color=accent).pack(fill="x")
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=18, pady=16)
    ctk.CTkLabel(inner, text=title.upper(), font=font(theme, 10, weight="bold"), text_color=theme.c("text_muted"), anchor="w").pack(anchor="w", pady=(0, 12))
    content = ctk.CTkFrame(inner, fg_color="transparent")
    content.pack(fill="both", expand=True)
    return card, content


def gauge_row(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
    color: str,
) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=6)
    top = ctk.CTkFrame(row, fg_color="transparent")
    top.pack(fill="x")
    ctk.CTkLabel(top, text=label, font=font(theme, 12), text_color=theme.c("text_secondary"), anchor="w").pack(side="left")
    val = ctk.CTkLabel(top, text="—", font=font(theme, 12, weight="bold"), text_color=theme.c("text_primary"))
    val.pack(side="right")
    bar = ctk.CTkProgressBar(row, height=8, corner_radius=4, progress_color=color, fg_color=theme.c("bg_elevated"))
    bar.pack(fill="x", pady=(6, 0))
    bar.set(0)
    return bar, val


def resource_gauge(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
    color: str,
    *,
    icon: str = "",
) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel, ctk.CTkLabel]:
    """Modern resource gauge with primary value, detail line, and progress bar."""
    card = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=theme.radius("md"),
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    card.pack(fill="x", pady=5)

    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="x", padx=14, pady=12)

    header = ctk.CTkFrame(inner, fg_color="transparent")
    header.pack(fill="x")

    title = f"{icon}  {label}" if icon else label
    ctk.CTkLabel(
        header,
        text=title,
        font=font(theme, 11, weight="bold"),
        text_color=theme.c("text_secondary"),
        anchor="w",
    ).pack(side="left")

    value_label = ctk.CTkLabel(
        header,
        text="—",
        font=font(theme, 20, weight="bold"),
        text_color=theme.c("text_primary"),
    )
    value_label.pack(side="right")

    detail_label = ctk.CTkLabel(
        inner,
        text="Collecting…",
        font=font(theme, 10),
        text_color=theme.c("text_muted"),
        anchor="w",
    )
    detail_label.pack(anchor="w", pady=(6, 10))

    bar = ctk.CTkProgressBar(
        inner,
        height=7,
        corner_radius=4,
        progress_color=color,
        fg_color=theme.c("bg_card") if not theme.is_light else theme.c("border_subtle"),
    )
    bar.pack(fill="x")
    bar.set(0)
    return bar, value_label, detail_label


def host_stat_row(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
) -> ctk.CTkLabel:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=4)
    ctk.CTkLabel(
        row,
        text=label,
        font=font(theme, 11),
        text_color=theme.c("text_muted"),
        anchor="w",
    ).pack(side="left")
    value = ctk.CTkLabel(
        row,
        text="—",
        font=font(theme, 11, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="e",
    )
    value.pack(side="right")
    return value


def stat_line(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
    tone: str = "neutral",
) -> ctk.CTkLabel:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=5)
    ctk.CTkLabel(row, text=label, font=font(theme, 12), text_color=theme.c("text_secondary"), anchor="w").pack(side="left")
    chip = status_chip(row, theme, text="—", tone=tone)
    chip.pack(side="right")
    return chip


def metric_inline(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
) -> ctk.CTkLabel:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=4)
    ctk.CTkLabel(row, text=label, font=font(theme, 12), text_color=theme.c("text_muted"), anchor="w").pack(side="left")
    val = ctk.CTkLabel(row, text="—", font=font(theme, 13, weight="bold"), text_color=theme.c("text_primary"))
    val.pack(side="right")
    return val


def recent_activity_row(parent: ctk.CTkFrame, theme: ThemeManager, event: dict) -> None:
    """Compact activity row: timestamp + action (matches sidebar recent activity style)."""
    row = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=theme.radius("sm"),
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    row.pack(fill="x", pady=3)
    action = str(event.get("action", "event")).replace("_", " ").title()
    time_txt = str(event.get("time", ""))
    ctk.CTkLabel(
        row,
        text=f"{time_txt}  {action}" if time_txt else action,
        font=font(theme, 12),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w", padx=14, pady=10)


def alert_row(parent: ctk.CTkFrame, theme: ThemeManager, event: dict) -> None:
    status = str(event.get("status", "info")).lower()
    tone = {"success": "success", "failed": "error", "warning": "warning"}.get(status, "primary")
    row = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=theme.radius("sm"),
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    row.pack(fill="x", pady=3)
    inner = ctk.CTkFrame(row, fg_color="transparent")
    inner.pack(fill="x", padx=12, pady=8)
    ctk.CTkLabel(
        inner,
        text=str(event.get("action", "event")).replace("_", " ").title(),
        font=font(theme, 12, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(side="left")
    status_chip(inner, theme, text=status.upper(), tone=tone).pack(side="right", padx=(8, 0))
    detail = event.get("resource") or event.get("user") or ""
    if detail:
        ctk.CTkLabel(
            row,
            text=str(detail)[:72],
            font=font(theme, 11),
            text_color=theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(0, 8))
