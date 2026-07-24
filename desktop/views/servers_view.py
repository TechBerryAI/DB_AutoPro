"""Server connection view — full inline connection manager."""

from __future__ import annotations

from typing import Callable

from desktop.connection import ConnectionResult
from desktop.connection_panel import ConnectionPanel, _widget_alive
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView


class ServersView(BaseView):
    """Manage saved targets and connect using the full connection workspace."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        log_callback=None,
        establish_callback: Callable[[ConnectionResult], None] | None = None,
        disconnect_callback: Callable[[], None] | None = None,
        saved_servers_changed_callback: Callable[[str | None], None] | None = None,
        manage_callback: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        servers_filter = kwargs.pop("servers_filter", "all")
        saved_server = kwargs.pop("saved_server", None)
        super().__init__(master, theme, log_callback, **kwargs)
        self.establish_callback = establish_callback
        self.disconnect_callback = disconnect_callback
        self.saved_servers_changed_callback = saved_servers_changed_callback
        self._panel: ConnectionPanel | None = None
        self._servers_filter = servers_filter
        self._pending_saved_server = saved_server
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._panel = ConnectionPanel(
            self,
            self.theme,
            on_connect_success=self._on_connected,
            disconnect_callback=self._disconnect,
            on_saved_servers_changed=self.saved_servers_changed_callback,
            log_callback=self.log,
        )
        self._panel.grid(row=0, column=0, sticky="nsew")

        if self._pending_saved_server:
            loaded = self._panel.load_saved_by_name(self._pending_saved_server)
            self._pending_saved_server = None
            if not loaded and self.connection:
                self._panel.load_from_connection(self.connection)
        elif self.connection and (self.connection.saved_name or self.connection.name):
            # After connect/reload, keep showing the active connection — not a blank PostgreSQL form.
            name = self.connection.saved_name or self.connection.name
            if not self._panel.load_saved_by_name(name):
                self._panel.load_from_connection(self.connection)
        else:
            self._panel.set_servers_filter(self._servers_filter)
        self._update_session()

    def set_servers_filter(self, db_filter: str = "all") -> None:
        self._servers_filter = db_filter or "all"
        if self._panel:
            self._panel.set_servers_filter(self._servers_filter)

    def load_saved_server(self, name: str) -> None:
        if self._panel:
            self._panel.load_saved_by_name(name)

    def _update_session(self) -> None:
        if not self.winfo_exists() or not self._panel:
            return
        if not _widget_alive(self._panel):
            return
        self._panel.update_session(self.connection)

    def _on_connected(self, connection: ConnectionResult) -> None:
        if self.establish_callback:
            self.establish_callback(connection)
        self._update_session()

    def _disconnect(self) -> None:
        if self.disconnect_callback:
            self.disconnect_callback()
        self._update_session()

    def refresh(self) -> None:
        if self._panel:
            self._panel._refresh_saved_list()
        self._update_session()

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        if self._panel:
            self._panel.apply_theme(theme)
