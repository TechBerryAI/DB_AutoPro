"""Enterprise classic-desktop surfaces for the administration workspace.

Palette follows SSMS / Visual Studio light: cool grays, white content,
tool-window blue headers, and a clear selection blue.
"""

from __future__ import annotations

from desktop.theme import ThemeManager

# SSMS-inspired light chrome (tool windows + Object Explorer).
LIGHT: dict[str, str] = {
    "bg_app": "#F0F0F0",
    "bg_content": "#FFFFFF",
    "bg_card": "#FFFFFF",
    "bg_elevated": "#F0F0F0",
    "bg_input": "#FFFFFF",
    "bg_card_hover": "#E8E8E8",
    "bg_zebra": "#F7F7F7",
    "bg_selection": "#CCE8FF",
    "bg_current_line": "#F0F7FC",
    "border": "#A0A0A0",
    "border_subtle": "#D4D4D4",
    "border_strong": "#6B6B6B",
    "text_primary": "#1E1E1E",
    "text_secondary": "#44546A",
    "text_muted": "#6B6B6B",
    # Tool-window / primary action blue (SSMS / VS)
    "primary": "#0078D4",
    "primary_hover": "#106EBE",
    "primary_muted": "#DEECF9",
    "header_bar": "#0078D4",
    "header_text": "#FFFFFF",
    "accent": "#0078D4",
    "accent_hover": "#106EBE",
    "text_on_primary": "#FFFFFF",
    "success": "#107C10",
    "warning": "#9A6700",
    "error": "#C42B1C",
    "metric_blue": "#0078D4",
    "metric_amber": "#9A6700",
    "gutter": "#F3F3F3",
    "gutter_text": "#8A8A8A",
    "status_bar": "#ECEEF1",
    "status_text": "#44546A",
    "tree_guide": "#A0A0A0",
    "toolbar": "#F0F0F0",
}

SIDEBAR: dict[str, str] = {
    "bg": "#F5F5F5",
    "card": "#FFFFFF",
    "border": "#CCCEDB",
    "text": "#1E1E1E",
    "muted": "#6B6B6B",
    "hover": "#E8E8E8",
    "active": "#CCE8FF",
    "accent": "#0078D4",
}


def cx(theme: ThemeManager, key: str) -> str:
    return LIGHT.get(key, theme.c(key))
