"""Server connection management view."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.connection_store import SavedServer, load_saved_servers
from desktop.design_system import destructive_button, primary_button
from desktop.theme import ThemeManager
from desktop.ui_components import panel_card, section_header
from desktop.views.base_view import BaseView


class ServersView(BaseView):
    """Connect to a database server or manage saved targets."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        connect_callback: Callable[..., None] | None = None,
        disconnect_callback: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.connect_callback = connect_callback
        self.disconnect_callback = disconnect_callback
        self._status_label: ctk.CTkLabel | None = None
        self._connect_buttons: dict[str, ctk.CTkButton] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))
        section_header(
            header,
            self.theme,
            "Servers",
            "Register SSH targets and connect to your database servers",
        )

        conn_card = panel_card(scroll, self.theme, "Active Connection")
        conn_card.pack(fill="x", pady=(0, 16))
        body = ctk.CTkFrame(conn_card, fg_color="transparent")
        body.pack(fill="x", padx=20, pady=(0, 20))

        self._status_label = ctk.CTkLabel(
            body,
            text="No server connected.",
            font=ctk.CTkFont(size=14),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
            justify="left",
        )
        self._status_label.pack(anchor="w", pady=(0, 16))

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x")

        primary_button(
            actions,
            self.theme,
            text="Connect to Server",
            command=self._connect,
            height=42,
        ).pack(side="left", padx=(0, 10))

        self._disconnect_btn = destructive_button(
            actions,
            self.theme,
            text="Disconnect",
            command=self._disconnect,
            height=42,
        )
        self._disconnect_btn.pack(side="left")

        saved_card = panel_card(scroll, self.theme, "Registered SSH Targets")
        saved_card.pack(fill="x", pady=(0, 12))
        self._saved_frame = ctk.CTkFrame(saved_card, fg_color="transparent")
        self._saved_frame.pack(fill="x", padx=20, pady=(0, 20))

    def refresh(self) -> None:
        if not self._status_label:
            return
        if self.connection:
            self._status_label.configure(
                text=(
                    f"Connected to {self.connection.name}\n"
                    f"{self.connection.db_type.value.upper()} · "
                    f"{self.connection.endpoint_label} · "
                    f"{self.connection.database}"
                ),
                text_color=self.theme.c("text_primary"),
            )
            self._disconnect_btn.configure(state="normal")
        else:
            self._status_label.configure(
                text="No server connected. Click a saved target below or use Connect to Server.",
                text_color=self.theme.c("text_secondary"),
            )
            self._disconnect_btn.configure(state="disabled")

        for child in self._saved_frame.winfo_children():
            child.destroy()
        self._connect_buttons.clear()

        servers = load_saved_servers(self.owner_username)
        if not servers:
            ctk.CTkLabel(
                self._saved_frame,
                text="No saved servers yet. They appear here after you save a connection.",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w", pady=8)
            return

        for server in servers:
            row = ctk.CTkFrame(
                self._saved_frame,
                fg_color=self.theme.c("bg_elevated"),
                corner_radius=self.theme.radius("md"),
                border_width=1,
                border_color=self.theme.c("border_subtle"),
            )
            row.pack(fill="x", pady=4)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=14, pady=10)

            ssh = "SSH enabled" if server.ssh_enabled else "Direct connection"
            ctk.CTkLabel(
                info,
                text=f"{server.name}  ·  {server.db_type.upper()}  ·  {server.host}:{server.port}",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                info,
                text=ssh,
                font=ctk.CTkFont(size=11),
                text_color=self.theme.c("text_muted"),
                anchor="w",
            ).pack(anchor="w")

            connect_btn = ctk.CTkButton(
                row,
                text="Connect",
                width=90,
                height=32,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color=self.theme.c("primary"),
                hover_color=self.theme.c("primary_hover"),
                command=lambda s=server: self._connect_saved(s),
            )
            connect_btn.pack(side="right", padx=14, pady=10)
            self._connect_buttons[server.name] = connect_btn

            if self.connection and (
                self.connection.saved_name == server.name
                or self.connection.name == server.name
            ):
                connect_btn.configure(state="disabled", text="Active")

    def _connect(self) -> None:
        if self.connect_callback:
            self.connect_callback()

    def _connect_saved(self, server: SavedServer) -> None:
        btn = self._connect_buttons.get(server.name)
        if btn:
            btn.configure(state="disabled", text="...")
        if self.connect_callback:
            self.connect_callback(saved=server)
        if btn and btn.winfo_exists():
            self.after(800, self.refresh)

    def _disconnect(self) -> None:
        if self.disconnect_callback:
            self.disconnect_callback()
