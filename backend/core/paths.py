"""Repository layout.

Source and committed configuration stay at the project root.
Anything the app writes at runtime lives under ``var/`` so a checkout
stays limited to code, tests, docs, and config templates.
"""

from __future__ import annotations

import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

VAR_DIR = PROJECT_ROOT / "var"
DATA_DIR = VAR_DIR / "data"
LOGS_DIR = VAR_DIR / "logs"
BACKUPS_DIR = VAR_DIR / "backups"

_LEGACY_FILES = (
    (CONFIG_DIR / "saved_servers.json", DATA_DIR / "saved_servers.json"),
    (CONFIG_DIR / "employees.json", DATA_DIR / "employees.json"),
    (CONFIG_DIR / "ui_preferences.json", DATA_DIR / "ui_preferences.json"),
)


def _move_if_free(old: Path, new: Path) -> None:
    """Move a leftover runtime file. Skip it if another process still has it open."""
    if not old.exists() or new.exists():
        return
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
    except OSError:
        return


def _retire_legacy_dir(legacy: Path, destination: Path) -> None:
    if not legacy.is_dir():
        return
    for item in list(legacy.iterdir()):
        _move_if_free(item, destination / item.name)
    try:
        if not any(legacy.iterdir()):
            legacy.rmdir()
    except OSError:
        return


def ensure_runtime_dirs() -> None:
    """Create runtime folders and move leftover files from older layouts."""
    for directory in (DATA_DIR, LOGS_DIR, BACKUPS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    for old, new in _LEGACY_FILES:
        _move_if_free(old, new)

    _retire_legacy_dir(PROJECT_ROOT / "logs", LOGS_DIR)
    _retire_legacy_dir(PROJECT_ROOT / "backups", BACKUPS_DIR)


ensure_runtime_dirs()
