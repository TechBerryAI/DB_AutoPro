"""Theme configuration for dark and light modes."""

from __future__ import annotations

import customtkinter as ctk

THEMES = {
    "dark": {
        "appearance_mode": "dark",
        "color_theme": "blue",
        "bg_primary": "#1a1a2e",
        "bg_secondary": "#16213e",
        "bg_sidebar": "#0f3460",
        "accent": "#e94560",
        "text_primary": "#ffffff",
        "text_secondary": "#a0a0a0",
        "success": "#00b894",
        "warning": "#fdcb6e",
        "error": "#d63031",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": "blue",
        "bg_primary": "#f5f6fa",
        "bg_secondary": "#ffffff",
        "bg_sidebar": "#2d3436",
        "accent": "#0984e3",
        "text_primary": "#2d3436",
        "text_secondary": "#636e72",
        "success": "#00b894",
        "warning": "#fdcb6e",
        "error": "#d63031",
    },
}


class ThemeManager:
    """Manages application theme switching."""

    def __init__(self, initial: str = "dark") -> None:
        self.current = initial
        self.colors = THEMES[initial]
        ctk.set_appearance_mode(self.colors["appearance_mode"])
        ctk.set_default_color_theme(self.colors["color_theme"])

    def toggle(self) -> str:
        self.current = "light" if self.current == "dark" else "dark"
        self.colors = THEMES[self.current]
        ctk.set_appearance_mode(self.colors["appearance_mode"])
        return self.current

    def get(self, key: str) -> str:
        return self.colors.get(key, "#ffffff")
