"""Small brand-style engine icons for the infrastructure table."""

from __future__ import annotations

from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw


_ENGINE_PALETTE = {
    "POSTGRESQL": ("#336791", "#FFFFFF"),
    "ORACLE": ("#C74634", "#FFFFFF"),
    "MONGODB": ("#00684A", "#FFFFFF"),
    "MYSQL": ("#00758F", "#FFFFFF"),
    "MARIADB": ("#C3368B", "#FFFFFF"),
    "SQLSERVER": ("#CC2927", "#FFFFFF"),
}


def _norm(engine: str) -> str:
    return str(engine or "").strip().upper().replace(" ", "")


@lru_cache(maxsize=64)
def _engine_pil(engine: str, size: int = 32) -> Image.Image:
    key = _norm(engine)
    bg, fg = _ENGINE_PALETTE.get(key, ("#64748B", "#FFFFFF"))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(1, size // 10)
    draw.rounded_rectangle(
        (pad, pad, size - pad - 1, size - pad - 1),
        radius=max(3, size // 5),
        fill=bg,
    )

    cx = cy = size // 2
    if key == "POSTGRESQL":
        # Elephant-like oval + ear
        draw.ellipse((cx - 7, cy - 5, cx + 7, cy + 8), fill=fg)
        draw.ellipse((cx - 10, cy - 2, cx - 3, cy + 5), fill=fg)
        draw.ellipse((cx + 3, cy - 2, cx + 10, cy + 5), fill=fg)
        draw.ellipse((cx - 3, cy - 1, cx + 1, cy + 3), fill=bg)
    elif key == "ORACLE":
        # Red hollow ellipse (Oracle mark)
        draw.ellipse((cx - 9, cy - 5, cx + 9, cy + 5), outline=fg, width=max(2, size // 12))
    elif key in {"MONGODB", "MARIADB"}:
        # Leaf / teardrop
        draw.polygon(
            [(cx, cy - 8), (cx + 7, cy + 2), (cx, cy + 9), (cx - 7, cy + 2)],
            fill=fg,
        )
        draw.line((cx, cy - 4, cx, cy + 7), fill=bg, width=max(1, size // 16))
    elif key in {"MYSQL", "SQLSERVER"}:
        # Cylinder / disk stack
        draw.ellipse((cx - 8, cy - 7, cx + 8, cy - 2), fill=fg)
        draw.rectangle((cx - 8, cy - 4, cx + 8, cy + 5), fill=fg)
        draw.ellipse((cx - 8, cy + 2, cx + 8, cy + 8), fill=fg)
        draw.ellipse((cx - 8, cy - 4, cx + 8, cy + 1), outline=bg, width=1)
    else:
        draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=fg)

    return img


def engine_ctk_image(engine: str, *, size: int = 16) -> ctk.CTkImage:
    pil = _engine_pil(_norm(engine), size=max(size * 2, 32))
    return ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))


def engine_icon_label(parent, engine: str, *, size: int = 16) -> ctk.CTkLabel:
    image = engine_ctk_image(engine, size=size)
    lbl = ctk.CTkLabel(parent, text="", image=image, width=size + 2, height=size + 2)
    lbl._engine_image = image  # type: ignore[attr-defined]
    return lbl
