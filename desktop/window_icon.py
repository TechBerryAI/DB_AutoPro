"""Apply the shared navy app icon to Tk / CustomTkinter windows."""

from __future__ import annotations

import sys
from pathlib import Path

_ICON_PATH: Path | None = None
_PHOTO_CACHE = None  # shared PhotoImage master can vary; keep per-window refs instead


def _icon_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    roots = [here.parents[1]]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.insert(0, Path(meipass))
    names = (
        Path("deployment") / "assets" / "DataAutomationPro.ico",
        Path("DataAutomationPro.ico"),
        Path("assets") / "DataAutomationPro.ico",
    )
    out: list[Path] = []
    for root in roots:
        for name in names:
            out.append(root / name)
    return out


def resolve_app_icon_path() -> Path | None:
    global _ICON_PATH
    if _ICON_PATH is not None and _ICON_PATH.is_file():
        return _ICON_PATH
    for path in _icon_candidates():
        if path.is_file():
            _ICON_PATH = path
            return path
    return None


def apply_window_icon(window) -> None:
    """Set the Windows title-bar icon when the .ico is available.

    CustomTkinter's CTkToplevel schedules its own blue icon at ~200ms unless
    ``iconbitmap`` was already called (sets ``_iconbitmap_method_called``).
    We set that flag, apply the navy .ico, and also set iconphoto.
    """
    path = resolve_app_icon_path()
    if path is None:
        return

    # Prevent CTk from injecting CustomTkinter_icon_Windows.ico.
    try:
        window._iconbitmap_method_called = True  # type: ignore[attr-defined]
    except Exception:
        pass

    icon = str(path)
    try:
        window.iconbitmap(icon)
    except Exception:
        try:
            window.wm_iconbitmap(icon)
        except Exception:
            pass
    try:
        window.iconbitmap(default=icon)
    except Exception:
        pass

    try:
        from PIL import Image, ImageTk

        with Image.open(path) as src:
            frame = src.convert("RGBA")
            if frame.size != (32, 32):
                frame = frame.resize((32, 32), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(frame)
        window._app_icon_photo = photo  # type: ignore[attr-defined]
        window.iconphoto(False, photo)
        window.iconphoto(True, photo)
    except Exception:
        pass


def schedule_window_icon(window) -> None:
    """Apply now and again after CTk's delayed default-icon hook (~200ms)."""
    apply_window_icon(window)
    for delay in (50, 220, 400):
        try:
            window.after(delay, lambda w=window: apply_window_icon(w))
        except Exception:
            pass
