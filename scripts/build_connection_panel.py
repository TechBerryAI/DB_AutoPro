"""Transform connection_dialog into themed ConnectionPanel."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "desktop" / "connection_dialog.py").read_text(encoding="utf-8")

# Strip module color constants (keep SIDEBAR_WIDTH etc.)
src = re.sub(
    r"# Layout tokens\n(?:COLOR_[^\n]+\n)+",
    "",
    src,
)

header = '''"""Embeddable, theme-aware database connection panel for the Servers view."""

from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult, DB_DEFAULTS, test_connection
from desktop.connection_store import SavedServer, load_saved_servers, remove_server, upsert_server
from desktop.db_icons import DB_ICON_META
from desktop.theme import ThemeManager

SIDEBAR_WIDTH = 280
ENTRY_HEIGHT = 36
ICON_TILE = 88


class ConnectionPanel(ctk.CTkFrame):
    """Inline connection manager — saved targets sidebar + configuration form."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        on_connect_success: Callable[[ConnectionResult], None] | None = None,
        log_callback: Callable[[str, str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=theme.c("bg_content"), **kwargs)
        self.theme = theme
        self.on_connect_success = on_connect_success
        self.log_callback = log_callback
        self._db_type_var = ctk.StringVar(value=DatabaseType.POSTGRESQL.value)
        self._connecting = False
        self._testing = False
        self._selected_saved: str | None = None
        self._saved_list_buttons: dict[str, ctk.CTkFrame] = {}
        self._db_type_tiles: dict[str, ctk.CTkFrame] = {}
        self._password_visible = False
        self._linux_password_visible = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_sidebar()
        self._build_main_panel()
        self.bind("<Return>", lambda _e: self._on_connect())
        self._refresh_saved_list()
        self._select_db_type(DatabaseType.POSTGRESQL)

    def _sidebar_bg(self) -> str:
        return self.theme.sc("bg_sidebar") if self.theme.is_light else self.theme.c("bg_sidebar")

    def _sidebar_item_bg(self) -> str:
        return self.theme.sc("sidebar_bg_card") if self.theme.is_light else self.theme.c("bg_elevated")

    def _sidebar_muted(self) -> str:
        return self.theme.sc("text_muted") if self.theme.is_light else self.theme.c("text_muted")

    def _sidebar_secondary(self) -> str:
        return self.theme.sc("text_secondary") if self.theme.is_light else self.theme.c("text_secondary")

    def _sidebar_text(self) -> str:
        return self.theme.sc("text_primary") if self.theme.is_light else self.theme.c("text_primary")

    def _sidebar_hover(self) -> str:
        return self.theme.sc("sidebar_bg_hover") if self.theme.is_light else self.theme.c("bg_card_hover")

    def _sidebar_footer_bg(self) -> str:
        return self.theme.sc("bg_list") if self.theme.is_light else self.theme.c("bg_elevated")

    def _sidebar_border(self) -> str:
        return self.theme.sc("border") if self.theme.is_light else self.theme.c("border_subtle")

    def _log(self, message: str, level: str = "INFO") -> None:
        if self.log_callback:
            self.log_callback(message, level)

'''

# Extract body from ConnectionDialog class (from _build_sidebar or first method after broken init)
match = re.search(
    r"    def _build_sidebar\(self\)",
    src,
)
if not match:
    raise SystemExit("Could not find _build_sidebar")
body = src[match.start() :]

# Remove dialog-only methods from body
for method in (
    "_build_ui",
    "_build_header",
    "_apply_fullscreen",
    "_apply_embedded",
    "_on_close",
    "show",
):
    body = re.sub(
        rf"    def {method}\(self[^)]*\)[^:]*:.*?(?=\n    def |\n    @staticmethod|\Z)",
        "",
        body,
        flags=re.DOTALL,
    )

# Fix grid row in _build_main_panel (was row=1 for dialog, now row=0)
body = body.replace("main.grid(row=1, column=1,", "main.grid(row=0, column=1,")
body = body.replace('sidebar.grid(row=1, column=0,', 'sidebar.grid(row=0, column=0,')

# Color token replacements (whole word)
color_map = {
    "COLOR_BG": 'self.theme.c("bg_content")',
    "COLOR_SIDEBAR": "self._sidebar_bg()",
    "COLOR_SIDEBAR_HOVER": "self._sidebar_item_bg()",
    "COLOR_SIDEBAR_ACTIVE": 'self.theme.c("primary")',
    "COLOR_CARD": 'self.theme.c("bg_card")',
    "COLOR_CARD_BORDER": 'self.theme.c("border")',
    "COLOR_TEXT": 'self.theme.c("text_primary")',
    "COLOR_MUTED": 'self.theme.c("text_muted")',
    "COLOR_PRIMARY": 'self.theme.c("primary")',
    "COLOR_PRIMARY_HOVER": 'self.theme.c("primary_hover")',
    "COLOR_SUCCESS": 'self.theme.c("success")',
    "COLOR_ERROR": 'self.theme.c("error")',
    "COLOR_WARNING": 'self.theme.c("warning")',
}
for old, new in color_map.items():
    body = re.sub(rf"\b{old}\b", new, body)

hardcoded = {
    '"#94a3b8"': "self._sidebar_muted()",
    '"#cbd5e1"': "self._sidebar_secondary()",
    '"#475569"': "self._sidebar_hover()",
    '"#172033"': "self._sidebar_footer_bg()",
    '"#334155"': "self._sidebar_border()",
    '"#f8fafc"': 'self.theme.c("bg_card_hover")',
    '"#eff6ff"': 'self.theme.c("primary_muted")',
    '"#f1f5f9"': 'self.theme.c("bg_elevated")',
    '"#e2e8f0"': 'self.theme.c("bg_card_hover")',
    '"#fffbeb"': 'self.theme.c("warning_muted")',
    '"#fef2f2"': 'self.theme.c("error_muted")',
    '"#ecfdf5"': 'self.theme.c("success_muted")',
    'text_color="white"': "text_color=self._sidebar_text()",
}
for old, new in hardcoded.items():
    body = body.replace(old, new)

# Connect success: callback instead of destroy
body = body.replace(
    """                self.connection_result = connection
                self.after(300, self.destroy)""",
    """                connection.saved_name = self._selected_saved
                self._log(f"Connected to {connection.name}", "SUCCESS")
                if self.on_connect_success:
                    self.on_connect_success(connection)""",
)

# Status frame colors use theme muted backgrounds
body = body.replace(
    '"info": (self.theme.c("text_muted"), self.theme.c("bg_card_hover")),',
    '"info": (self.theme.c("text_muted"), self.theme.c("bg_elevated")),',
)

# Style entries
entry_style = """
        for widget in (
            getattr(self, "connection_name_entry", None),
            getattr(self, "host_entry", None),
            getattr(self, "port_entry", None),
            getattr(self, "user_entry", None),
            getattr(self, "password_entry", None),
            getattr(self, "database_entry", None),
            getattr(self, "auth_database_entry", None),
            getattr(self, "ssh_host_entry", None),
            getattr(self, "ssh_port_entry", None),
            getattr(self, "linux_user_entry", None),
            getattr(self, "linux_password_entry", None),
        ):
            if widget is not None:
                widget.configure(
                    fg_color=self.theme.c("bg_input"),
                    border_color=self.theme.c("border"),
                    text_color=self.theme.c("text_primary"),
                )
"""

# Add apply_theme and _style_inputs at end
footer = '''

    def _style_inputs(self) -> None:
        for name in (
            "connection_name_entry", "host_entry", "port_entry", "user_entry",
            "password_entry", "database_entry", "auth_database_entry",
            "ssh_host_entry", "ssh_port_entry", "linux_user_entry", "linux_password_entry",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(
                    fg_color=self.theme.c("bg_input"),
                    border_color=self.theme.c("border"),
                    text_color=self.theme.c("text_primary"),
                )

    def apply_theme(self, theme: ThemeManager) -> None:
        """Re-apply theme tokens after light/dark toggle."""
        self.theme = theme
        self.configure(fg_color=theme.c("bg_content"))
        self._refresh_saved_list()
        self._apply_db_type_tiles(DatabaseType(self._db_type_var.get()))
        self._style_inputs()
'''

# Inject _style_inputs call after building details - add to end of _build_details_card
body = body.replace(
    '        self.hint_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))',
    '        self.hint_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))\n        self.after_idle(self._style_inputs)',
)

out = header + body + footer
(ROOT / "desktop" / "connection_panel.py").write_text(out, encoding="utf-8")
print("connection_panel.py written", len(out), "chars")
