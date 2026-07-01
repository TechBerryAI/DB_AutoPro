"""Reusable enterprise report UI widgets."""

from __future__ import annotations

import customtkinter as ctk

# Enterprise palette (Reports page)
REPORT_BG = "#F5F7FA"
REPORT_PRIMARY = "#2563EB"
REPORT_SUCCESS = "#22C55E"
REPORT_WARNING = "#F59E0B"
REPORT_DANGER = "#EF4444"
REPORT_CARD = "#FFFFFF"

_ACCENT_TINTS = {
    "#22C55E": "#ECFDF5",
    "#EF4444": "#FEF2F2",
    "#2563EB": "#EFF6FF",
    "#7C3AED": "#F5F3FF",
}


def _accent_tint(accent: str) -> str:
    return _ACCENT_TINTS.get(accent, "#F8FAFC")

_FONTS: dict[str, ctk.CTkFont] = {}


def _font(size: int = 11, *, weight: str = "normal") -> ctk.CTkFont:
    key = f"{size}:{weight}"
    if key not in _FONTS:
        _FONTS[key] = ctk.CTkFont(family="Segoe UI", size=size, weight=weight)
    return _FONTS[key]


def _card(parent, *, fg: str = REPORT_CARD) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent,
        corner_radius=12,
        fg_color=fg,
        border_width=1,
        border_color="#E2E8F0",
    )


def report_header_card(
    parent,
    *,
    connection_status: str,
    connected: bool,
) -> ctk.CTkFrame:
    card = _card(parent)
    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=24, pady=20)
    body.grid_columnconfigure(0, weight=1)

    left = ctk.CTkFrame(body, fg_color="transparent")
    left.grid(row=0, column=0, sticky="w")
    ctk.CTkLabel(left, text="Reports", font=_font(22, weight="bold"), text_color="#0F172A", anchor="w").pack(anchor="w")
    ctk.CTkLabel(
        left,
        text="Database performance and automation insights",
        font=_font(12),
        text_color="#64748B",
        anchor="w",
    ).pack(anchor="w", pady=(4, 0))

    right = ctk.CTkFrame(body, fg_color="transparent")
    right.grid(row=0, column=1, sticky="e")

    badge_fg = "#ECFDF5" if connected else "#F1F5F9"
    badge_text = REPORT_SUCCESS if connected else "#64748B"
    ctk.CTkLabel(
        right,
        text=f"● {connection_status}",
        font=_font(weight="bold"),
        text_color=badge_text,
        fg_color=badge_fg,
        corner_radius=8,
        padx=10,
        pady=4,
    ).pack(anchor="e")

    return card


def enterprise_kpi_card(
    parent,
    *,
    title: str,
    value: str,
    icon: str,
    accent: str,
    comparison: str,
    indicator_color: str,
) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]:
    card = _card(parent)
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=18, pady=16)

    top = ctk.CTkFrame(inner, fg_color="transparent")
    top.pack(fill="x")

    icon_box = ctk.CTkFrame(top, width=36, height=36, corner_radius=10, fg_color=_accent_tint(accent))
    icon_box.pack(side="left")
    icon_box.pack_propagate(False)
    ctk.CTkLabel(icon_box, text=icon, font=_font(16), text_color=accent).place(
        relx=0.5, rely=0.5, anchor="center"
    )

    ctk.CTkLabel(top, text=title, font=_font(weight="bold"), text_color="#475569", anchor="w").pack(
        side="left", padx=(10, 0)
    )

    ctk.CTkFrame(top, width=8, height=8, corner_radius=4, fg_color=indicator_color).pack(side="right")

    val_lbl = ctk.CTkLabel(inner, text=value, font=_font(28, weight="bold"), text_color="#0F172A", anchor="w")
    val_lbl.pack(anchor="w", pady=(14, 4))

    cmp_lbl = ctk.CTkLabel(inner, text=comparison, font=_font(), text_color="#94A3B8", anchor="w")
    cmp_lbl.pack(anchor="w")

    return card, val_lbl, cmp_lbl


def metric_chip(parent, label: str, value: str) -> ctk.CTkLabel:
    chip = ctk.CTkFrame(parent, corner_radius=10, fg_color="#F8FAFC", border_width=1, border_color="#E2E8F0")
    inner = ctk.CTkFrame(chip, fg_color="transparent")
    inner.pack(padx=14, pady=10)
    ctk.CTkLabel(inner, text=label, font=_font(), text_color="#64748B", anchor="w").pack(anchor="w")
    val = ctk.CTkLabel(inner, text=value, font=_font(18, weight="bold"), text_color="#0F172A", anchor="w")
    val.pack(anchor="w", pady=(2, 0))
    chip.pack(side="left", padx=(0, 10), pady=4)
    return val


def section_card(parent, title: str) -> ctk.CTkFrame:
    card = _card(parent)
    ctk.CTkLabel(
        card,
        text=title,
        font=_font(14, weight="bold"),
        text_color="#0F172A",
        anchor="w",
    ).pack(anchor="w", padx=20, pady=(16, 8))
    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=20, pady=(0, 16))
    return body


def operation_row(parent, op: dict) -> None:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=6)
    row.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(row, text=op["type"], font=_font(weight="bold"), text_color="#0F172A", width=90, anchor="w").grid(
        row=0, column=0, sticky="w"
    )
    ctk.CTkLabel(
        row,
        text=f"Success {op['success']}  ·  Failed {op['failed']}  ·  Rate {op['success_rate']:.0f}%",
        font=_font(),
        text_color="#64748B",
        anchor="w",
    ).grid(row=0, column=1, sticky="w", padx=(8, 8))

    bar_bg = ctk.CTkFrame(row, height=6, corner_radius=3, fg_color="#E2E8F0")
    bar_bg.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    bar_bg.grid_columnconfigure(0, weight=1)

    rate = max(0, min(100, float(op.get("success_rate", 0))))
    color = REPORT_SUCCESS if rate >= 80 else REPORT_WARNING if rate >= 50 else REPORT_DANGER
    fill = ctk.CTkFrame(bar_bg, height=6, corner_radius=3, fg_color=color)
    fill.place(relx=0, rely=0, relwidth=rate / 100, relheight=1)


def health_tile(parent, label: str, count: int, color: str, icon: str) -> None:
    tile = ctk.CTkFrame(parent, corner_radius=10, fg_color="#F8FAFC", border_width=1, border_color="#E2E8F0")
    tile.pack(side="left", fill="x", expand=True, padx=(0, 10))
    inner = ctk.CTkFrame(tile, fg_color="transparent")
    inner.pack(padx=16, pady=14)
    ctk.CTkLabel(inner, text=icon, font=_font(18), text_color=color).pack(anchor="w")
    ctk.CTkLabel(
        inner,
        text=str(count),
        font=_font(24, weight="bold"),
        text_color="#0F172A",
    ).pack(anchor="w", pady=(4, 0))
    ctk.CTkLabel(inner, text=label, font=_font(), text_color="#64748B").pack(anchor="w")


def analytics_row(parent, label: str, value: str) -> ctk.CTkLabel:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=4)
    ctk.CTkLabel(row, text=label, font=_font(), text_color="#64748B", anchor="w").pack(side="left")
    val = ctk.CTkLabel(row, text=value, font=_font(weight="bold"), text_color="#0F172A", anchor="e")
    val.pack(side="right")
    return val


def activity_table(parent, rows: list[dict]) -> None:
    headers = ("Time", "Operation", "Server", "Database", "Status", "Duration")
    widths = (70, 90, 120, 100, 80, 70)

    header = ctk.CTkFrame(parent, fg_color="#F1F5F9", corner_radius=8)
    header.pack(fill="x", pady=(0, 4))
    for i, (h, w) in enumerate(zip(headers, widths)):
        ctk.CTkLabel(
            header,
            text=h,
            font=_font(weight="bold"),
            text_color="#475569",
            width=w,
            anchor="w",
        ).grid(row=0, column=i, padx=8, pady=8, sticky="w")

    if not rows:
        ctk.CTkLabel(parent, text="No activity for selected filters.", font=_font(), text_color="#94A3B8").pack(
            anchor="w", pady=12
        )
        return

    for row_data in rows:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        values = (
            row_data.get("time", "—"),
            row_data.get("operation", "—"),
            row_data.get("server", "—"),
            row_data.get("database", "—"),
            row_data.get("status", "—"),
            str(row_data.get("duration", "—")),
        )
        for i, (val, w) in enumerate(zip(values, widths)):
            color = "#0F172A"
            if i == 4:
                st = val.lower()
                color = REPORT_SUCCESS if st == "success" else REPORT_DANGER if st == "failed" else "#64748B"
            ctk.CTkLabel(row, text=val, font=_font(), text_color=color, width=w, anchor="w").grid(
                row=0, column=i, padx=8, pady=4, sticky="w"
            )


def toolbar_button(
    parent,
    text: str,
    *,
    primary: bool = False,
    command=None,
) -> ctk.CTkButton:
    if primary:
        return ctk.CTkButton(
            parent,
            text=text,
            height=36,
            corner_radius=8,
            font=_font(weight="bold"),
            fg_color=REPORT_PRIMARY,
            hover_color="#1D4ED8",
            text_color="#FFFFFF",
            command=command,
        )
    return ctk.CTkButton(
        parent,
        text=text,
        height=36,
        corner_radius=8,
        font=_font(weight="bold"),
        fg_color=REPORT_CARD,
        hover_color="#F8FAFC",
        border_width=1,
        border_color="#E2E8F0",
        text_color="#0F172A",
        command=command,
    )
