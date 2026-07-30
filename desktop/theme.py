"""Premium theme tokens for Database Automation Pro."""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

THEME_PREF_PATH = Path(__file__).resolve().parent.parent / "config" / "ui_preferences.json"

RADIUS = {"sm": 8, "md": 12, "lg": 14, "xl": 16, "pill": 999}

# Ultra-modern realistic palette — layered depth, glass surfaces, refined accents
THEMES = {
    "dark": {
        "appearance_mode": "dark",
        "color_theme": "blue",
        "bg_app": "#030508",
        "bg_sidebar": "#070b12",
        "bg_content": "#0a0e16",
        "bg_card": "#0f1520",
        "bg_card_hover": "#151d2c",
        "bg_elevated": "#121a28",
        "bg_input": "#0a1018",
        "bg_glass": "#111827",
        "bg_list": "#0c121c",
        "border": "#1e2a3d",
        "border_subtle": "#141c28",
        "border_focus": "#5b9aff",
        "primary": "#5b9aff",
        "primary_hover": "#4a8ef5",
        "primary_muted": "#152847",
        "primary_glow": "#3b82f640",
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
        "destructive_bg": "#3f1515",
        "destructive_hover": "#5c1d1d",
        "destructive_text": "#fca5a5",
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
        "sidebar_nav_active": "#152847",
        "sidebar_nav_hover": "#121a28",
        "bg_primary": "#030508",
        "bg_secondary": "#0f1520",
        "bg_sidebar_legacy": "#070b12",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": "blue",
        "bg_app": "#F7F9FC",
        "bg_content": "#F7F9FC",
        "bg_card": "#ffffff",
        "bg_card_hover": "#f8fafc",
        "bg_elevated": "#F1F5F9",
        "bg_input": "#ffffff",
        "bg_glass": "#ffffff",
        "bg_list": "#F1F5F9",
        "border": "#E2E8F0",
        "border_subtle": "#EEF2F7",
        "border_focus": "#2563eb",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_muted": "#EFF6FF",
        "primary_glow": "#2563eb18",
        "text_primary": "#0F172A",
        "text_secondary": "#475569",
        "text_muted": "#64748B",
        "text_on_primary": "#ffffff",
        "success": "#059669",
        "success_muted": "#ECFDF5",
        "warning": "#D97706",
        "warning_muted": "#FFFBEB",
        "error": "#DC2626",
        "error_muted": "#FEF2F2",
        "destructive_bg": "#FEE2E2",
        "destructive_hover": "#FECACA",
        "destructive_text": "#B91C1C",
        "accent": "#4F46E5",
        "accent_secondary": "#6366F1",
        "metric_blue": "#2563EB",
        "metric_blue_bg": "#EFF6FF",
        "metric_purple": "#7C3AED",
        "metric_purple_bg": "#F5F3FF",
        "metric_green": "#059669",
        "metric_green_bg": "#ECFDF5",
        "metric_red": "#DC2626",
        "metric_red_bg": "#FEF2F2",
        "metric_amber": "#D97706",
        "metric_amber_bg": "#FFFBEB",
        "nav_active_text": "#ffffff",
        "nav_idle_text": "#cbd5e1",
        "nav_indicator": "#2563eb",
        "log_info": "#475569",
        "log_success": "#059669",
        "log_warning": "#d97706",
        "log_error": "#dc2626",
        "terminal_bg": "#0f172a",
        "bg_sidebar": "#0B1220",
        "sidebar_border": "#1E293B",
        "sidebar_text_primary": "#F8FAFC",
        "sidebar_text_secondary": "#CBD5E1",
        "sidebar_text_muted": "#94A3B8",
        "sidebar_bg_card": "#111827",
        "sidebar_bg_hover": "#1E293B",
        "sidebar_bg_list": "#0B1220",
        "sidebar_nav_active": "#1D4ED8",
        "sidebar_nav_hover": "#1E293B",
        "bg_primary": "#F7F9FC",
        "bg_secondary": "#FFFFFF",
        "bg_sidebar_legacy": "#0B1220",
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


def load_last_password() -> str:
    return str(_load_ui_prefs().get("last_password", ""))


def save_last_credentials(username: str, password: str) -> None:
    name = username.strip()
    if not name:
        return
    data = _load_ui_prefs()
    data["last_username"] = name
    data["last_password"] = password
    data["remember_password"] = True
    _save_ui_prefs(data)


def load_remember_password() -> bool:
    return bool(_load_ui_prefs().get("remember_password", True))


def clear_last_password() -> None:
    data = _load_ui_prefs()
    data.pop("last_password", None)
    data["remember_password"] = False
    _save_ui_prefs(data)


def load_last_backup_path() -> str:
    return str(_load_ui_prefs().get("last_backup_path", "")).strip()


def save_last_backup_path(path: str) -> None:
    value = path.strip()
    if not value:
        return
    data = _load_ui_prefs()
    data["last_backup_path"] = value
    _save_ui_prefs(data)
