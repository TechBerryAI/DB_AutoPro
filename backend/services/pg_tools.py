"""Locate PostgreSQL client binaries on the local machine."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_local_pg_tool(tool: str, preferred_path: str | None = None) -> str:
    """
    Find pg_dump and related PostgreSQL CLI tools on this PC.

    Checks PATH, a configured path, sibling binaries, and common Windows
    install folders (e.g. C:\\Program Files\\PostgreSQL\\18\\bin).
    """
    name = tool.strip()
    if not name:
        raise ValueError("tool name is required")

    candidates: list[Path] = []

    if preferred_path and preferred_path.strip():
        pref = Path(preferred_path.strip())
        if name not in pref.name.lower():
            pref = pref.parent / (f"{name}.exe" if sys.platform == "win32" else name)
        candidates.append(pref)

    for lookup in (name, f"{name}.exe"):
        found = shutil.which(lookup)
        if found:
            candidates.append(Path(found))

    if sys.platform == "win32":
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for root in program_files:
            if not root:
                continue
            pg_root = Path(root) / "PostgreSQL"
            if pg_root.is_dir():
                for bin_dir in sorted(pg_root.glob("*/bin"), reverse=True):
                    candidates.append(bin_dir / f"{name}.exe")

    if name != "pg_dump":
        try:
            sibling = resolve_local_pg_tool("pg_dump")
            candidates.append(
                Path(sibling).with_name(f"{name}.exe" if sys.platform == "win32" else name)
            )
        except FileNotFoundError:
            pass

    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return str(path)

    raise FileNotFoundError(name)


def is_pg_custom_dump(path: Path | str) -> bool:
    """True when the file is a pg_dump custom-format archive (magic PGDMP)."""
    target = Path(path)
    if not target.is_file():
        return False
    try:
        with target.open("rb") as handle:
            return handle.read(5) == b"PGDMP"
    except OSError:
        return False
