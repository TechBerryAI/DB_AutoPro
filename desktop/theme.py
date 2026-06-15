"""Enterprise theme tokens for Database Automation Pro."""

from __future__ import annotations

import customtkinter as ctk

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
        # Legacy keys used elsewhere
        "bg_primary": "#0b0f14",
        "bg_secondary": "#1a2332",
        "bg_sidebar_legacy": "#0f172a",
    },
    "light": {
        "appearance_mode": "light",
        "color_theme": "blue",
        "bg_app": "#f1f5f9",
        "bg_sidebar": "#ffffff",
        "bg_content": "#f8fafc",
        "bg_card": "#ffffff",
        "bg_card_hover": "#f1f5f9",
        "bg_elevated": "#ffffff",
        "bg_input": "#ffffff",
        "border": "#e2e8f0",
        "border_subtle": "#f1f5f9",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_muted": "#dbeafe",
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_muted": "#94a3b8",
        "success": "#16a34a",
        "success_muted": "#dcfce7",
        "warning": "#d97706",
        "warning_muted": "#fef3c7",
        "error": "#dc2626",
        "error_muted": "#fee2e2",
        "accent": "#2563eb",
        "bg_primary": "#f1f5f9",
        "bg_secondary": "#ffffff",
        "bg_sidebar_legacy": "#ffffff",
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

    def c(self, key: str) -> str:
        """Shorthand for get."""
        return self.get(key)
