"""Premium theme tokens for Database Automation Pro."""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

THEME_PREF_PATH = Path(__file__).resolve().parent.parent / "config" / "ui_preferences.json"

RADIUS = {"sm": 12, "md": 16, "lg": 22, "xl": 28, "pill": 999}

FONT_DISPLAY = "Segoe UI"
FONT_UI = "Segoe UI"

# Ultra-realistic modern palette — layered depth, glass surfaces, mesh backgrounds
THEMES = {
    "dark": {
        "appearance_mode": "dark",
        "color_theme": "blue",
        "bg_app": "#020408",
        "bg_sidebar": "#060a12",
        "bg_content": "#0b1018",
        "bg_card": "#101722",
        "bg_card_hover": "#161f2e",
        "bg_elevated": "#141c2a",
        "bg_input": "#0a1018",
        "bg_glass": "#121a28cc",
        "bg_list": "#0c121c",
        "bg_mesh": "#0e1420",
        "border": "#243044",
        "border_subtle": "#1a2434",
        "border_glow": "#3b82f633",
        "border_focus": "#60a5fa",
        "shadow_outer": "#000000",
        "shadow_inner": "#1a2434",
        "primary": "#3b82f6",
        "primary_hover": "#2563eb",
        "primary_muted": "#1e3a5f",
        "primary_glow": "#3b82f640",
        "gradient_start": "#2563eb",
        "gradient_end": "#7c3aed",
        "text_primary": "#f8fafc",
        "text_secondary": "#9aa8bc",
        "text_muted": "#6b7a90",
        "text_on_primary": "#ffffff",
        "success": "#3dd68c",
        "success_muted": "#0d3d2a",
        "warning": "#f5c451",
        "warning_muted": "#4a3410",
        "error": "#ff6b6b",
        "error_muted": "#4a1515",
        "destructive_bg": "#8b2222",
        "destructive_hover": "#ef4444",
        "destructive_text": "#fecaca",
        "accent": "#818cf8",
        "accent_secondary": "#a78bfa",
        "metric_blue": "#5b9aff",
        "metric_blue_bg": "#152847",
        "metric_purple": "#b4a0ff",
        "metric_purple_bg": "#2a1f4a",
        "metric_green": "#3dd68c",
        "metric_green_bg": "#0d3d2a",
        "metric_red": "#ff6b6b",
        "metric_red_bg": "#4a1515",
        "metric_amber": "#f5c451",
        "metric_amber_bg": "#4a3410",
        "nav_active_text": "#ffffff",
        "nav_idle_text": "#8b9bb0",
        "nav_indicator": "#5b9aff",
        "log_info": "#9aa8bc",
        "log_success": "#3dd68c",
        "log_warning": "#f5c451",
        "log_error": "#ff6b6b",
        "terminal_bg": "#050810",
        "sidebar_border": "#141c28",
        "sidebar_text_primary": "#f1f5f9",
        "sidebar_text_secondary": "#94a3b8",
        "sidebar_text_muted": "#64748b",
        "sidebar_bg_card": "#0f1520",
        "sidebar_bg_hover": "#151d2c",
        "sidebar_bg_list": "#0a0e16",
        "sidebar_nav_active": "#1e3a5f",
        "sidebar_nav_hover": "#141c2a",
        "bg_primary": "#020408",
        "bg_secondary": "#101722",
        "bg_sidebar_legacy": "#060a12",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": "blue",
        "bg_app": "#c8d4e4",
        "bg_content": "#dce4f0",
        "bg_card": "#ffffff",
        "bg_card_hover": "#f8fafc",
        "bg_elevated": "#f1f5f9",
        "bg_input": "#ffffff",
        "bg_glass": "#ffffff",
        "bg_list": "#eef2f8",
        "bg_mesh": "#d4deec",
        "border": "#b8c5d6",
        "border_subtle": "#d8e0eb",
        "border_glow": "#2563eb22",
        "border_focus": "#2563eb",
        "shadow_outer": "#b8c4d4",
        "shadow_inner": "#ffffff",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_muted": "#dbeafe",
        "primary_glow": "#2563eb18",
        "gradient_start": "#2563eb",
        "gradient_end": "#6366f1",
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
        "nav_idle_text": "#94a3b8",
        "nav_indicator": "#2563eb",
        "log_info": "#475569",
        "log_success": "#059669",
        "log_warning": "#d97706",
        "log_error": "#dc2626",
        "terminal_bg": "#0f172a",
        "bg_sidebar": "#0a1020",
        "sidebar_border": "#1a2744",
        "sidebar_text_primary": "#f8fafc",
        "sidebar_text_secondary": "#cbd5e1",
        "sidebar_text_muted": "#64748b",
        "sidebar_bg_card": "#111827",
        "sidebar_bg_hover": "#1e293b",
        "sidebar_bg_list": "#0c1220",
        "sidebar_nav_active": "#2563eb",
        "sidebar_nav_hover": "#1e293b",
        "bg_primary": "#c8d4e4",
        "bg_secondary": "#ffffff",
        "bg_sidebar_legacy": "#0a1020",
    },
}

_SIDEBAR_FALLBACKS: dict[str, str] = {
    "bg_sidebar": "bg_sidebar",
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
        try:
            ctk.set_widget_scaling(1.0)
        except Exception:
            pass

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
        family = FONT_DISPLAY if size >= 22 else FONT_UI
        return ctk.CTkFont(family=family, size=size, weight=weight)

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
        return self.colors.get(f"sidebar_{key}", self.c(fallback))


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
