"""Dedicated execution log view."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.execution_log import ExecutionLogPanel, ExecutionLogStore
from desktop.theme import ThemeManager
from desktop.ops_widgets import page_header
from desktop.views.base_view import BaseView


class LogsView(BaseView):
    """Full-page execution log with filter, search, and auto-scroll."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        log_store: ExecutionLogStore | None = None,
        register_log_panel: Callable[[ExecutionLogPanel | None], None] | None = None,
        log_filter: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self._register_log_panel = register_log_panel
        self._panel: ExecutionLogPanel | None = None
        self._build_ui(log_store)
        if log_filter:
            self.apply_log_filter(log_filter)

    def _build_ui(self, log_store: ExecutionLogStore | None) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 8))
        page_header(
            header,
            self.theme,
            "Execution Log",
            subtitle="Application activity, navigation, and operation messages",
        )

        self._panel = ExecutionLogPanel(self, self.theme, store=log_store)
        self._panel.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 16))

        if self._register_log_panel:
            self._register_log_panel(self._panel)

    def apply_log_filter(self, preset: str | None) -> None:
        """Apply a dashboard deep-link preset (alerts | jobs | None)."""
        if self._panel is not None:
            self._panel.apply_preset_filter(preset)

    def refresh(self, *, force: bool = False) -> None:
        """Re-render the execution log panel from the shared store."""
        del force
        if self._panel is not None and hasattr(self._panel, "refresh"):
            self._panel.refresh()
        self.log("Execution log refreshed")

    def destroy(self) -> None:
        if self._register_log_panel:
            self._register_log_panel(None)
        super().destroy()
