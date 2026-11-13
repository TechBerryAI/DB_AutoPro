"""Premium theme tokens for Database Automation Pro."""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

from backend.core.paths import DATA_DIR

THEME_PREF_PATH = DATA_DIR / "ui_preferences.json"
# Logo-navy CustomTkinter theme — one button/control accent across the app.
_CTK_NAVY_THEME = str(Path(__file__).resolve().parent / "assets" / "ctk_theme_navy.json")

RADIUS = {"sm": 6, "md": 8, "lg": 10, "xl": 12, "pill": 999}

# Shared shell chrome sizes (CustomTkinter — not CSS, but one source of truth).
FOOTER_HEIGHT = 36
CONTENT_BOTTOM_INSET = FOOTER_HEIGHT + 16  # scroll content clears the fixed status bar

# 4/8-based spacing scale, in pixels — use via ThemeManager.spacing(key) instead of a
# literal padx/pady number, so a future spacing change is a one-line edit here.
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}

# (size, weight) pairs for every text role in the app — use via ThemeManager.type_scale(key)
# together with ThemeManager.font(), e.g. theme.font(*theme.type_scale("section")).
TYPE_SCALE = {
    "page_title": (22, "bold"),
    "section": (15, "bold"),
    "card_title": (12, "bold"),
    "body": (13, "normal"),
    "label": (12, "normal"),
    "metadata": (11, "normal"),
    "kpi_value": (22, "bold"),
    "monospace": (12, "normal"),
    "error": (12, "normal"),
}

# Logo-navy primary actions; green reserved for status/success only.
THEMES = {
    "dark": {
        "appearance_mode": "dark",
        "color_theme": _CTK_NAVY_THEME,
        "bg_app": "#080D0E",
        "bg_sidebar": "#090E10",
        "bg_content": "#080D0E",
        "bg_card": "#101719",
        "bg_card_hover": "#152022",
        "bg_elevated": "#172124",
        "bg_input": "#0C1214",
        "bg_glass": "#101719",
        "bg_list": "#0C1214",
        "border": "#202D2F",
        "border_subtle": "#1A2527",
        "border_focus": "#111827",
        "primary": "#111827",
        "primary_hover": "#1E293B",
        "primary_muted": "#1F2937",
        "primary_glow": "#11182766",
        "text_primary": "#E7F0EF",
        "text_secondary": "#849695",
        "text_muted": "#6B7C7B",
        "text_on_primary": "#FFFFFF",
        "success": "#21C98B",
        "success_muted": "#0D2A22",
        "warning": "#E6AA45",
        "warning_muted": "#2E2410",
        "error": "#EF6262",
        "error_muted": "#2E1414",
        "info": "#559BEB",
        "info_muted": "#122033",
        "neutral": "#849695",
        "neutral_muted": "#172124",
        "background": "#080D0E",
        "surface": "#101719",
        "surface_raised": "#172124",
        "destructive_bg": "#2E1414",
        "destructive_hover": "#3F1A1A",
        "destructive_text": "#EF6262",
        "accent": "#111827",
        "accent_secondary": "#1E293B",
        "metric_blue": "#559BEB",
        "metric_blue_bg": "#122033",
        "metric_purple": "#64748B",
        "metric_purple_bg": "#1F2937",
        "metric_green": "#21C98B",
        "metric_green_bg": "#0D2A22",
        "metric_red": "#EF6262",
        "metric_red_bg": "#2E1414",
        "metric_amber": "#E6AA45",
        "metric_amber_bg": "#2E2410",
        "nav_active_text": "#E7F0EF",
        "nav_idle_text": "#849695",
        "nav_indicator": "#111827",
        "nav_active_bg": "#111827",
        "log_info": "#849695",
        "log_success": "#21C98B",
        "log_warning": "#E6AA45",
        "log_error": "#EF6262",
        "terminal_bg": "#060A0B",
        "sidebar_border": "#1A2527",
        "sidebar_text_primary": "#E7F0EF",
        "sidebar_text_secondary": "#849695",
        "sidebar_text_muted": "#6B7C7B",
        "sidebar_bg_card": "#101719",
        "sidebar_bg_hover": "#152022",
        "sidebar_bg_list": "#080D0E",
        "sidebar_nav_active": "#111827",
        "sidebar_nav_hover": "#1E293B",
        "sidebar_nav_accent": "#111827",
        "bg_primary": "#080D0E",
        "bg_secondary": "#101719",
        "bg_sidebar_legacy": "#090E10",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": _CTK_NAVY_THEME,
        # SSMS / VS light — cool gray chrome, white content, tool-window blue.
        "bg_app": "#F0F0F0",
        "bg_content": "#F0F0F0",
        "bg_card": "#ffffff",
        "bg_card_hover": "#E8E8E8",
        "bg_elevated": "#F0F0F0",
        "bg_input": "#ffffff",
        "bg_glass": "#ffffff",
        "bg_list": "#F5F5F5",
        "border": "#A0A0A0",
        "border_subtle": "#D4D4D4",
        "border_focus": "#0078D4",
        "primary": "#0078D4",
        "primary_hover": "#106EBE",
        "primary_muted": "#DEECF9",
        "primary_glow": "#0078D422",
        "text_primary": "#1E1E1E",
        "text_secondary": "#44546A",
        "text_muted": "#6B6B6B",
        "text_on_primary": "#ffffff",
        "success": "#107C10",
        "success_muted": "#E6F4E6",
        "warning": "#9A6700",
        "warning_muted": "#FFF8E1",
        "error": "#C42B1C",
        "error_muted": "#FDE7E9",
        "info": "#0078D4",
        "info_muted": "#DEECF9",
        "neutral": "#44546A",
        "neutral_muted": "#F0F0F0",
        "background": "#F0F0F0",
        "surface": "#ffffff",
        "surface_raised": "#F0F0F0",
        "destructive_bg": "#FDE7E9",
        "destructive_hover": "#F9D2D5",
        "destructive_text": "#A4262C",
        "accent": "#0078D4",
        "accent_secondary": "#106EBE",
        "metric_blue": "#0078D4",
        "metric_blue_bg": "#DEECF9",
        "metric_purple": "#5C2D91",
        "metric_purple_bg": "#F3EDF7",
        "metric_green": "#107C10",
        "metric_green_bg": "#E6F4E6",
        "metric_red": "#C42B1C",
        "metric_red_bg": "#FDE7E9",
        "metric_amber": "#9A6700",
        "metric_amber_bg": "#FFF8E1",
        "nav_active_text": "#FFFFFF",
        "nav_idle_text": "#44546A",
        "nav_indicator": "#0078D4",
        "nav_active_bg": "#0078D4",
        "log_info": "#44546A",
        "log_success": "#107C10",
        "log_warning": "#9A6700",
        "log_error": "#C42B1C",
        "terminal_bg": "#1E1E1E",
        "bg_sidebar": "#F5F5F5",
        "sidebar_border": "#CCCEDB",
        "sidebar_text_primary": "#1E1E1E",
        "sidebar_text_secondary": "#44546A",
        "sidebar_text_muted": "#6B6B6B",
        "sidebar_bg_card": "#FFFFFF",
        "sidebar_bg_hover": "#E8E8E8",
        "sidebar_bg_list": "#F5F5F5",
        "sidebar_nav_active": "#CCE8FF",
        "sidebar_nav_hover": "#E8E8E8",
        "sidebar_nav_accent": "#0078D4",
        "bg_primary": "#F0F0F0",
        "bg_secondary": "#FFFFFF",
        "bg_sidebar_legacy": "#F5F5F5",
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
    "nav_active": "nav_active_bg",
    "nav_hover": "bg_card_hover",
    "nav_accent": "nav_indicator",
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

    def spacing(self, key: str = "md") -> int:
        return SPACING.get(key, SPACING["md"])

    def type_scale(self, key: str) -> tuple[int, str]:
        return TYPE_SCALE.get(key, TYPE_SCALE["body"])

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

    def scroll_kwargs(self) -> dict[str, str]:
        """Colors for CTkScrollableFrame so dark mode never shows a light OS bar."""
        return {
            "scrollbar_button_color": self.c("bg_elevated"),
            "scrollbar_button_hover_color": self.c("primary"),
        }


def themed_scrollable_frame(parent, theme: "ThemeManager", **kwargs):
    """CTkScrollableFrame with theme-matched scrollbar (fixes light-gray dark-mode bars)."""
    import customtkinter as ctk

    opts = theme.scroll_kwargs()
    opts.update(kwargs)
    opts.setdefault("fg_color", "transparent")
    return ctk.CTkScrollableFrame(parent, **opts)


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


def load_sidebar_collapsed() -> bool:
    return bool(_load_ui_prefs().get("sidebar_collapsed", False))


def save_sidebar_collapsed(collapsed: bool) -> None:
    data = _load_ui_prefs()
    data["sidebar_collapsed"] = bool(collapsed)
    _save_ui_prefs(data)


def load_sidebar_width(default: int = 232) -> int:
    try:
        width = int(_load_ui_prefs().get("sidebar_width", default))
    except (TypeError, ValueError):
        width = default
    return max(180, min(400, width))


def save_sidebar_width(width: int) -> None:
    data = _load_ui_prefs()
    data["sidebar_width"] = int(max(180, min(400, width)))
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
