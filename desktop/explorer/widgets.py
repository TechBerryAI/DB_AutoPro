"""Reusable widgets for the Database Explorer UI."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.theme import ThemeManager

try:
    from desktop.lucide_icons import lucide_ctk_image
except Exception:  # pragma: no cover - optional icon dependency
    lucide_ctk_image = None  # type: ignore


def count_badge(parent: ctk.CTkFrame, theme: ThemeManager, count: int) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=str(count),
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=theme.c("primary"),
        fg_color=theme.c("bg_elevated"),
        corner_radius=8,
        width=28,
        height=20,
    )


def kv_grid(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    items: list[tuple[str, str]],
    *,
    columns: int = 2,
) -> ctk.CTkFrame:
    """Render a key/value metadata grid."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="x", padx=12, pady=8)
    for i in range(columns * 2):
        frame.grid_columnconfigure(i, weight=1 if i % 2 == 1 else 0)

    for idx, (key, value) in enumerate(items):
        row = idx // columns
        col = (idx % columns) * 2
        ctk.CTkLabel(
            frame,
            text=key,
            font=ctk.CTkFont(size=11),
            text_color=theme.c("text_muted"),
            anchor="w",
        ).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
        ctk.CTkLabel(
            frame,
            text=value or "—",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=theme.c("text_primary"),
            anchor="w",
            wraplength=260,
            justify="left",
        ).grid(row=row, column=col + 1, sticky="ew", padx=(0, 16), pady=4)
    return frame


def section_card(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    title: str,
) -> ctk.CTkFrame:
    card = ctk.CTkFrame(
        parent,
        fg_color=theme.c("bg_card"),
        corner_radius=10,
        border_width=1,
        border_color=theme.c("border_subtle"),
    )
    card.pack(fill="x", padx=8, pady=(0, 10))
    ctk.CTkLabel(
        card,
        text=title,
        font=ctk.CTkFont(size=13, weight="bold"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w", padx=14, pady=(12, 4))
    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=2, pady=(0, 10))
    return body


def tree_row(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    label: str,
    indent: int = 0,
    count: int | None = None,
    expandable: bool = False,
    expanded: bool = False,
    selected: bool = False,
    icon_name: str | None = None,
    on_click: Callable[[], None] | None = None,
    on_toggle: Callable[[], None] | None = None,
) -> ctk.CTkFrame:
    """One explorer tree row with optional chevron, icon, and count badge."""
    bg = theme.c("bg_elevated") if selected else "transparent"
    row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=6, cursor="hand2")
    row.pack(fill="x", padx=(8 + indent * 14, 8), pady=1)

    if expandable:
        chev = ctk.CTkLabel(
            row,
            text="▼" if expanded else "▶",
            width=18,
            font=ctk.CTkFont(size=10),
            text_color=theme.c("text_muted"),
        )
        chev.pack(side="left", padx=(6, 2), pady=6)
        if on_toggle:
            chev.bind("<Button-1>", lambda _e: on_toggle())
    else:
        ctk.CTkLabel(row, text="", width=18).pack(side="left", padx=(6, 2), pady=6)

    if icon_name and lucide_ctk_image is not None:
        try:
            img = lucide_ctk_image(icon_name, size=14, color=theme.c("text_muted"))
            icon_lbl = ctk.CTkLabel(row, text="", image=img, width=18)
            icon_lbl.pack(side="left", padx=(0, 4))
            icon_lbl.image = img  # type: ignore[attr-defined]
        except Exception:
            ctk.CTkLabel(
                row,
                text="•",
                width=14,
                text_color=theme.c("primary"),
            ).pack(side="left", padx=(0, 4))
    else:
        ctk.CTkLabel(
            row,
            text="•",
            width=14,
            text_color=theme.c("primary"),
        ).pack(side="left", padx=(0, 4))

    title = ctk.CTkLabel(
        row,
        text=label,
        font=ctk.CTkFont(size=12, weight="bold" if selected else "normal"),
        text_color=theme.c("text_primary"),
        anchor="w",
    )
    title.pack(side="left", fill="x", expand=True, pady=6)

    if count is not None:
        badge = count_badge(row, theme, count)
        badge.pack(side="right", padx=(4, 8), pady=6)

    def _click(_event=None) -> None:
        if on_click:
            on_click()

    for w in row.winfo_children():
        w.bind("<Button-1>", _click)
    row.bind("<Button-1>", _click)
    return row


def simple_table(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    headers: list[str],
    rows: list[list[str]],
) -> ctk.CTkFrame:
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", padx=12, pady=6)
    for i in range(len(headers)):
        wrap.grid_columnconfigure(i, weight=1)

    for col, header in enumerate(headers):
        ctk.CTkLabel(
            wrap,
            text=header,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=theme.c("text_muted"),
            anchor="w",
        ).grid(row=0, column=col, sticky="ew", padx=4, pady=(0, 6))

    for r_idx, row in enumerate(rows, start=1):
        for c_idx, cell in enumerate(row):
            ctk.CTkLabel(
                wrap,
                text=cell,
                font=ctk.CTkFont(size=12),
                text_color=theme.c("text_primary"),
                anchor="w",
                wraplength=180,
                justify="left",
            ).grid(row=r_idx, column=c_idx, sticky="ew", padx=4, pady=2)
    return wrap
