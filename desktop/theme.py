"""Premium theme tokens for Database Automation Pro."""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

THEME_PREF_PATH = Path(__file__).resolve().parent.parent / "config" / "ui_preferences.json"

RADIUS = {"sm": 8, "md": 12, "lg": 16, "xl": 20, "pill": 999}

# Ultra-modern realistic palette — depth, glass panels, refined accents
THEMES = {
    "dark": {
        "appearance_mode": "dark",
        "color_theme": "blue",
        "bg_app": "#05070c",
        "bg_sidebar": "#080c14",
        "bg_content": "#0a0f18",
        "bg_card": "#111827",
        "bg_card_hover": "#172033",
        "bg_elevated": "#1a2438",
        "bg_input": "#0c121c",
        "bg_glass": "#141c2b",
        "bg_list": "#0e1522",
        "border": "#243044",
        "border_subtle": "#151d2b",
        "border_focus": "#3b82f6",
        "primary": "#4f8cff",
        "primary_hover": "#3b7cf0",
        "primary_muted": "#1a2d52",
        "primary_glow": "#2563eb33",
        "text_primary": "#f1f5f9",
        "text_secondary": "#94a3b8",
        "text_muted": "#64748b",
        "text_on_primary": "#ffffff",
        "success": "#34d399",
        "success_muted": "#064e3b",
        "warning": "#fbbf24",
        "warning_muted": "#78350f",
        "error": "#f87171",
        "error_muted": "#450a0a",
        "destructive_bg": "#7f1d1d",
        "destructive_hover": "#ef4444",
        "destructive_text": "#fecaca",
        "accent": "#6366f1",
        "accent_secondary": "#8b5cf6",
        "metric_blue": "#4f8cff",
        "metric_blue_bg": "#1a2d52",
        "metric_purple": "#a78bfa",
        "metric_purple_bg": "#2e1f5e",
        "metric_green": "#34d399",
        "metric_green_bg": "#064e3b",
        "metric_red": "#f87171",
        "metric_red_bg": "#450a0a",
        "metric_amber": "#fbbf24",
        "metric_amber_bg": "#78350f",
        "nav_active_text": "#ffffff",
        "nav_idle_text": "#94a3b8",
        "nav_indicator": "#4f8cff",
        "log_info": "#94a3b8",
        "log_success": "#34d399",
        "log_warning": "#fbbf24",
        "log_error": "#f87171",
        "terminal_bg": "#060a12",
        "bg_primary": "#05070c",
        "bg_secondary": "#111827",
        "bg_sidebar_legacy": "#080c14",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": "blue",
        "bg_app": "#e8ecf3",
        "bg_content": "#eef2f8",
        "bg_card": "#ffffff",
        "bg_card_hover": "#f8fafc",
        "bg_elevated": "#ffffff",
        "bg_input": "#ffffff",
        "bg_glass": "#ffffff",
        "bg_list": "#f1f5f9",
        "border": "#d4dce8",
        "border_subtle": "#e8edf4",
        "border_focus": "#2563eb",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_muted": "#eff6ff",
        "primary_glow": "#2563eb22",
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_muted": "#64748b",
        "text_on_primary": "#ffffff",
        "success": "#059669",
        "success_muted": "#ecfdf5",
        "warning": "#d97706",
        "warning_muted": "#fffbeb",
        "error": "#dc2626",
        "error_muted": "#fef2f2",
        "destructive_bg": "#991b1b",
        "destructive_hover": "#dc2626",
        "destructive_text": "#ffffff",
        "accent": "#6366f1",
        "accent_secondary": "#8b5cf6",
        "metric_blue": "#2563eb",
        "metric_blue_bg": "#eff6ff",
        "metric_purple": "#7c3aed",
        "metric_purple_bg": "#f5f3ff",
        "metric_green": "#059669",
        "metric_green_bg": "#ecfdf5",
        "metric_red": "#dc2626",
        "metric_red_bg": "#fef2f2",
        "metric_amber": "#d97706",
        "metric_amber_bg": "#fffbeb",
        "nav_active_text": "#ffffff",
        "nav_idle_text": "#cbd5e1",
        "nav_indicator": "#2563eb",
        "log_info": "#475569",
        "log_success": "#059669",
        "log_warning": "#d97706",
        "log_error": "#dc2626",
        "terminal_bg": "#0f172a",
        "bg_sidebar": "#0f172a",
        "sidebar_border": "#1e293b",
        "sidebar_text_primary": "#f8fafc",
        "sidebar_text_secondary": "#cbd5e1",
        "sidebar_text_muted": "#94a3b8",
        "sidebar_bg_card": "#1e293b",
        "sidebar_bg_hover": "#334155",
        "sidebar_bg_list": "#0c1220",
        "sidebar_nav_active": "#2563eb",
        "sidebar_nav_hover": "#1e293b",
        "bg_primary": "#e8ecf3",
        "bg_secondary": "#ffffff",
        "bg_sidebar_legacy": "#0f172a",
    },
}

_SIDEBAR_FALLBACKS: dict[str, str] = {
    "bg_list": "bg_list",
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
    """Manages application theme switching and design tokens."""

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

    def radius(self, key: str = "md") -> int:
        return RADIUS.get(key, RADIUS["md"])

    def font(self, size: int, *, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)

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
        return self.get(key)

    def sc(self, key: str) -> str:
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
