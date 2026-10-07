"""Premium visual components for the operations command center."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from desktop.design_system import font, ghost_button, primary_button, secondary_button
from desktop.lucide_icons import lucide_ctk_image, lucide_icon_badge
from desktop.theme import ThemeManager

def _type_font(theme: ThemeManager, role: str) -> ctk.CTkFont:
    size, weight = theme.type_scale(role)
    return font(theme, size, weight=weight)


# KPI key → Lucide icon (chips stay neutral; colour only when a value warrants it)
KPI_ICONS = {
    "linux_servers": "server",
    "databases": "database",
    "healthy_dbs": "heart-pulse",
    "active_jobs": "activity",
    "active_alerts": "bell",
    "failed_today": "hard-drive",
}


def surface_card(parent, theme: ThemeManager) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """Neutral elevated surface — 1px border, no coloured accents or hover chrome."""
    shell = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("sm"),
        fg_color=theme.c("surface"),
        border_width=1,
        border_color=theme.c("border"),
    )
    inner = ctk.CTkFrame(shell, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=14, pady=12)
    return shell, inner


def page_header(
    parent,
    theme: ThemeManager,
    title: str,
    *,
    subtitle: str = "",
    action_text: str | None = None,
    action_command: Callable[[], None] | None = None,
    pady: tuple[int, int] = (0, 14),
) -> ctk.CTkFrame:
    """Canonical page heading used across all workspace views.

    Title = type_scale page_title (22 bold). Subtitle = label (12).
    No accent bars — one style for Servers, Databases, Backup, etc.
    """
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", pady=pady)

    header = ctk.CTkFrame(wrap, fg_color="transparent")
    header.pack(fill="x")

    left = ctk.CTkFrame(header, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)

    # Always Segoe UI — never leave page titles on the CTk default face.
    title_size, title_weight = theme.type_scale("page_title")
    ctk.CTkLabel(
        left,
        text=title,
        font=ctk.CTkFont(family="Segoe UI", size=title_size, weight=title_weight),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w")
    if subtitle:
        sub_size, sub_weight = theme.type_scale("label")
        ctk.CTkLabel(
            left,
            text=subtitle,
            font=ctk.CTkFont(family="Segoe UI", size=sub_size, weight=sub_weight),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w", pady=(6, 0))

    # Do not place action buttons here — shell header already has Refresh.
    del action_text, action_command

    return wrap


def section_header(
    parent,
    theme: ThemeManager,
    title: str,
    *,
    subtitle: str = "",
    action_text: str | None = None,
    action_command: Callable[[], None] | None = None,
) -> ctk.CTkFrame:
    """In-page section heading (smaller than page_header)."""
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", pady=(0, 10))

    header = ctk.CTkFrame(wrap, fg_color="transparent")
    header.pack(fill="x", pady=(0, 8))

    left = ctk.CTkFrame(header, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(
        left,
        text=title,
        font=_type_font(theme, "section"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(
            left,
            text=subtitle,
            font=_type_font(theme, "metadata"),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w", pady=(3, 0))

    if action_text and action_command:
        btn = ghost_button(
            header,
            theme,
            text=action_text,
            command=action_command,
            height=28,
        )
        btn.configure(
            text_color=theme.c("text_secondary"),
            hover_color=theme.c("bg_card_hover"),
            font=font(theme, 12),
        )
        btn.pack(side="right")

    body = ctk.CTkFrame(wrap, fg_color="transparent")
    body.pack(fill="x")
    return body


def command_center_header(
    parent,
    theme: ThemeManager,
    *,
    greeting: str = "",
    role: str = "",
    initials: str = "",
    datetime_text: str = "",
    on_refresh: Callable[[], None] | None = None,
) -> None:
    """Dashboard page title — same typography as every other page_header."""
    del greeting, role, initials, datetime_text, on_refresh
    page_header(
        parent,
        theme,
        "Dashboard",
        subtitle="Active connection activity and workstation metrics.",
    )

def metric_card(
    parent,
    theme: ThemeManager,
    *,
    key: str,
    label: str,
    secondary: str = "",
) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]:
    """Dense KPI tile (~88–96px): neutral chip, clear label, tabular-feeling value."""
    icon_name = KPI_ICONS.get(key, "activity")
    chip_fg = theme.c("text_secondary")
    chip_bg = theme.c("bg_elevated") if not theme.is_light else theme.c("neutral_muted")

    shell, body = surface_card(parent, theme)

    top = ctk.CTkFrame(body, fg_color="transparent")
    top.pack(fill="x")

    icon = lucide_icon_badge(
        top, icon_name, chip_fg, chip_bg, box=28, icon_size=14, radius=8
    )
    icon.pack(side="left")

    title = ctk.CTkLabel(
        top,
        text=label,
        font=_type_font(theme, "card_title"),
        text_color=theme.c("text_secondary"),
        anchor="w",
    )
    title.pack(side="left", padx=(10, 0), fill="x", expand=True)

    value = ctk.CTkLabel(
        body,
        text="0",
        font=_type_font(theme, "kpi_value"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    value.pack(anchor="w", pady=(8, 0))

    secondary_lbl = ctk.CTkLabel(
        body,
        text=secondary or "—",
        font=_type_font(theme, "metadata"),
        text_color=theme.c("text_secondary"),
        anchor="w",
    )
    secondary_lbl.pack(anchor="w", pady=(2, 0))
    return shell, value, secondary_lbl


def empty_state(
    parent,
    theme: ThemeManager,
    *,
    title: str,
    message: str,
    icon: str = "database",
    badge: str | None = None,
    action_text: str | None = None,
    action_command: Callable[[], None] | None = None,
    tone: str = "info",
) -> None:
    # badge kept for backwards compatibility → map letter codes to Lucide names
    legacy = {
        "DB": "database",
        "OK": "shield-check",
        "JB": "briefcase",
        "EV": "activity",
    }
    icon_name = legacy.get(badge or "", icon) if badge else icon

    # Success empty states stay tinted lightly; others use neutral surface.
    if tone == "success":
        wrap_bg = theme.c("success_muted")
        icon_fg = theme.c("success")
    elif tone == "warning":
        wrap_bg = theme.c("warning_muted")
        icon_fg = theme.c("warning")
    else:
        wrap_bg = theme.c("bg_elevated")
        icon_fg = theme.c("text_secondary")

    wrap = ctk.CTkFrame(
        parent,
        fg_color=wrap_bg,
        corner_radius=theme.radius("sm"),
        border_width=1,
        border_color=theme.c("border"),
    )
    wrap.pack(fill="x", pady=4)
    inner = ctk.CTkFrame(wrap, fg_color="transparent")
    inner.pack(fill="x", padx=16, pady=14)

    badge_box = lucide_icon_badge(
        inner,
        icon_name,
        icon_fg,
        theme.c("surface"),
        box=36,
        icon_size=18,
        radius=10,
    )
    badge_box.pack()

    ctk.CTkLabel(
        inner,
        text=title,
        font=_type_font(theme, "card_title"),
        text_color=theme.c("text_primary"),
    ).pack(pady=(10, 0))
    ctk.CTkLabel(
        inner,
        text=message,
        font=_type_font(theme, "metadata"),
        text_color=theme.c("text_secondary"),
        wraplength=320,
        justify="center",
    ).pack(pady=(4, 0))
    if action_text and action_command:
        primary_button(
            inner,
            theme,
            text=action_text,
            command=action_command,
            width=160,
            height=32,
        ).pack(pady=(12, 0))


def health_overview_visual(
    parent,
    theme: ThemeManager,
) -> tuple[dict[str, ctk.CTkLabel], Callable[[dict[str, int]], None]]:
    """Donut + legend + distribution bar. Returns count labels and update(counts)."""
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x")

    top = ctk.CTkFrame(wrap, fg_color="transparent")
    top.pack(fill="x")

    left = ctk.CTkFrame(top, fg_color="transparent", width=140)
    left.pack(side="left", padx=(0, 16))
    left.pack_propagate(False)

    size = 128
    canvas = tk.Canvas(
        left,
        width=size,
        height=size,
        bg=theme.c("bg_card"),
        highlightthickness=0,
        bd=0,
    )
    canvas.pack(pady=(4, 0))
    center_id = canvas.create_text(
        size // 2,
        size // 2,
        text="0\nConnected",
        fill=theme.c("text_primary"),
        font=("Segoe UI", 12, "bold"),
        justify="center",
    )

    right = ctk.CTkFrame(top, fg_color="transparent")
    right.pack(side="left", fill="both", expand=True)

    labels: dict[str, ctk.CTkLabel] = {}
    legend_specs = [
        ("healthy", "Healthy", theme.c("success")),
        ("warning", "Warning", theme.c("warning")),
        ("critical", "Critical", theme.c("error")),
        ("offline", "Registered", theme.c("text_muted")),
    ]
    for key, label, color in legend_specs:
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x", pady=5)
        ctk.CTkLabel(row, text="●", font=font(theme, 12), text_color=color, width=16).pack(side="left")
        ctk.CTkLabel(
            row,
            text=label,
            font=font(theme, 12),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        val = ctk.CTkLabel(
            row,
            text="0",
            font=font(theme, 13, weight="bold"),
            text_color=theme.c("text_primary"),
        )
        val.pack(side="right")
        labels[key] = val

    total_row = ctk.CTkFrame(right, fg_color="transparent")
    total_row.pack(fill="x", pady=(8, 0))
    ctk.CTkLabel(
        total_row,
        text="Total",
        font=font(theme, 12, weight="bold"),
        text_color=theme.c("text_muted"),
        anchor="w",
    ).pack(side="left")
    total_val = ctk.CTkLabel(
        total_row,
        text="0",
        font=font(theme, 13, weight="bold"),
        text_color=theme.c("text_primary"),
    )
    total_val.pack(side="right")
    labels["total"] = total_val

    track = ctk.CTkFrame(wrap, height=8, corner_radius=4, fg_color=theme.c("bg_elevated"))
    track.pack(fill="x", pady=(14, 0))
    track.pack_propagate(False)
    bar_segs: dict[str, ctk.CTkFrame] = {}
    palette = {
        "healthy": theme.c("success"),
        "warning": theme.c("warning"),
        "critical": theme.c("error"),
        "offline": theme.c("text_muted"),
    }
    for key, color in palette.items():
        seg = ctk.CTkFrame(track, fg_color=color, corner_radius=0, height=8)
        bar_segs[key] = seg

    def update(counts: dict[str, int]) -> None:
        for key, lbl in labels.items():
            lbl.configure(text=str(int(counts.get(key, 0))))

        total = max(sum(int(counts.get(k, 0)) for k in ("healthy", "warning", "critical", "offline")), 0)
        connected = int(counts.get("healthy", 0)) + int(counts.get("warning", 0)) + int(counts.get("critical", 0))
        canvas.itemconfigure(center_id, text=f"{connected}\nConnected", fill=theme.c("text_primary"))

        canvas.delete("arc")
        pad = 10
        x0, y0, x1, y1 = pad, pad, size - pad, size - pad
        if total <= 0:
            canvas.create_oval(x0, y0, x1, y1, outline=theme.c("border"), width=12, tags="arc")
        else:
            start = -90.0
            for key in ("healthy", "warning", "critical", "offline"):
                value = int(counts.get(key, 0))
                if value <= 0:
                    continue
                extent = 360.0 * (value / total)
                canvas.create_arc(
                    x0,
                    y0,
                    x1,
                    y1,
                    start=start,
                    extent=extent,
                    style=tk.ARC,
                    width=12,
                    outline=palette[key],
                    tags="arc",
                )
                start += extent

        if total <= 0:
            for seg in bar_segs.values():
                seg.place_forget()
            return
        x = 0.0
        for key in ("healthy", "warning", "critical", "offline"):
            value = int(counts.get(key, 0))
            if value <= 0:
                bar_segs[key].place_forget()
                continue
            frac = value / total
            bar_segs[key].place(relx=x, rely=0, relwidth=max(frac, 0.02), relheight=1)
            x += frac

    return labels, update


def alert_item_compact(
    parent,
    theme: ThemeManager,
    *,
    severity: str,
    title: str,
    detail: str,
    time_text: str,
) -> None:
    sev = severity.lower()
    if sev == "critical":
        tone_fg, tone_bg, icon_name = theme.c("error"), theme.c("error_muted"), "circle-alert"
    elif sev == "warning":
        tone_fg, tone_bg, icon_name = theme.c("warning"), theme.c("warning_muted"), "triangle-alert"
    else:
        tone_fg, tone_bg, icon_name = theme.c("primary"), theme.c("primary_muted"), "bell"

    card = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_elevated"),
        corner_radius=theme.radius("sm"),
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    card.pack(fill="x", pady=4)
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="x", padx=12, pady=10)

    icon = lucide_icon_badge(inner, icon_name, tone_fg, tone_bg, box=28, icon_size=15, radius=8)
    icon.pack(side="left")

    col = ctk.CTkFrame(inner, fg_color="transparent")
    col.pack(side="left", fill="x", expand=True, padx=(10, 0))
    top = ctk.CTkFrame(col, fg_color="transparent")
    top.pack(fill="x")
    ctk.CTkLabel(
        top,
        text=title,
        font=font(theme, 13, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(top, text=time_text, font=font(theme, 11), text_color=theme.c("text_muted")).pack(side="right")
    if detail:
        ctk.CTkLabel(
            col,
            text=detail,
            font=font(theme, 12),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))


def resource_progress_row(
    parent,
    theme: ThemeManager,
    label: str,
) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel, ctk.CTkLabel]:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=6)
    head = ctk.CTkFrame(row, fg_color="transparent")
    head.pack(fill="x")
    ctk.CTkLabel(head, text=label, font=font(theme, 12), text_color=theme.c("text_secondary"), anchor="w").pack(
        side="left"
    )
    value = ctk.CTkLabel(head, text="—", font=font(theme, 13, weight="bold"), text_color=theme.c("text_primary"))
    value.pack(side="right")
    bar = ctk.CTkProgressBar(
        row,
        height=7,
        corner_radius=4,
        progress_color=theme.c("primary"),
        fg_color=theme.c("bg_elevated"),
    )
    bar.pack(fill="x", pady=(6, 0))
    bar.set(0)
    detail = ctk.CTkLabel(row, text="", font=font(theme, 11), text_color=theme.c("text_muted"), anchor="w")
    detail.pack(anchor="w", pady=(3, 0))
    return bar, value, detail


def activity_item(
    parent,
    theme: ThemeManager,
    *,
    time_text: str,
    title: str,
    detail: str,
    tone: str = "info",
) -> None:
    colors = {
        "success": theme.c("success"),
        "warning": theme.c("warning"),
        "error": theme.c("error"),
        "info": theme.c("primary"),
    }
    icons = {
        "success": "circle-check",
        "warning": "triangle-alert",
        "error": "circle-alert",
        "info": "activity",
    }
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=5)
    badge = lucide_icon_badge(
        row,
        icons.get(tone, "activity"),
        colors.get(tone, theme.c("primary")),
        theme.c("bg_elevated"),
        box=26,
        icon_size=14,
        radius=8,
    )
    badge.pack(side="left")
    col = ctk.CTkFrame(row, fg_color="transparent")
    col.pack(side="left", fill="x", expand=True, padx=(10, 0))
    ctk.CTkLabel(col, text=time_text, font=font(theme, 11), text_color=theme.c("text_muted"), anchor="w").pack(
        anchor="w"
    )
    ctk.CTkLabel(
        col,
        text=title,
        font=font(theme, 13, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w")
    if detail:
        ctk.CTkLabel(
            col,
            text=detail,
            font=font(theme, 12),
            text_color=theme.c("text_secondary"),
            anchor="w",
        ).pack(anchor="w")


def threshold_color(theme: ThemeManager, pct: float) -> str:
    if pct >= 85:
        return theme.c("error")
    if pct >= 70:
        return theme.c("warning")
    return theme.c("primary")


# Backwards-compatible aliases
compact_section = section_header
panel_card = surface_card
compact_kpi_card = metric_card
