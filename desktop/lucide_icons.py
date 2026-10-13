"""Lucide icon rendering for CustomTkinter (official Lucide SVGs + resvg)."""

from __future__ import annotations

import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import customtkinter as ctk
import resvg_py
from PIL import Image

_ICONS_DIR = Path(__file__).resolve().parent / "assets" / "lucide"

# Dashboard / UI icon name aliases → Lucide file stem
LUCIDE_ALIASES: dict[str, str] = {
    "linux_servers": "server",
    "servers": "server",
    "databases": "database",
    "healthy_dbs": "heart-pulse",
    "active_jobs": "activity",
    "active_alerts": "bell",
    "failed_today": "hard-drive",
    "refresh": "refresh-cw",
    "shield": "shield-check",
    "alert_critical": "circle-alert",
    "alert_warning": "triangle-alert",
    "alert_info": "circle-alert",
    "check": "circle-check",
    "empty_db": "database",
    "empty_jobs": "briefcase",
    "empty_activity": "activity",
    "dashboard": "layout-dashboard",
    "backup": "archive",
    "restore": "upload",
    "logs": "scroll-text",
    "audit": "shield-check",
    "reports": "file-chart-column",
    "settings": "settings",
    "sign_out": "log-out",
    "logout": "log-out",
    "theme_light": "sun",
    "theme_dark": "moon",
    "cpu": "cpu",
    "migration": "briefcase",
    "patching": "circle-check",
    "mysql_patch": "database-zap",
    "monitoring": "activity",
}


def _resolve_name(name: str) -> str:
    return LUCIDE_ALIASES.get(name, name)


def _load_svg(name: str) -> str:
    stem = _resolve_name(name)
    path = _ICONS_DIR / f"{stem}.svg"
    if not path.exists():
        raise FileNotFoundError(f"Lucide icon not found: {stem} ({path})")
    return path.read_text(encoding="utf-8")


def _colored_svg(svg: str, color: str, *, stroke_width: float | None = None) -> str:
    """Apply stroke color (and optional width) to a Lucide SVG."""
    hex_color = color if color.startswith("#") else f"#{color}"
    out = svg.replace("currentColor", hex_color)
    out = re.sub(r'stroke="[^"]*"', f'stroke="{hex_color}"', out, count=1)
    if stroke_width is not None:
        out = re.sub(
            r'stroke-width="[^"]*"',
            f'stroke-width="{stroke_width}"',
            out,
            count=1,
        )
    return out


@lru_cache(maxsize=256)
def render_lucide_png(
    name: str,
    color: str,
    size: int = 48,
    stroke_width: float = 2.0,
) -> bytes:
    """Rasterize a Lucide icon to PNG bytes (size×size)."""
    svg = _colored_svg(_load_svg(name), color, stroke_width=stroke_width)
    return resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)


@lru_cache(maxsize=256)
def lucide_pil_image(
    name: str,
    color: str,
    size: int = 48,
    stroke_width: float = 2.0,
) -> Image.Image:
    png = render_lucide_png(name, color, size, stroke_width)
    return Image.open(BytesIO(png)).convert("RGBA")


def lucide_ctk_image(
    name: str,
    color: str,
    *,
    size: int = 18,
    stroke_width: float = 2.0,
) -> ctk.CTkImage:
    """CTkImage for a Lucide icon (2× raster for sharpness)."""
    raster = max(size * 2, 32)
    img = lucide_pil_image(name, color, raster, stroke_width)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def lucide_icon_label(
    parent,
    name: str,
    color: str,
    *,
    size: int = 18,
    bg: str | tuple | None = "transparent",
) -> ctk.CTkLabel:
    """Label that displays only a Lucide icon (keeps image ref on the widget)."""
    image = lucide_ctk_image(name, color, size=size)
    lbl = ctk.CTkLabel(parent, text="", image=image, fg_color=bg or "transparent", width=size, height=size)
    lbl._lucide_image = image  # type: ignore[attr-defined]
    return lbl


def lucide_icon_badge(
    parent,
    name: str,
    color: str,
    bg: str,
    *,
    box: int = 34,
    icon_size: int = 18,
    radius: int = 10,
) -> ctk.CTkFrame:
    """Rounded tinted container with a centered Lucide icon."""
    frame = ctk.CTkFrame(parent, width=box, height=box, corner_radius=radius, fg_color=bg)
    frame.pack_propagate(False)
    icon = lucide_icon_label(frame, name, color, size=icon_size)
    icon.place(relx=0.5, rely=0.5, anchor="center")
    frame._lucide_child = icon  # type: ignore[attr-defined]
    return frame
