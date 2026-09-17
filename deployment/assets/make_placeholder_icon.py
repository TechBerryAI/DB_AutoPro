"""Generate the TEMPORARY placeholder icon used by the demo build.

The project has no branded icon yet. This script draws a neutral "DA" tile so the
packaged executable, shortcuts and installer are not left with the generic
default. Replace ``DataAutomationPro.ico`` with the real artwork before a
production release; this script is then no longer needed.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_PATH = Path(__file__).resolve().parent / "DataAutomationPro.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]
BG = (37, 99, 235, 255)
FG = (255, 255, 255, 255)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _tile(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, size // 6)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=BG)

    # Three stacked "database" bands, drawn only where they stay legible.
    if size >= 32:
        inset = size * 0.22
        band_h = size * 0.10
        for index in range(3):
            top = size * 0.26 + index * (band_h + size * 0.08)
            draw.rounded_rectangle(
                [(inset, top), (size - inset, top + band_h)],
                radius=band_h / 2,
                fill=FG,
            )
    else:
        text = "DA"
        font = _font(max(8, int(size * 0.55)))
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(
            ((size - box[2] + box[0]) / 2, (size - box[3] + box[1]) / 2 - box[1]),
            text,
            font=font,
            fill=FG,
        )
    return img


def main() -> None:
    base = _tile(256)
    base.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Placeholder icon written to {OUT_PATH}")


if __name__ == "__main__":
    main()
