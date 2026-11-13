"""Enterprise classic-desktop surfaces for the administration workspace.

Enterprise SaaS / SSMS hybrid: soft cool content, blue for selection +
primary actions + Object Explorer header, dark navy for the status strip.
"""

from __future__ import annotations

from desktop.theme import ThemeManager

LIGHT: dict[str, str] = {
    "bg_app": "#F4F7FB",
    "bg_content": "#F4F7FB",
    "bg_card": "#FFFFFF",
    "bg_elevated": "#EEF2F7",
    "bg_input": "#FFFFFF",
    "bg_card_hover": "#E8EEF5",
    "bg_zebra": "#F7F9FC",
    "bg_selection": "#D6E9FF",
    "bg_current_line": "#F0F7FC",
    "border": "#D0D7E2",
    "border_subtle": "#E2E8F0",
    "border_strong": "#A8B3C4",
    "text_primary": "#0F172A",
    "text_secondary": "#475569",
    "text_muted": "#64748B",
    # Blue only for selection / primary action / tool-window header
    "primary": "#0078D4",
    "primary_hover": "#106EBE",
    "primary_muted": "#DEECF9",
    "header_bar": "#1B305E",
    "header_text": "#FFFFFF",
    "accent": "#0078D4",
    "accent_hover": "#106EBE",
    "text_on_primary": "#FFFFFF",
    "success": "#107C10",
    "success_muted": "#E6F4E6",
    "warning": "#CA5010",
    "warning_muted": "#FFF8E1",
    "error": "#C42B1C",
    "error_muted": "#FDE7E9",
    "info": "#0078D4",
    "info_muted": "#DEECF9",
    "metric_blue": "#0078D4",
    "metric_amber": "#CA5010",
    "gutter": "#EEF2F7",
    "gutter_text": "#94A3B8",
    # Dark navy chrome (mockup top/bottom bars)
    "title_bar": "#1B305E",
    "title_text": "#FFFFFF",
    "title_muted": "#A8B8D0",
    "status_bar": "#1B305E",
    "status_text": "#E8EEF5",
    "status_muted": "#A8B8D0",
    "active_dot": "#22C55E",
    "tree_guide": "#94A3B8",
    "toolbar": "#EEF2F7",
    "connect_ok": "#107C10",
    "connect_ok_hover": "#0B5A0B",
}

SIDEBAR: dict[str, str] = {
    "bg": "#FFFFFF",
    "card": "#F8FAFC",
    "border": "#D0D7E2",
    "text": "#0F172A",
    "muted": "#64748B",
    "hover": "#E8EEF5",
    "active": "#D6E9FF",
    "accent": "#0078D4",
}


def cx(theme: ThemeManager, key: str) -> str:
    return LIGHT.get(key, theme.c(key))
