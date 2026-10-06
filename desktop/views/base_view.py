"""Base view class for desktop application pages."""

from __future__ import annotations

import customtkinter as ctk

from desktop.connection import ConnectionResult
from desktop.employee_store import EmployeeSession
from desktop.design_system import font
from desktop.theme import ThemeManager


class BaseView(ctk.CTkFrame):
    """Base class for all desktop views."""

    def __init__(
        self,
        master: ctk.CTk,
        theme: ThemeManager,
        log_callback=None,
        connection: ConnectionResult | None = None,
        employee: EmployeeSession | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=theme.c("bg_content"), **kwargs)
        self.theme = theme
        self.log_callback = log_callback
        self.connection = connection
        self.employee = employee

    @property
    def owner_username(self) -> str | None:
        return self.employee.username if self.employee else None

    def log(self, message: str, level: str = "INFO") -> None:
        if self.log_callback:
            self.log_callback(message, level)

    def get_active_db_type(self):
        from backend.models.schemas import DatabaseType

        if self.connection:
            return self.connection.db_type
        return DatabaseType.POSTGRESQL

    def get_active_profile(self) -> str:
        return "default"

    def set_connection(self, connection: ConnectionResult | None) -> None:
        """Push the active session connection without rebuilding the page."""
        self.connection = connection
        if hasattr(self, "on_connection_changed"):
            try:
                self.on_connection_changed()
            except Exception:
                pass

    def card_frame(self, parent: ctk.CTkFrame | None = None) -> ctk.CTkFrame:
        """Styled card container matching the enterprise theme.

        DEPRECATED (UI consolidation, docs/ui_plan.md §8): use
        design_system.glass_surface / elevated_card instead.
        """
        target = parent or self
        return ctk.CTkFrame(
            target,
            corner_radius=self.theme.radius("lg"),
            fg_color=self.theme.c("bg_glass") if not self.theme.is_light else self.theme.c("bg_card"),
            border_width=1,
            border_color=self.theme.c("border"),
        )

    def create_connection_context(self, parent: ctk.CTkFrame | None = None) -> ctk.CTkFrame | None:
        """Connection is shown in the sidebar; no redundant strip per page."""
        return None

    def create_section_title(self, text: str, parent: ctk.CTkFrame | None = None) -> ctk.CTkFrame:
        """Shared page title — same style as ops_widgets.page_header."""
        from desktop.ops_widgets import page_header

        target = parent or self
        return page_header(target, self.theme, text, pady=(0, 12))

    def create_progress_section(self) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
        progress = ctk.CTkProgressBar(self)
        progress.set(0)
        status = ctk.CTkLabel(self, text="Ready", font=ctk.CTkFont(size=12))
        return progress, status
