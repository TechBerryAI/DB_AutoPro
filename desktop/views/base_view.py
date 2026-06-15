"""Base view class for desktop application pages."""

from __future__ import annotations

import customtkinter as ctk

from desktop.theme import ThemeManager


class BaseView(ctk.CTkFrame):
    """Base class for all desktop views."""

    def __init__(
        self,
        master: ctk.CTk,
        theme: ThemeManager,
        log_callback=None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.theme = theme
        self.log_callback = log_callback

    def log(self, message: str, level: str = "INFO") -> None:
        if self.log_callback:
            self.log_callback(message, level)

    def create_section_title(self, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            self,
            text=text,
            font=ctk.CTkFont(size=22, weight="bold"),
        )

    def create_progress_section(self) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
        progress = ctk.CTkProgressBar(self, width=400)
        progress.set(0)
        status = ctk.CTkLabel(self, text="Ready", font=ctk.CTkFont(size=12))
        return progress, status
