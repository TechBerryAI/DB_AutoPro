"""Enterprise calendar date picker for CustomTkinter."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Callable

import customtkinter as ctk

from desktop.report_widgets import REPORT_CARD, REPORT_PRIMARY, _font

_WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class DatePicker(ctk.CTkFrame):
    """Date field with a popup calendar for picking YYYY-MM-DD values."""

    def __init__(
        self,
        master,
        variable: ctk.StringVar,
        *,
        width: int = 132,
        on_change: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.variable = variable
        self.on_change = on_change
        self._popup: ctk.CTkToplevel | None = None
        self._view_year = date.today().year
        self._view_month = date.today().month
        self._days_frame: ctk.CTkFrame | None = None
        self._month_label: ctk.CTkLabel | None = None

        self.entry = ctk.CTkEntry(
            self,
            textvariable=variable,
            width=width,
            height=32,
            font=_font(),
            fg_color="#F8FAFC",
            border_color="#E2E8F0",
        )
        self.entry.pack(side="left")
        self.entry.bind("<Key>", lambda _e: "break")
        self.entry.bind("<Button-1>", lambda _e: self._open_calendar())

        ctk.CTkButton(
            self,
            text="Cal",
            width=40,
            height=32,
            font=_font(14),
            fg_color="#EFF6FF",
            hover_color="#DBEAFE",
            text_color=REPORT_PRIMARY,
            corner_radius=8,
            command=self._open_calendar,
        ).pack(side="left", padx=(6, 0))

    def _parse_current(self) -> date | None:
        try:
            return datetime.strptime(self.variable.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    def _open_calendar(self) -> None:
        if self._popup and self._popup.winfo_exists():
            self._popup.lift()
            self._popup.focus()
            return

        selected = self._parse_current() or date.today()
        self._view_year = selected.year
        self._view_month = selected.month

        root = self.winfo_toplevel()
        self._popup = ctk.CTkToplevel(root)
        self._popup.title("Select date")
        self._popup.resizable(False, False)
        self._popup.transient(root)
        self._popup.grab_set()

        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        self._popup.geometry(f"+{x}+{y}")

        card = ctk.CTkFrame(
            self._popup,
            corner_radius=12,
            fg_color=REPORT_CARD,
            border_width=1,
            border_color="#E2E8F0",
        )
        card.pack(padx=8, pady=8)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))

        ctk.CTkButton(
            header,
            text="‹",
            width=32,
            height=28,
            font=_font(16, weight="bold"),
            fg_color="#F8FAFC",
            hover_color="#E2E8F0",
            text_color="#334155",
            command=self._prev_month,
        ).pack(side="left")

        self._month_label = ctk.CTkLabel(
            header,
            text="",
            font=_font(13, weight="bold"),
            text_color="#0F172A",
        )
        self._month_label.pack(side="left", expand=True)

        ctk.CTkButton(
            header,
            text="›",
            width=32,
            height=28,
            font=_font(16, weight="bold"),
            fg_color="#F8FAFC",
            hover_color="#E2E8F0",
            text_color="#334155",
            command=self._next_month,
        ).pack(side="right")

        weekdays = ctk.CTkFrame(card, fg_color="transparent")
        weekdays.pack(fill="x", padx=12)
        for label in _WEEKDAYS:
            ctk.CTkLabel(
                weekdays,
                text=label,
                width=36,
                font=_font(weight="bold"),
                text_color="#64748B",
            ).pack(side="left", padx=1)

        self._days_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._days_frame.pack(padx=12, pady=(4, 12))

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(
            footer,
            text="Today",
            height=28,
            font=_font(weight="bold"),
            fg_color="#F8FAFC",
            hover_color="#E2E8F0",
            text_color="#334155",
            command=self._pick_today,
        ).pack(side="left")
        ctk.CTkButton(
            footer,
            text="Close",
            height=28,
            font=_font(weight="bold"),
            fg_color=REPORT_PRIMARY,
            hover_color="#1D4ED8",
            text_color="#FFFFFF",
            command=self._close_popup,
        ).pack(side="right")

        self._render_days()
        self._popup.protocol("WM_DELETE_WINDOW", self._close_popup)

    def _close_popup(self) -> None:
        if self._popup and self._popup.winfo_exists():
            self._popup.grab_release()
            self._popup.destroy()
        self._popup = None

    def _prev_month(self) -> None:
        if self._view_month == 1:
            self._view_month = 12
            self._view_year -= 1
        else:
            self._view_month -= 1
        self._render_days()

    def _next_month(self) -> None:
        if self._view_month == 12:
            self._view_month = 1
            self._view_year += 1
        else:
            self._view_month += 1
        self._render_days()

    def _pick_today(self) -> None:
        self._select_date(date.today())

    def _select_date(self, picked: date) -> None:
        self.variable.set(picked.strftime("%Y-%m-%d"))
        self._close_popup()
        if self.on_change:
            self.on_change()

    def _render_days(self) -> None:
        if not self._days_frame or not self._month_label:
            return

        self._month_label.configure(text=f"{_MONTHS[self._view_month - 1]} {self._view_year}")

        for child in self._days_frame.winfo_children():
            child.destroy()

        calendar.setfirstweekday(calendar.MONDAY)
        weeks = calendar.monthcalendar(self._view_year, self._view_month)
        selected = self._parse_current()
        today = date.today()

        for week in weeks:
            row = ctk.CTkFrame(self._days_frame, fg_color="transparent")
            row.pack(pady=1)
            for day_num in week:
                if day_num == 0:
                    ctk.CTkFrame(row, width=36, height=30, fg_color="transparent").pack(
                        side="left", padx=1
                    )
                    continue

                picked = date(self._view_year, self._view_month, day_num)
                is_selected = selected == picked
                is_today = today == picked

                fg = REPORT_PRIMARY if is_selected else "#F8FAFC" if is_today else "transparent"
                text_color = "#FFFFFF" if is_selected else REPORT_PRIMARY if is_today else "#0F172A"
                hover = "#1D4ED8" if is_selected else "#EFF6FF"

                btn = ctk.CTkButton(
                    row,
                    text=str(day_num),
                    width=36,
                    height=30,
                    font=_font(weight="bold" if is_selected or is_today else "normal"),
                    fg_color=fg,
                    hover_color=hover,
                    text_color=text_color,
                    corner_radius=8,
                    command=lambda d=picked: self._select_date(d),
                )
                btn.pack(side="left", padx=1)
