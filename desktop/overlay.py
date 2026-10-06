"""Single overlay implementation: modal, drawer, stepper, and toast.

Pages should call these instead of building a second dialog stack.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.design_system import danger_button, ghost_button, primary_button, role_font, secondary_button
from desktop.theme import ThemeManager
from desktop.window_icon import schedule_window_icon


def _center(window: ctk.CTkToplevel, width: int, height: int) -> None:
    window.update_idletasks()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


class Modal(ctk.CTkToplevel):
    """Small blocking dialog. Esc cancels. Enter confirms when confirm_on_enter is set."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        title: str,
        width: int = 440,
        height: int = 220,
    ) -> None:
        super().__init__(master)
        self.theme = theme
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=theme.c("surface"))
        schedule_window_icon(self)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())
        _center(self, width, height)
        self.grid_columnconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=theme.spacing("xl"), pady=theme.spacing("lg"))
        self.body.grid_columnconfigure(0, weight=1)
        self.after(10, self._focus)

    def _focus(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def confirm(
    master,
    theme: ThemeManager,
    *,
    title: str,
    message: str,
    confirm_text: str = "Confirm",
    cancel_text: str = "Cancel",
    on_confirm: Callable | None = None,
    on_cancel: Callable | None = None,
    destructive: bool = False,
) -> Modal:
    dialog = Modal(master, theme, title=title, height=240)
    ctk.CTkLabel(
        dialog.body,
        text=title,
        font=role_font(theme, "section"),
        text_color=theme.c("text_primary"),
        anchor="w",
    ).grid(row=0, column=0, columnspan=2, sticky="ew")
    ctk.CTkLabel(
        dialog.body,
        text=message,
        font=role_font(theme, "body"),
        text_color=theme.c("text_secondary"),
        wraplength=380,
        justify="left",
        anchor="w",
    ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(theme.spacing("sm"), theme.spacing("lg")))

    close_dialog = dialog._on_close

    def _accept() -> None:
        close_dialog()
        if on_confirm:
            on_confirm()

    def _reject() -> None:
        close_dialog()
        if on_cancel:
            on_cancel()
    dialog.protocol("WM_DELETE_WINDOW", _reject)
    dialog.bind("<Escape>", lambda _e: _reject())
    dialog.bind("<Return>", lambda _e: _accept())

    actions = ctk.CTkFrame(dialog.body, fg_color="transparent")
    actions.grid(row=2, column=0, columnspan=2, sticky="e")
    if cancel_text:
        ghost_button(actions, theme, text=cancel_text, command=_reject).pack(
            side="left", padx=(0, theme.spacing("sm"))
        )
    action = danger_button if destructive else primary_button
    action(actions, theme, text=confirm_text, command=_accept).pack(side="left")
    return dialog


def detail_panel(
    master,
    theme: ThemeManager,
    *,
    title: str,
    subtitle: str = "",
    body: Callable | None = None,
    width: int = 520,
    accent: str | None = None,
) -> Modal:
    """Centred detail dialog. body(container) fills the scroll area."""
    dialog = Modal(master, theme, title=title, width=width, height=420)
    dialog.resizable(True, True)
    ctk.CTkLabel(
        dialog.body,
        text=title,
        font=role_font(theme, "section"),
        text_color=accent or theme.c("text_primary"),
        anchor="w",
    ).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(
            dialog.body,
            text=subtitle,
            font=role_font(theme, "metadata"),
            text_color=theme.c("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(theme.spacing("xs"), theme.spacing("sm")))
    content = ctk.CTkFrame(dialog.body, fg_color="transparent")
    content.pack(fill="both", expand=True)
    if body:
        body(content)
    return dialog


def make_clickable(
    widget,
    theme: ThemeManager,
    command: Callable,
    *,
    hover_border: str | None = None,
) -> None:
    """Make a card open a detail view. Hover uses a token border colour."""
    normal = theme.c("border")
    hover = hover_border or theme.c("border_focus")

    def _enter(_event=None) -> None:
        if widget.winfo_exists():
            widget.configure(border_width=1, border_color=hover)

    def _leave(_event=None) -> None:
        if widget.winfo_exists():
            widget.configure(border_color=normal)

    def _click(_event=None) -> None:
        command()

    def _bind_clicks(node) -> None:
        node.bind("<Button-1>", _click)
        for child in node.winfo_children():
            _bind_clicks(child)

    widget.bind("<Enter>", _enter)
    widget.bind("<Leave>", _leave)
    _bind_clicks(widget)


def alert(
    master,
    theme: ThemeManager,
    *,
    title: str,
    message: str,
    button_text: str = "OK",
) -> Modal:
    return confirm(
        master,
        theme,
        title=title,
        message=message,
        confirm_text=button_text,
        cancel_text="",
        on_confirm=None,
    )


class Drawer(ctk.CTkFrame):
    """Right-side detail panel. Non-modal: the page stays visible beside it."""

    def __init__(self, master, theme: ThemeManager, *, width: int = 360) -> None:
        super().__init__(
            master,
            width=width,
            fg_color=theme.c("surface"),
            border_width=1,
            border_color=theme.c("border"),
            corner_radius=0,
        )
        self.theme = theme
        self._width = width
        self.grid_propagate(False)
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=theme.spacing("lg"), pady=(theme.spacing("lg"), theme.spacing("sm")))
        self.title_label = ctk.CTkLabel(
            self.header,
            text="",
            font=role_font(theme, "section"),
            text_color=theme.c("text_primary"),
            anchor="w",
        )
        self.title_label.pack(side="left", fill="x", expand=True)
        ghost_button(self.header, theme, text="Close", command=self.close).pack(side="right")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=theme.spacing("lg"), pady=(0, theme.spacing("lg")))
        self._esc_binding: str | None = None

    def open(self, title: str) -> None:
        self.title_label.configure(text=title)
        if not self.winfo_manager():
            self.pack(side="right", fill="y")
        top = self.winfo_toplevel()
        self._esc_binding = top.bind("<Escape>", lambda _e: self.close(), add="+")

    def close(self) -> None:
        if self.winfo_manager():
            self.pack_forget()
        if self._esc_binding:
            try:
                self.winfo_toplevel().unbind("<Escape>", self._esc_binding)
            except Exception:
                pass
            self._esc_binding = None


class Stepper(ctk.CTkFrame):
    """Horizontal step labels for a wizard. Call set_step to mark progress."""

    def __init__(self, master, theme: ThemeManager, steps: list[str]) -> None:
        super().__init__(master, fg_color="transparent")
        self.theme = theme
        self._labels: list[ctk.CTkLabel] = []
        for index, name in enumerate(steps):
            label = ctk.CTkLabel(
                self,
                text=f"{index + 1}. {name}",
                font=role_font(theme, "label"),
                text_color=theme.c("text_muted"),
            )
            label.pack(side="left", padx=(0, theme.spacing("lg")))
            self._labels.append(label)
        self.set_step(0)

    def set_step(self, index: int) -> None:
        for position, label in enumerate(self._labels):
            if position == index:
                label.configure(text_color=self.theme.c("primary"))
            elif position < index:
                label.configure(text_color=self.theme.c("text_secondary"))
            else:
                label.configure(text_color=self.theme.c("text_muted"))


class Toast(ctk.CTkFrame):
    """Short status line. Removes itself after a few seconds."""

    def __init__(self, master, theme: ThemeManager, message: str, *, tone: str = "info") -> None:
        color = theme.c("info") if tone == "info" else theme.c(tone if tone in ("success", "warning", "error") else "text_primary")
        super().__init__(
            master,
            fg_color=theme.c("surface_raised"),
            corner_radius=theme.radius("md"),
            border_width=1,
            border_color=theme.c("border"),
        )
        ctk.CTkLabel(
            self,
            text=message,
            font=role_font(theme, "body"),
            text_color=color,
        ).pack(padx=theme.spacing("lg"), pady=theme.spacing("sm"))
        self.pack(anchor="e", padx=theme.spacing("lg"), pady=theme.spacing("sm"))
        self.after(4000, self._dismiss)

    def _dismiss(self) -> None:
        if self.winfo_exists():
            self.destroy()
