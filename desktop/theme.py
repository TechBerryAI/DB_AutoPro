"""Enterprise theme tokens for Database Automation Pro."""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

THEME_PREF_PATH = Path(__file__).resolve().parent.parent / "config" / "ui_preferences.json"

THEMES = {
    "dark": {
        "appearance_mode": "dark",
        "color_theme": "blue",
        "bg_app": "#0b0f14",
        "bg_sidebar": "#0f172a",
        "bg_content": "#111827",
        "bg_card": "#1a2332",
        "bg_card_hover": "#1f2a3d",
        "bg_elevated": "#243044",
        "bg_input": "#0f172a",
        "border": "#2d3a4f",
        "border_subtle": "#1e293b",
        "primary": "#3b82f6",
        "primary_hover": "#2563eb",
        "primary_muted": "#1e3a5f",
        "text_primary": "#f8fafc",
        "text_secondary": "#94a3b8",
        "text_muted": "#64748b",
        "success": "#22c55e",
        "success_muted": "#14532d",
        "warning": "#f59e0b",
        "warning_muted": "#78350f",
        "error": "#ef4444",
        "error_muted": "#7f1d1d",
        "accent": "#3b82f6",
        "text_on_primary": "#ffffff",
        "nav_active_text": "#f8fafc",
        "nav_idle_text": "#94a3b8",
        "log_info": "#94a3b8",
        "log_success": "#22c55e",
        "log_warning": "#f59e0b",
        "log_error": "#ef4444",
        # Legacy keys used elsewhere
        "bg_primary": "#0b0f14",
        "bg_secondary": "#1a2332",
        "bg_sidebar_legacy": "#0f172a",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": "blue",
        # Main content — matches connection dialog right panel
        "bg_app": "#eef1f6",
        "bg_content": "#eef1f6",
        "bg_card": "#ffffff",
        "bg_card_hover": "#f8fafc",
        "bg_elevated": "#ffffff",
        "bg_input": "#ffffff",
        "border": "#dde3ec",
        "border_subtle": "#eef1f6",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_muted": "#eff6ff",
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_muted": "#64748b",
        "text_on_primary": "#ffffff",
        "nav_active_text": "#ffffff",
        "nav_idle_text": "#cbd5e1",
        "success": "#059669",
        "success_muted": "#ecfdf5",
        "warning": "#d97706",
        "warning_muted": "#fffbeb",
        "error": "#dc2626",
        "error_muted": "#fef2f2",
        "accent": "#2563eb",
        "log_info": "#475569",
        "log_success": "#059669",
        "log_warning": "#d97706",
        "log_error": "#dc2626",
        # Sidebar — matches connection dialog left panel (dark navy)
        "bg_sidebar": "#1e293b",
        "sidebar_border": "#334155",
        "sidebar_text_primary": "#f8fafc",
        "sidebar_text_secondary": "#cbd5e1",
        "sidebar_text_muted": "#94a3b8",
        "sidebar_bg_card": "#334155",
        "sidebar_bg_hover": "#475569",
        "sidebar_bg_list": "#172033",
        "sidebar_nav_active": "#2563eb",
        "sidebar_nav_hover": "#334155",
        "bg_primary": "#eef1f6",
        "bg_secondary": "#ffffff",
        "bg_sidebar_legacy": "#1e293b",
    },
}


# When sidebar-specific tokens are absent, map to standard theme keys.
_SIDEBAR_FALLBACKS: dict[str, str] = {
    "bg_list": "bg_card",
    "bg_card": "bg_card",
    "bg_hover": "bg_card_hover",
    "border": "border",
    "text_primary": "text_primary",
    "text_secondary": "text_secondary",
    "text_muted": "text_muted",
    "nav_active": "primary_muted",
    "nav_hover": "bg_card_hover",
}


class ThemeManager:
    """Manages application theme switching."""

    def __init__(self, initial: str | None = None) -> None:
        self.current = initial or load_saved_theme()
        if self.current not in THEMES:
            self.current = "dark"
        self.colors = THEMES[self.current]
        ctk.set_appearance_mode(self.colors["appearance_mode"])
        ctk.set_default_color_theme(self.colors["color_theme"])

    def toggle(self) -> str:
        self.current = "light" if self.current == "dark" else "dark"
        self.colors = THEMES[self.current]
        ctk.set_appearance_mode(self.colors["appearance_mode"])
        ctk.set_default_color_theme(self.colors["color_theme"])
        save_theme(self.current)
        return self.current

    @property
    def is_light(self) -> bool:
        return self.current == "light"

    def log_level_color(self, level: str) -> str:
        key = f"log_{level.lower()}"
        return self.colors.get(key, self.c("text_secondary"))

    def set(self, name: str) -> str:
        if name not in THEMES:
            name = "dark"
        self.current = name
        self.colors = THEMES[name]
        ctk.set_appearance_mode(self.colors["appearance_mode"])
        ctk.set_default_color_theme(self.colors["color_theme"])
        save_theme(name)
        return self.current

    def get(self, key: str) -> str:
        return self.colors.get(key, "#ffffff")

    def c(self, key: str) -> str:
        """Shorthand for get."""
        return self.get(key)

    def sc(self, key: str) -> str:
        """Sidebar chrome color — light mode uses dedicated navy-sidebar tokens."""
        fallback = _SIDEBAR_FALLBACKS.get(key, key)
        if self.is_light:
            return self.colors.get(f"sidebar_{key}", self.c(fallback))
        return self.c(fallback)


def _load_ui_prefs() -> dict:
    try:
        if THEME_PREF_PATH.exists():
            data = json.loads(THEME_PREF_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {}


def _save_ui_prefs(data: dict) -> None:
    try:
        THEME_PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        THEME_PREF_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_saved_theme() -> str:
    theme = _load_ui_prefs().get("theme", "dark")
    if theme in THEMES:
        return theme
    return "dark"


def save_theme(name: str) -> None:
    data = _load_ui_prefs()
    data["theme"] = name
    _save_ui_prefs(data)


def load_last_username() -> str:
    return str(_load_ui_prefs().get("last_username", "")).strip()


def save_last_username(username: str) -> None:
    name = username.strip()
    if not name:
        return
    data = _load_ui_prefs()
    data["last_username"] = name
    _save_ui_prefs(data)
