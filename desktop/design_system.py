"""Shared design tokens and widget factories for the desktop UI."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.theme import ThemeManager

FONT_FAMILY = "Segoe UI"


def font(theme: ThemeManager, size: int, *, weight: str = "normal") -> ctk.CTkFont:
    return theme.font(size, weight=weight)


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
    height: int = 36,
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
    height: int = 36,
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
    height: int = 36,
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
    width: int = 420,
) -> ctk.CTkEntry:
    kwargs: dict = {
        "width": width,
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
        "info": (theme.c("info_muted"), theme.c("info")),
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
    from desktop.theme import themed_scrollable_frame

    scroll = themed_scrollable_frame(parent, theme)
    scroll.grid_columnconfigure(0, weight=1)
    return scroll


def dropdown(
    parent,
    theme: ThemeManager,
    *,
    values: list[str],
    variable: ctk.StringVar | None = None,
    command: Callable[[str], None] | None = None,
    width: int = 220,
    height: int = 40,
) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        parent,
        values=values,
        variable=variable,
        command=command,
        width=width,
        height=height,
        corner_radius=theme.radius("md"),
        fg_color=theme.c("bg_input"),
        button_color=theme.c("bg_elevated"),
        button_hover_color=theme.c("bg_card_hover"),
        dropdown_fg_color=theme.c("bg_card"),
        dropdown_hover_color=theme.c("bg_card_hover"),
        dropdown_text_color=theme.c("text_primary"),
        text_color=theme.c("text_primary"),
        font=font(theme, 13),
    )


def danger_button(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    command: Callable | None = None,
    width: int | None = None,
    height: int = 36,
) -> ctk.CTkButton:
    """Danger action. Same widget as destructive_button."""
    return destructive_button(
        parent, theme, text=text, command=command, width=width, height=height
    )


def status_badge(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    tone: str = "neutral",
) -> ctk.CTkLabel:
    """Status label. Always includes visible text, never a color alone."""
    return status_chip(parent, theme, text=text, tone=tone)


def role_font(theme: ThemeManager, role: str) -> ctk.CTkFont:
    size, weight = theme.type_scale(role)
    return font(theme, size, weight=weight)


def card(
    parent,
    theme: ThemeManager,
    *,
    title: str = "",
) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """Flat surface card. Returns (shell, body)."""
    shell = ctk.CTkFrame(
        parent,
        corner_radius=theme.radius("lg"),
        fg_color=theme.c("surface"),
        border_width=1,
        border_color=theme.c("border"),
    )
    body = ctk.CTkFrame(shell, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=theme.spacing("lg"), pady=theme.spacing("lg"))
    if title:
        ctk.CTkLabel(
            body,
            text=title,
            font=role_font(theme, "card_title"),
            text_color=theme.c("text_primary"),
            anchor="w",
        ).pack(anchor="w", pady=(0, theme.spacing("sm")))
    return shell, body


def data_table(
    parent,
    theme: ThemeManager,
    *,
    columns: list[tuple[str, str]],
    height: int = 240,
) -> "ctk.CTkFrame":
    """Sortable ttk table. columns are (id, heading). Returns a frame with .tree."""
    import tkinter.ttk as ttk

    shell = ctk.CTkFrame(parent, fg_color=theme.c("surface"), corner_radius=theme.radius("md"))
    style = ttk.Style(shell)
    style_name = f"Dap.{theme.current}.Treeview"
    style.configure(
        style_name,
        background=theme.c("surface"),
        fieldbackground=theme.c("surface"),
        foreground=theme.c("text_primary"),
        borderwidth=0,
        rowheight=28,
        font=("Segoe UI", 12),
    )
    style.configure(
        f"{style_name}.Heading",
        background=theme.c("surface_raised"),
        foreground=theme.c("text_secondary"),
        font=("Segoe UI", 11, "bold"),
    )
    style.map(style_name, background=[("selected", theme.c("primary_muted"))])

    tree = ttk.Treeview(
        shell,
        columns=[col_id for col_id, _ in columns],
        show="headings",
        height=max(4, height // 28),
        style=style_name,
    )
    for col_id, heading in columns:
        tree.heading(col_id, text=heading, command=lambda c=col_id: _sort_tree(tree, c))
        tree.column(col_id, anchor="w", stretch=True)
    tree.pack(fill="both", expand=True, padx=1, pady=1)
    shell.tree = tree  # type: ignore[attr-defined]
    return shell


def _sort_tree(tree, column: str) -> None:
    rows = [(tree.set(item, column), item) for item in tree.get_children("")]
    reverse = bool(getattr(tree, "_sort_reverse", {}).get(column))
    rows.sort(key=lambda pair: pair[0].lower(), reverse=reverse)
    for index, (_, item) in enumerate(rows):
        tree.move(item, "", index)
    flags = getattr(tree, "_sort_reverse", {})
    flags[column] = not reverse
    tree._sort_reverse = flags  # type: ignore[attr-defined]


def empty_state(
    parent,
    theme: ThemeManager,
    *,
    title: str,
    message: str,
) -> ctk.CTkFrame:
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        frame, text=title, font=role_font(theme, "section"), text_color=theme.c("text_primary")
    ).pack(anchor="w")
    ctk.CTkLabel(
        frame,
        text=message,
        font=role_font(theme, "body"),
        text_color=theme.c("text_secondary"),
        wraplength=420,
        justify="left",
    ).pack(anchor="w", pady=(theme.spacing("xs"), 0))
    return frame


def loading_state(parent, theme: ThemeManager, *, message: str = "Loading") -> ctk.CTkFrame:
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        frame, text=message, font=role_font(theme, "body"), text_color=theme.c("text_secondary")
    ).pack(anchor="w")
    return frame


def error_state(
    parent,
    theme: ThemeManager,
    *,
    title: str,
    message: str,
    detail: str = "",
    on_retry: Callable | None = None,
) -> ctk.CTkFrame:
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        frame, text=title, font=role_font(theme, "section"), text_color=theme.c("error")
    ).pack(anchor="w")
    ctk.CTkLabel(
        frame,
        text=message,
        font=role_font(theme, "body"),
        text_color=theme.c("text_secondary"),
        wraplength=480,
        justify="left",
    ).pack(anchor="w", pady=(theme.spacing("xs"), 0))
    if detail:
        ctk.CTkLabel(
            frame,
            text=detail,
            font=role_font(theme, "metadata"),
            text_color=theme.c("text_muted"),
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(theme.spacing("sm"), 0))
    if on_retry:
        secondary_button(frame, theme, text="Retry", command=on_retry).pack(
            anchor="w", pady=(theme.spacing("md"), 0)
        )
    return frame


def confirm(*args, **kwargs):
    from desktop.overlay import confirm as _confirm

    return _confirm(*args, **kwargs)


def alert(*args, **kwargs):
    from desktop.overlay import alert as _alert

    return _alert(*args, **kwargs)


def checkbox(
    parent,
    theme: ThemeManager,
    *,
    text: str,
    variable: ctk.BooleanVar | None = None,
    command: Callable[[], None] | None = None,
) -> ctk.CTkCheckBox:
    return ctk.CTkCheckBox(
        parent,
        text=text,
        variable=variable,
        command=command,
        corner_radius=theme.radius("sm"),
        fg_color=theme.c("primary"),
        hover_color=theme.c("primary_hover"),
        checkmark_color=theme.c("text_on_primary"),
        border_color=theme.c("border"),
        text_color=theme.c("text_primary"),
        font=font(theme, 13),
    )
