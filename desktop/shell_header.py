"""Shell header: sidebar toggle + refresh stamp + theme toggle."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import customtkinter as ctk

from desktop.connection import ConnectionResult
from desktop.design_system import font, secondary_button
from desktop.employee_store import EmployeeSession
from desktop.lucide_icons import lucide_ctk_image
from desktop.theme import ThemeManager


def format_updated_stamp(when: datetime | None = None) -> str:
    """Single display format for the shell refresh time, e.g. '10:14:02 AM'."""
    stamp = when or datetime.now()
    return stamp.strftime("%I:%M:%S %p").lstrip("0")


def role_label(employee: EmployeeSession | None) -> str:
    if not employee:
        return "Operator"
    roles = {"admin": "Administrator", "operator": "Operator", "viewer": "Viewer"}
    return roles.get(employee.role.value, employee.role.value.title())


def initials(employee: EmployeeSession | None) -> str:
    if not employee:
        return "U"
    parts = [p for p in employee.full_name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (employee.full_name[:2] or "U").upper()


def display_name(employee: EmployeeSession | None) -> str:
    if not employee:
        return "User"
    return employee.full_name or employee.username or "User"


class ShellHeader(ctk.CTkFrame):
    """Top chrome: toggle left, Updated + Refresh + Light/Dark right."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        employee: EmployeeSession | None = None,
        connection: ConnectionResult | None = None,
        on_refresh: Callable[[], None] | None = None,
        on_theme: Callable[[], None] | None = None,
        on_toggle_sidebar: Callable[[], None] | None = None,
        sidebar_collapsed: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            height=48,
            corner_radius=0,
            fg_color=theme.c("bg_content"),
            border_width=0,
            **kwargs,
        )
        self.theme = theme
        self.employee = employee
        self.connection = connection
        self._on_refresh = on_refresh
        self._on_theme = on_theme
        self._on_toggle_sidebar = on_toggle_sidebar
        self._sidebar_collapsed = sidebar_collapsed
        self._last_updated: datetime | None = None
        self._updating = True
        self._refreshing = False
        self.grid_propagate(False)
        self._build()
        self.set_connection(connection)

    def _build(self) -> None:
        ctk.CTkFrame(self, height=1, fg_color=self.theme.c("border_subtle")).pack(
            side="bottom", fill="x"
        )
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="both", expand=True, padx=16, pady=6)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="y")

        self._sidebar_btn = ctk.CTkButton(
            left,
            text="‹",
            width=32,
            height=30,
            corner_radius=self.theme.radius("sm"),
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_secondary"),
            font=font(self.theme, 16),
            command=self._handle_toggle_sidebar,
        )
        self._sidebar_btn.pack(side="left")
        self.set_sidebar_collapsed(self._sidebar_collapsed)

        # Action row — let natural width drive packing so Light/Dark stays clickable
        # (fixed pack_propagate(False) frames clipped the theme button hit target).
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right")
        self._header_actions = right

        self._updated_label = ctk.CTkLabel(
            right,
            text="Updating...",
            font=font(self.theme, 11),
            text_color=self.theme.c("text_muted"),
        )
        self._updated_label.pack(side="left", padx=(0, 10))

        refresh_img = lucide_ctk_image("refresh-cw", self.theme.c("text_primary"), size=14)
        self._refresh_btn = secondary_button(
            right,
            self.theme,
            text="Refresh",
            command=self._handle_refresh,
            height=30,
            width=96,
        )
        self._refresh_btn.configure(image=refresh_img, compound="left")
        self._refresh_btn._lucide_image = refresh_img  # type: ignore[attr-defined]
        self._refresh_btn.pack(side="left", padx=(0, 8))

        # Light/Dark — same secondary chrome as Refresh (replaces Account menu).
        self._theme_btn = secondary_button(
            right,
            self.theme,
            text="Dark",
            command=self._handle_theme,
            height=30,
            width=96,
        )
        self._theme_btn.pack(side="left")
        self._sync_theme_button()

    def _theme_target(self) -> tuple[str, str]:
        """Return (label, lucide key) for the theme we switch TO."""
        if self.theme.current == "dark":
            return "Light", "theme_light"
        return "Dark", "theme_dark"

    def _sync_theme_button(self) -> None:
        label, icon_key = self._theme_target()
        color = self.theme.c("text_primary")
        img = lucide_ctk_image(icon_key, color, size=14)
        # Keep command wired — configure(image=...) must not drop the handler.
        self._theme_btn.configure(
            text=f" {label}",
            image=img,
            compound="left",
            width=96,
            command=self._handle_theme,
        )
        self._theme_btn._lucide_image = img  # type: ignore[attr-defined]

    def _handle_refresh(self) -> None:
        if self._refreshing:
            return
        if self._on_refresh:
            self._on_refresh()

    def set_refreshing(self, busy: bool) -> None:
        """Disable Refresh and show a loading label while a page reload runs."""
        self._refreshing = bool(busy)
        btn = getattr(self, "_refresh_btn", None)
        if btn is None:
            return
        if busy:
            self.set_updating()
            color = self.theme.c("text_muted")
            img = lucide_ctk_image("refresh-cw", color, size=14)
            btn.configure(
                text=" Loading…",
                image=img,
                compound="left",
                state="disabled",
                text_color=color,
                fg_color=self.theme.c("bg_elevated"),
                border_color=self.theme.c("border_subtle"),
                width=110,
                command=self._handle_refresh,
            )
            btn._lucide_image = img  # type: ignore[attr-defined]
            return
        color = self.theme.c("text_primary")
        img = lucide_ctk_image("refresh-cw", color, size=14)
        btn.configure(
            text="Refresh",
            image=img,
            compound="left",
            state="normal",
            text_color=color,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            border_color=self.theme.c("border"),
            width=96,
            command=self._handle_refresh,
        )
        btn._lucide_image = img  # type: ignore[attr-defined]

    def _handle_theme(self) -> None:
        if self._on_theme:
            self._on_theme()

    def _handle_toggle_sidebar(self) -> None:
        if self._on_toggle_sidebar:
            self._on_toggle_sidebar()

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        """Update the header arrow: ‹ = hide, › = show."""
        self._sidebar_collapsed = bool(collapsed)
        if getattr(self, "_sidebar_btn", None) is None:
            return
        self._sidebar_btn.configure(text="›" if self._sidebar_collapsed else "‹")

    def set_connection(self, connection: ConnectionResult | None) -> None:
        """Connection is shown in the status footer — keep API for app.py callers."""
        self.connection = connection

    def set_updating(self) -> None:
        """Show Updating... until the first (or next) refresh completes."""
        self._updating = True
        self._updated_label.configure(text="Updating...")

    def set_last_updated(self, when: datetime | str | None = None) -> None:
        """Set the shared last-updated stamp shown next to Refresh."""
        if when is None:
            self.set_updating()
            return
        if isinstance(when, datetime):
            self._last_updated = when
            stamp = format_updated_stamp(when)
        else:
            text = str(when).strip()
            if not text or text in {"—", "-", "…", "..."}:
                self.set_updating()
                return
            lower = text.lower()
            if lower.startswith("updating"):
                self.set_updating()
                return
            if lower.startswith("updated "):
                text = text[8:].strip()
            self._last_updated = datetime.now()
            stamp = text
        self._updating = False
        self._updated_label.configure(text=f"Updated {stamp}")

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        self.configure(fg_color=theme.c("bg_content"))
        self._updated_label.configure(text_color=theme.c("text_muted"))
        self._sidebar_btn.configure(
            fg_color=theme.c("bg_elevated"),
            hover_color=theme.c("bg_card_hover"),
            border_color=theme.c("border"),
            text_color=theme.c("text_secondary"),
        )
        self.set_sidebar_collapsed(self._sidebar_collapsed)
        for btn in (self._refresh_btn, self._theme_btn):
            btn.configure(
                fg_color=theme.c("bg_elevated"),
                hover_color=theme.c("bg_card_hover"),
                border_color=theme.c("border"),
                text_color=theme.c("text_primary"),
            )
        self._sync_theme_button()
        self.set_connection(self.connection)
        if self._refreshing:
            self.set_refreshing(True)
        elif self._updating or self._last_updated is None:
            self.set_updating()
            self.set_refreshing(False)
        else:
            self.set_last_updated(self._last_updated)
            self.set_refreshing(False)
        # Re-bind after theme restyle so Light/Dark always fires.
        self._theme_btn.configure(command=self._handle_theme)
