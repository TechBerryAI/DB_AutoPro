"""Modal connection dialog — wraps the themed ConnectionPanel."""

from __future__ import annotations

import sys

import customtkinter as ctk

from desktop.connection import ConnectionResult
from desktop.connection_panel import ConnectionPanel
from desktop.constants import APP_NAME
from desktop.theme import ThemeManager


class ConnectionDialog(ctk.CTkToplevel):
    """Optional full-window connection manager (legacy / standalone)."""

    def __init__(self, parent: ctk.CTk | None = None, theme: ThemeManager | None = None) -> None:
        super().__init__(parent)
        self.connection_result: ConnectionResult | None = None
        self._theme = theme or ThemeManager()

        self.title(f"{APP_NAME} — Connect")
        self.configure(fg_color=self._theme.c("bg_content"))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if parent is not None:
            self.transient(parent)
            self.update_idletasks()
            parent.update_idletasks()
            w = max(parent.winfo_width(), 1024)
            h = max(parent.winfo_height(), 680)
            self.geometry(f"{w}x{h}+{parent.winfo_rootx()}+{parent.winfo_rooty()}")
            if sys.platform == "win32":
                self.state("zoomed")
        else:
            if sys.platform == "win32":
                self.state("zoomed")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._panel = ConnectionPanel(
            self,
            self._theme,
            on_connect_success=self._on_connected,
        )
        self._panel.grid(row=0, column=0, sticky="nsew")

    def _on_connected(self, connection: ConnectionResult) -> None:
        self.connection_result = connection
        self.after(200, self.destroy)

    def _on_close(self) -> None:
        self.connection_result = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    @staticmethod
    def show(parent: ctk.CTk | None = None, theme: ThemeManager | None = None) -> ConnectionResult | None:
        dialog = ConnectionDialog(parent, theme=theme)
        if parent is not None:
            dialog.grab_set()
            dialog.focus_force()
            parent.wait_window(dialog)
        else:
            dialog.mainloop()
        return dialog.connection_result
