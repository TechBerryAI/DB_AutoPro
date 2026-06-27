"""Database browser view for the connected server."""

from __future__ import annotations

import customtkinter as ctk

from desktop.db_catalog import fetch_server_databases
from desktop.theme import ThemeManager
from desktop.ui_components import panel_card, section_header
from desktop.views.base_view import BaseView


class DatabasesView(BaseView):
    """List databases on the active server connection."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self._list_frame: ctk.CTkFrame | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))
        section_header(
            header,
            self.theme,
            "Databases",
            "PostgreSQL, MySQL, MongoDB, Oracle, and SQL Server on the active connection",
        )

        card = panel_card(scroll, self.theme, "Database Inventory")
        card.pack(fill="x", pady=(0, 12))
        self._list_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._list_frame.pack(fill="x", padx=20, pady=(0, 20))

    def refresh(self) -> None:
        if not self._list_frame:
            return
        for child in self._list_frame.winfo_children():
            child.destroy()

        if not self.connection:
            ctk.CTkLabel(
                self._list_frame,
                text="Connect to a server first (Servers → Connect to Server).",
                font=ctk.CTkFont(size=13),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", pady=8)
            return

        try:
            databases = fetch_server_databases(self.connection)
        except Exception as exc:
            ctk.CTkLabel(
                self._list_frame,
                text=f"Could not load databases: {exc}",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("error"),
                anchor="w",
                wraplength=640,
                justify="left",
            ).pack(anchor="w", pady=8)
            return

        if not databases:
            ctk.CTkLabel(
                self._list_frame,
                text="No databases found on this server.",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", pady=8)
            return

        for db in databases:
            name = db.get("name", "—")
            size = db.get("size_mb") or db.get("size")
            if isinstance(size, (int, float)):
                size_text = f"{size:.1f} MB"
            elif size:
                size_text = str(size)
            else:
                size_text = "—"
            row = ctk.CTkFrame(
                self._list_frame,
                fg_color=self.theme.c("bg_elevated"),
                corner_radius=8,
            )
            row.pack(fill="x", pady=4)
            ctk.CTkLabel(
                row,
                text=name,
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(side="left", padx=14, pady=12)
            ctk.CTkLabel(
                row,
                text=size_text,
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                anchor="e",
            ).pack(side="right", padx=14, pady=12)
