"""Shared design tokens and widget factories for the desktop UI."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.theme import ThemeManager

FONT_FAMILY = "Segoe UI"


def font(theme: ThemeManager, size: int, *, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def glass_surface(
    parent,
    theme: ThemeManager,
    *,
    radius: str = "lg",
    accent: str | None = None,
    hover: bool = True,
) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """Layered glass panel with optional accent strip; returns (shell, inner)."""
    shell = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius(radius),
        fg_color=theme.c("bg_glass") if not theme.is_light else theme.c("bg_card"),
        border_width=1,
        border_color=theme.c("border"),
    )
    if accent:
        ctk.CTkFrame(shell, height=3, corner_radius=2, fg_color=accent).pack(fill="x", padx=1, pady=(1, 0))
    inner = ctk.CTkFrame(shell, fg_color="transparent")
    inner.pack(fill="both", expand=True)
    if hover:
        normal = theme.c("border")
        hover_c = accent or theme.c("border_focus")
        shell.bind("<Enter>", lambda _: shell.configure(border_color=hover_c))
        shell.bind("<Leave>", lambda _: shell.configure(border_color=normal))
    return shell, inner


def elevated_card(
    parent: ctk.CTkFrame,
    theme: ThemeManager,
    *,
    accent: str | None = None,
    hover: bool = True,
) -> ctk.CTkFrame:
    """Layered card with optional top accent strip (depth / glass feel)."""
    shell, inner = glass_surface(parent, theme, accent=accent, hover=hover)
    shell._inner = inner  # type: ignore[attr-defined]
    return shell


def card_inner(card: ctk.CTkFrame) -> ctk.CTkFrame:
    return getattr(card, "_inner", card)


def primary_button(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    command: Callable | None = None,
    width: int | None = None,
    height: int = 44,
) -> ctk.CTkButton:
    kwargs: dict = {
        "text": text,
        "command": command,
        "height": height,
        "corner_radius": theme.radius("md"),
        "fg_color": theme.c("primary"),
        "hover_color": theme.c("primary_hover"),
        "text_color": theme.c("text_on_primary"),
        "font": font(theme, 13, weight="bold"),
        "border_width": 0,
    }
    if width is not None:
        kwargs["width"] = width
    return ctk.CTkButton(parent, **kwargs)


def secondary_button(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    command: Callable | None = None,
    width: int | None = None,
    height: int = 44,
) -> ctk.CTkButton:
    kwargs: dict = {
        "text": text,
        "command": command,
        "height": height,
        "corner_radius": theme.radius("md"),
        "fg_color": theme.c("bg_elevated"),
        "hover_color": theme.c("bg_card_hover"),
        "border_width": 1,
        "border_color": theme.c("border"),
        "text_color": theme.c("text_primary"),
        "font": font(theme, 13),
    }
    if width is not None:
        kwargs["width"] = width
    return ctk.CTkButton(parent, **kwargs)


def ghost_button(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    command: Callable | None = None,
    height: int = 36,
) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        height=height,
        corner_radius=theme.radius("md"),
        fg_color="transparent",
        hover_color=theme.c("primary_muted"),
        text_color=theme.c("primary"),
        font=font(theme, 13, weight="bold"),
    )


def destructive_button(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    command: Callable | None = None,
    width: int | None = None,
    height: int = 44,
) -> ctk.CTkButton:
    kwargs: dict = {
        "text": text,
        "command": command,
        "height": height,
        "corner_radius": theme.radius("md"),
        "fg_color": theme.c("destructive_bg"),
        "hover_color": theme.c("destructive_hover"),
        "text_color": theme.c("destructive_text"),
        "font": font(theme, 13, weight="bold"),
    }
    if width is not None:
        kwargs["width"] = width
    return ctk.CTkButton(parent, **kwargs)


def text_input(
    parent,
    theme: ThemeManager,
    *,
    placeholder: str = "",
    height: int = 44,
    show: str | None = None,
) -> ctk.CTkEntry:
    kwargs: dict = {
        "height": height,
        "placeholder_text": placeholder,
        "corner_radius": theme.radius("md"),
        "fg_color": theme.c("bg_input"),
        "border_color": theme.c("border"),
        "border_width": 1,
        "text_color": theme.c("text_primary"),
        "placeholder_text_color": theme.c("text_muted"),
        "font": font(theme, 13),
    }
    if show is not None:
        kwargs["show"] = show
    return ctk.CTkEntry(parent, **kwargs)


def status_chip(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    tone: str = "neutral",
) -> ctk.CTkLabel:
    tones = {
        "neutral": (theme.c("bg_elevated"), theme.c("text_secondary")),
        "success": (theme.c("success_muted"), theme.c("success")),
        "warning": (theme.c("warning_muted"), theme.c("warning")),
        "error": (theme.c("error_muted"), theme.c("error")),
        "primary": (theme.c("primary_muted"), theme.c("primary")),
    }
    bg, fg = tones.get(tone, tones["neutral"])
    return ctk.CTkLabel(
        parent,
        text=text,
        font=font(theme, 10, weight="bold"),
        text_color=fg,
        fg_color=bg,
        corner_radius=theme.radius("pill"),
        padx=12,
        pady=5,
    )


def page_scroll(parent, theme: ThemeManager) -> ctk.CTkScrollableFrame:
    scroll = ctk.CTkScrollableFrame(
        parent,
        fg_color="transparent",
        scrollbar_button_color=theme.c("bg_elevated"),
        scrollbar_button_hover_color=theme.c("primary"),
    )
    scroll.grid_columnconfigure(0, weight=1)
    return scroll
