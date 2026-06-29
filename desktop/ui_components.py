"""Reusable enterprise UI components for the desktop app."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.design_system import font
from desktop.theme import ThemeManager


def _hover_card(card: ctk.CTkFrame, theme: ThemeManager, accent: str) -> None:
    normal_border = theme.c("border")
    hover_border = accent if accent else theme.c("border_focus")
    card.bind("<Enter>", lambda _: card.configure(border_color=hover_border))
    card.bind("<Leave>", lambda _: card.configure(border_color=normal_border))


def kpi_card(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    icon: str,
    title: str,
    accent: str,
    trend: str = "",
) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]:
    """Build a KPI card; returns (card, value_label, trend_label)."""
    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("lg"),
        fg_color=theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    _hover_card(card, theme, accent)

    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=20, pady=18)

    accent_strip = ctk.CTkFrame(body, height=3, corner_radius=2, fg_color=accent)
    accent_strip.pack(fill="x", pady=(0, 14))

    top = ctk.CTkFrame(body, fg_color="transparent")
    top.pack(fill="x")

    icon_bg = ctk.CTkFrame(
        top,
        width=32,
        height=32,
        corner_radius=8,
        fg_color=theme.c("bg_elevated"),
        border_width=1 if theme.is_light else 0,
        border_color=theme.c("border"),
    )
    icon_bg.pack(side="left")
    icon_bg.pack_propagate(False)
    ctk.CTkLabel(icon_bg, text=icon, font=ctk.CTkFont(size=15), text_color=accent).place(relx=0.5, rely=0.5, anchor="center")

    ctk.CTkLabel(
        top,
        text=title.upper(),
        font=ctk.CTkFont(size=11, weight="bold"),
        text_color=theme.c("text_muted"),
        anchor="w",
    ).pack(side="left", padx=(10, 0))

    value_label = ctk.CTkLabel(
        body,
        text="—",
        font=ctk.CTkFont(size=36, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value_label.pack(anchor="w", pady=(14, 4))

    trend_label = ctk.CTkLabel(
        body,
        text=trend,
        font=ctk.CTkFont(size=12),
        text_color=theme.c("text_secondary"),
        anchor="w",
    )
    trend_label.pack(anchor="w")

    return card, value_label, trend_label


def key_metric_tile(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    icon: str,
    icon_color: str,
    title: str,
    badge: str,
    badge_fg: str,
    badge_text: str,
    subtitle: str,
    footer_link: str,
    icon_bg: str | None = None,
) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]:
    """Premium metric tile with glass-style elevation."""
    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("xl"),
        fg_color=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    _hover_card(card, theme, icon_color)

    accent_top = ctk.CTkFrame(card, height=3, corner_radius=0, fg_color=icon_color)
    accent_top.pack(fill="x")

    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=22)

    top = ctk.CTkFrame(body, fg_color="transparent")
    top.pack(fill="x")

    icon_frame = ctk.CTkFrame(
        top,
        width=44,
        height=44,
        corner_radius=theme.radius("md"),
        fg_color=icon_bg or theme.c("bg_elevated"),
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    icon_frame.pack(side="left")
    icon_frame.pack_propagate(False)
    ctk.CTkLabel(
        icon_frame,
        text=icon,
        font=font(theme, 17),
        text_color=icon_color,
    ).place(relx=0.5, rely=0.5, anchor="center")

    ctk.CTkLabel(
        top,
        text=title,
        font=font(theme, 14, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(side="left", padx=(14, 0))

    badge_label = ctk.CTkLabel(
        top,
        text=badge,
        font=font(theme, 10, weight="bold"),
        text_color=badge_text,
        fg_color=badge_fg,
        corner_radius=theme.radius("sm"),
        padx=10,
        pady=4,
    )
    badge_label.pack(side="right")

    value_label = ctk.CTkLabel(
        body,
        text="0",
        font=font(theme, 42, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value_label.pack(anchor="w", pady=(22, 8))

    ctk.CTkLabel(
        body,
        text=subtitle,
        font=font(theme, 12),
        text_color=theme.c("text_muted"),
        anchor="w",
    ).pack(anchor="w")

    ctk.CTkLabel(
        body,
        text=footer_link,
        font=font(theme, 12, weight="bold"),
        text_color=theme.c("primary"),
        anchor="w",
        cursor="hand2",
    ).pack(anchor="w", pady=(14, 0))

    return card, value_label, badge_label


def home_greeting_card(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    greeting: str,
    role: str,
    initials: str,
    datetime_text: str,
    on_refresh: Callable[[], None] | None = None,
) -> tuple[None, ctk.CTkLabel]:
    """Premium home header with avatar and actions."""
    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("xl"),
        fg_color=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    card.pack(fill="x", pady=(0, 28))

    accent = ctk.CTkFrame(card, height=4, corner_radius=0, fg_color=theme.c("primary"))
    accent.pack(fill="x")
    ctk.CTkFrame(card, height=1, fg_color=theme.c("accent")).pack(fill="x")

    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=32, pady=28)
    body.grid_columnconfigure(0, weight=1)

    top = ctk.CTkFrame(body, fg_color="transparent")
    top.grid(row=0, column=0, sticky="ew", pady=(0, 20))
    top.grid_columnconfigure(0, weight=1)

    left = ctk.CTkFrame(top, fg_color="transparent")
    left.grid(row=0, column=0, sticky="w")

    avatar = ctk.CTkFrame(
        left,
        width=56,
        height=56,
        corner_radius=28,
        fg_color=theme.c("primary"),
        border_width=2,
        border_color=theme.c("accent"),
    )
    avatar.pack(side="left")
    avatar.pack_propagate(False)
    ctk.CTkLabel(
        avatar,
        text=initials,
        font=font(theme, 18, weight="bold"),
        text_color=theme.c("text_on_primary"),
    ).place(relx=0.5, rely=0.5, anchor="center")

    text_col = ctk.CTkFrame(left, fg_color="transparent")
    text_col.pack(side="left", padx=(14, 0))
    ctk.CTkLabel(
        text_col,
        text=greeting,
        font=font(theme, 30, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w")
    ctk.CTkLabel(
        text_col,
        text=role,
        font=ctk.CTkFont(size=13),
        text_color=theme.c("text_muted"),
        anchor="w",
    ).pack(anchor="w", pady=(4, 0))

    right = ctk.CTkFrame(top, fg_color="transparent")
    right.grid(row=0, column=1, sticky="e", padx=(16, 0))

    datetime_label = ctk.CTkLabel(
        right,
        text=datetime_text,
        font=font(theme, 12, weight="bold"),
        text_color=theme.c("text_secondary"),
        anchor="e",
    )
    datetime_label.pack(anchor="e")
    ctk.CTkLabel(
        right,
        text="Live",
        font=font(theme, 10, weight="bold"),
        text_color=theme.c("success"),
        anchor="e",
    ).pack(anchor="e", pady=(4, 0))

    actions = ctk.CTkFrame(body, fg_color="transparent")
    actions.grid(row=1, column=0, sticky="e", pady=(16, 0))
    ctk.CTkButton(
        actions,
        text="Refresh Dashboard",
        width=160,
        height=44,
        corner_radius=theme.radius("md"),
        fg_color=theme.c("primary"),
        hover_color=theme.c("primary_hover"),
        text_color=theme.c("text_on_primary"),
        font=font(theme, 13, weight="bold"),
        command=on_refresh,
    ).pack(side="right")

    return None, datetime_label


def set_kpi_trend(trend_label: ctk.CTkLabel, theme: ThemeManager, trend: dict, period: str = "vs last week") -> None:
    """Apply arrow, percentage, and color to a KPI trend label."""
    direction = trend.get("direction", "flat")
    pct = abs(trend.get("pct", 0))
    arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(direction, "→")
    if direction == "up":
        color = theme.c("success")
    elif direction == "down":
        color = theme.c("error")
    else:
        color = theme.c("text_muted")
    text = f"{arrow}  {pct:.1f}%  {period}" if pct or direction != "flat" else f"→  0%  {period}"
    trend_label.configure(text=text, text_color=color)


def progress_metric(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    label: str,
    icon: str,
    color: str,
) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel, ctk.CTkLabel]:
    """Metric row with label, progress bar, and value label."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=10)

    header = ctk.CTkFrame(row, fg_color="transparent")
    header.pack(fill="x")

    ctk.CTkLabel(
        header,
        text=f"{icon}  {label}",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=theme.c("text_secondary"),
        anchor="w",
    ).pack(side="left")

    value_label = ctk.CTkLabel(
        header,
        text="—",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=theme.c("text_primary"),
    )
    value_label.pack(side="right")

    bar = ctk.CTkProgressBar(
        row,
        height=10,
        corner_radius=5,
        progress_color=color,
        fg_color=theme.c("bg_elevated"),
    )
    bar.pack(fill="x", pady=(8, 0))
    bar.set(0)

    pct_label = ctk.CTkLabel(
        row,
        text="",
        font=ctk.CTkFont(size=10),
        text_color=theme.c("text_muted"),
        anchor="w",
    )
    pct_label.pack(anchor="w", pady=(4, 0))

    return bar, value_label, pct_label


def gauge_metric(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    label: str,
    color: str,
) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
    """Compact gauge-style metric for monitoring widgets."""
    frame = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=10,
        border_width=1 if theme.is_light else 0,
        border_color=theme.c("border"),
    )
    frame.pack(fill="x", pady=6)

    inner = ctk.CTkFrame(frame, fg_color="transparent")
    inner.pack(fill="x", padx=14, pady=12)

    ctk.CTkLabel(
        inner,
        text=label.upper(),
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=theme.c("text_muted"),
        anchor="w",
    ).pack(anchor="w")

    value_lbl = ctk.CTkLabel(
        inner,
        text="—",
        font=ctk.CTkFont(size=22, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value_lbl.pack(anchor="w", pady=(6, 8))

    bar = ctk.CTkProgressBar(
        inner,
        height=6,
        corner_radius=3,
        progress_color=color,
        fg_color=theme.c("bg_card"),
    )
    bar.pack(fill="x")
    bar.set(0)
    return bar, value_lbl


def mini_bar_chart(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    title: str,
    height: int = 100,
) -> tuple[tk.Canvas, Callable[[list[dict]], None]]:
    """Bar chart canvas; updater accepts [{label, count}, ...]."""
    card = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=10,
        border_width=1 if theme.is_light else 0,
        border_color=theme.c("border"),
    )
    card.pack(fill="both", expand=True)

    if title:
        ctk.CTkLabel(
            card,
            text=title.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=theme.c("text_muted"),
        ).pack(anchor="w", padx=14, pady=(12, 4))

    canvas = tk.Canvas(
        card,
        height=height,
        bg=theme.c("bg_elevated"),
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True, padx=14, pady=(0, 12))

    def update(data: list[dict]) -> None:
        canvas.delete("all")
        if not data:
            return
        canvas.update_idletasks()
        w = max(canvas.winfo_width(), 200)
        h = height - 8
        max_count = max((d.get("count", 0) for d in data), default=1) or 1
        bar_w = max(12, (w - 20) // len(data) - 8)
        gap = 8
        x = 10
        for item in data:
            count = item.get("count", 0)
            bar_h = max(4, int((count / max_count) * (h - 24)))
            y0 = h - bar_h
            color = theme.c("primary") if count else theme.c("border")
            canvas.create_rectangle(x, y0, x + bar_w, h, fill=color, outline="")
            canvas.create_text(
                x + bar_w // 2, h + 10,
                text=item.get("label", ""),
                fill=theme.c("text_muted"),
                font=("Segoe UI", 8),
            )
            if count:
                canvas.create_text(
                    x + bar_w // 2, y0 - 8,
                    text=str(count),
                    fill=theme.c("text_secondary"),
                    font=("Segoe UI", 8, "bold"),
                )
            x += bar_w + gap

    return canvas, update


def timeline_item(parent: ctk.CTkFrame, theme: ThemeManager, event: dict) -> None:
    """Single row in the recent activity timeline."""
    status = event.get("status", "info").lower()
    dot_color = {
        "success": theme.c("success"),
        "failed": theme.c("error"),
        "warning": theme.c("warning"),
    }.get(status, theme.c("primary"))

    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=4)

    rail = ctk.CTkFrame(row, width=20, fg_color="transparent")
    rail.pack(side="left", fill="y")
    ctk.CTkLabel(rail, text="●", font=ctk.CTkFont(size=10), text_color=dot_color).pack(anchor="n", pady=(2, 0))

    content = ctk.CTkFrame(
        row,
        fg_color=theme.c("bg_elevated"),
        corner_radius=8,
        border_width=1 if theme.is_light else 0,
        border_color=theme.c("border"),
    )
    content.pack(side="left", fill="x", expand=True)

    top = ctk.CTkFrame(content, fg_color="transparent")
    top.pack(fill="x", padx=12, pady=(8, 2))

    ctk.CTkLabel(
        top,
        text=event.get("action", "event").replace("_", " ").title(),
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(side="left")

    time_txt = event.get("time", "")
    if time_txt:
        ctk.CTkLabel(
            top,
            text=time_txt,
            font=ctk.CTkFont(size=10),
            text_color=theme.c("text_muted"),
        ).pack(side="right")

    resource = event.get("resource", "")
    user = event.get("user", "")
    detail = f"{resource}" if resource else ""
    if user:
        detail = f"{detail} · {user}" if detail else user
    if detail:
        ctk.CTkLabel(
            content,
            text=detail[:80],
            font=ctk.CTkFont(size=11),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(0, 8))


def quick_action_button(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    text: str,
    icon: str,
    command: Callable,
    primary: bool = False,
) -> ctk.CTkButton:
    """Styled quick-action button."""
    if primary:
        fg = theme.c("primary")
        hover = theme.c("primary_hover")
        txt = theme.c("text_on_primary")
        border_w = 0
        border_c = theme.c("primary")
    else:
        fg = theme.c("bg_card") if theme.is_light else theme.c("bg_elevated")
        hover = theme.c("bg_card_hover")
        txt = theme.c("text_secondary")
        border_w = 1 if theme.is_light else 0
        border_c = theme.c("border")

    return ctk.CTkButton(
        parent,
        text=f"{icon}  {text}",
        command=command,
        height=38,
        corner_radius=8,
        fg_color=fg,
        hover_color=hover,
        border_width=border_w,
        border_color=border_c,
        text_color=txt,
        font=ctk.CTkFont(size=12, weight="bold" if primary else "normal"),
        anchor="center",
    )


def health_row(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    label: str,
) -> ctk.CTkLabel:
    """Single label/value row in the health panel."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=5)

    ctk.CTkLabel(
        row,
        text=label,
        font=ctk.CTkFont(size=12),
        text_color=theme.c("text_muted"),
        width=120,
        anchor="w",
    ).pack(side="left")

    value = ctk.CTkLabel(
        row,
        text="—",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value.pack(side="left", fill="x", expand=True)
    return value


def section_header(parent: ctk.CTkFrame, theme: ThemeManager, title: str, subtitle: str = "") -> None:
    """Page section header with optional subtitle."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x")
    ctk.CTkFrame(row, width=4, height=28, corner_radius=2, fg_color=theme.c("primary")).pack(side="left", padx=(0, 12))
    text_col = ctk.CTkFrame(row, fg_color="transparent")
    text_col.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(
        text_col,
        text=title,
        font=font(theme, 26, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(
            text_col,
            text=subtitle,
            font=font(theme, 13),
            text_color=theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))


def database_catalog_table(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    host: str,
    port: int,
) -> tuple[ctk.CTkScrollableFrame, ctk.CTkLabel, ctk.CTkLabel]:
    """Scrollable database catalog; returns (body_frame, count_label, host_label)."""
    header = ctk.CTkFrame(parent, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(0, 8))

    host_label = ctk.CTkLabel(
        header,
        text=f"Server  {host}:{port}",
        font=ctk.CTkFont(size=12),
        text_color=theme.c("text_secondary"),
    )
    host_label.pack(side="left")

    count_label = ctk.CTkLabel(
        header,
        text="",
        font=ctk.CTkFont(size=11, weight="bold"),
        text_color=theme.c("text_muted"),
    )
    count_label.pack(side="right")

    table_wrap = ctk.CTkFrame(parent, fg_color=theme.c("bg_elevated"), corner_radius=8)
    table_wrap.pack(fill="x", padx=16, pady=(0, 16))

    col_header = ctk.CTkFrame(table_wrap, fg_color=theme.c("bg_card"), corner_radius=0)
    col_header.pack(fill="x", padx=1, pady=(1, 0))

    columns = [
        ("DATABASE", 0.32),
        ("OWNER", 0.22),
        ("SIZE", 0.18),
        ("STATUS", 0.14),
        ("", 0.14),
    ]
    for i, (title, weight) in enumerate(columns):
        col_header.grid_columnconfigure(i, weight=int(weight * 100))
        ctk.CTkLabel(
            col_header,
            text=title,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=theme.c("text_muted"),
            anchor="w",
        ).grid(row=0, column=i, padx=12, pady=8, sticky="w")

    body = ctk.CTkScrollableFrame(table_wrap, fg_color="transparent", height=160)
    body.pack(fill="x", padx=4, pady=4)

    return body, count_label, host_label


def populate_database_catalog(
    body: ctk.CTkScrollableFrame,
    count_label: ctk.CTkLabel,
    theme: ThemeManager,
    databases: list[dict],
    active_database: str | None,
) -> None:
    """Fill catalog table rows."""
    for child in body.winfo_children():
        child.destroy()

    count_label.configure(text=f"{len(databases)} database{'s' if len(databases) != 1 else ''} found")

    if not databases:
        ctk.CTkLabel(
            body,
            text="No databases found or insufficient permissions.",
            font=ctk.CTkFont(size=12),
            text_color=theme.c("text_muted"),
        ).pack(anchor="w", padx=12, pady=12)
        return

    for idx, db in enumerate(databases):
        is_active = active_database and db.get("name") == active_database
        row_bg = theme.c("primary_muted") if is_active else ("transparent" if idx % 2 == 0 else theme.c("bg_card"))

        row = ctk.CTkFrame(body, fg_color=row_bg, corner_radius=6)
        row.pack(fill="x", pady=1)
        for i, weight in enumerate([32, 22, 18, 14, 14]):
            row.grid_columnconfigure(i, weight=weight)

        name_color = theme.c("primary") if is_active else theme.c("text_primary")
        ctk.CTkLabel(
            row,
            text=db.get("name", "—"),
            font=ctk.CTkFont(size=12, weight="bold" if is_active else "normal"),
            text_color=name_color,
            anchor="w",
        ).grid(row=0, column=0, padx=12, pady=8, sticky="w")

        ctk.CTkLabel(
            row,
            text=db.get("owner", "—"),
            font=ctk.CTkFont(size=11),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).grid(row=0, column=1, padx=8, pady=8, sticky="w")

        ctk.CTkLabel(
            row,
            text=db.get("size", "—"),
            font=ctk.CTkFont(size=11),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).grid(row=0, column=2, padx=8, pady=8, sticky="w")

        status = db.get("status", "online")
        status_color = theme.c("success") if status == "online" else theme.c("warning")
        ctk.CTkLabel(
            row,
            text=status.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=status_color,
            anchor="w",
        ).grid(row=0, column=3, padx=8, pady=8, sticky="w")

        if is_active:
            ctk.CTkLabel(
                row,
                text="ACTIVE",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=theme.c("success"),
                anchor="e",
            ).grid(row=0, column=4, padx=12, pady=8, sticky="e")


def panel_card(parent: ctk.CTkFrame, theme: ThemeManager, title: str) -> ctk.CTkFrame:
    """Standard titled panel card with premium elevation."""
    card = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("lg"),
        fg_color=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    header = ctk.CTkFrame(card, fg_color="transparent")
    header.pack(fill="x", padx=20, pady=(18, 10))
    ctk.CTkFrame(header, width=3, height=14, corner_radius=2, fg_color=theme.c("primary")).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        header,
        text=title.upper(),
        font=font(theme, 11, weight="bold"),
        text_color=theme.c("text_muted"),
    ).pack(side="left")
    return card
