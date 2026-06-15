"""Sidebar database list for the main application shell."""

from __future__ import annotations

import customtkinter as ctk

from desktop.connection import ConnectionResult
from desktop.db_catalog import fetch_server_databases
from desktop.theme import ThemeManager


class SidebarDatabasePanel(ctk.CTkFrame):
    """Scrollable list of databases on the connected server."""

    def __init__(self, master, theme: ThemeManager, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.theme = theme
        self._server_label: ctk.CTkLabel | None = None
        self._count_label: ctk.CTkLabel | None = None
        self._list_host: ctk.CTkScrollableFrame | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            header,
            text="DATABASES",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self.theme.c("text_muted"),
        ).pack(side="left")

        self._count_label = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self.theme.c("text_secondary"),
        )
        self._count_label.pack(side="right")

        self._server_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        )
        self._server_label.pack(fill="x", pady=(0, 6))

        t = self.theme
        self._list_host = ctk.CTkScrollableFrame(
            self,
            fg_color=t.c("bg_card"),
            corner_radius=8,
            border_width=1,
            border_color=t.c("border"),
            scrollbar_button_color=t.c("bg_elevated"),
            scrollbar_button_hover_color=t.c("primary"),
            label_text="",
        )
        self._list_host.pack(fill="both", expand=True)

    def refresh(self, connection: ConnectionResult | None) -> None:
        if not self._list_host or not self._server_label or not self._count_label:
            return

        for child in self._list_host.winfo_children():
            child.destroy()

        if not connection:
            self._server_label.configure(text="Not connected")
            self._count_label.configure(text="")
            self._empty_message("Connect to a server to browse databases.")
            return

        self._server_label.configure(text=f"{connection.host}:{connection.port}")

        try:
            databases = fetch_server_databases(connection)
        except Exception:
            databases = []

        self._count_label.configure(text=str(len(databases)))

        if not databases:
            self._empty_message("No databases found.")
            return

        # Active database first so it is always at the top
        databases = sorted(
            databases,
            key=lambda d: (d.get("name") != connection.database, d.get("name", "")),
        )

        for db in databases:
            is_active = db.get("name") == connection.database
            self._add_db_row(db, is_active)

        self._list_host.update_idletasks()

    def _empty_message(self, text: str) -> None:
        assert self._list_host is not None
        ctk.CTkLabel(
            self._list_host,
            text=text,
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            wraplength=220,
            justify="left",
        ).pack(anchor="w", padx=10, pady=10)

    def _add_db_row(self, db: dict, is_active: bool) -> None:
        assert self._list_host is not None
        t = self.theme
        name = db.get("name", "—")
        size = db.get("size", "—")

        row = ctk.CTkFrame(
            self._list_host,
            fg_color=t.c("primary_muted") if is_active else t.c("bg_elevated"),
            corner_radius=6,
            border_width=1,
            border_color=t.c("primary") if is_active else t.c("border"),
            height=44,
        )
        row.pack(fill="x", padx=4, pady=3)
        row.pack_propagate(False)

        row_bg = t.c("primary_muted") if is_active else t.c("bg_elevated")
        inner = ctk.CTkFrame(row, fg_color=row_bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        ctk.CTkLabel(
            inner,
            text=name,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=t.c("text_primary"),
            anchor="w",
        ).pack(side="left", padx=8)

        right = ctk.CTkFrame(inner, fg_color=row_bg)
        right.pack(side="right", padx=8)

        if is_active:
            ctk.CTkLabel(
                right,
                text="ACTIVE",
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color=t.c("success"),
            ).pack(side="right", padx=(4, 0))

        ctk.CTkLabel(
            right,
            text=size,
            font=ctk.CTkFont(size=10),
            text_color=t.c("text_muted"),
        ).pack(side="right")
