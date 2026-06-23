"""Locate MongoDB Database Tools binaries on the local machine."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_local_mongo_tool(
    tool: str,
    preferred_path: str | None = None,
    *,
    _skip_sibling: bool = False,
) -> str:
    """
    Find mongodump / mongorestore on this PC.

    Checks PATH, a configured path, sibling of the other tool, and common
    Windows install folders (e.g. C:\\Program Files\\MongoDB\\Tools).
    """
    name = tool.strip()
    if not name:
        raise ValueError("tool name is required")

    candidates: list[Path] = []

    if preferred_path and preferred_path.strip():
        pref = Path(preferred_path.strip())
        if name not in pref.name.lower():
            if "mongodump" in pref.name:
                pref = pref.with_name(pref.name.replace("mongodump", name))
            elif "mongorestore" in pref.name:
                pref = pref.with_name(pref.name.replace("mongorestore", name))
            else:
                pref = pref.parent / f"{name}.exe" if sys.platform == "win32" else pref.parent / name
        candidates.append(pref)

    for lookup in (name, f"{name}.exe"):
        found = shutil.which(lookup)
        if found:
            candidates.append(Path(found))

    if sys.platform == "win32":
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
        ]
        for root in program_files:
            if not root:
                continue
            tools_root = Path(root) / "MongoDB" / "Tools"
            if tools_root.is_dir():
                candidates.extend(tools_root.glob(f"*/bin/{name}.exe"))
                candidates.append(tools_root / "bin" / f"{name}.exe")

    if not _skip_sibling:
        other = "mongorestore" if name == "mongodump" else "mongodump"
        try:
            sibling = resolve_local_mongo_tool(other, _skip_sibling=True)
            sibling_path = Path(sibling)
            candidates.append(
                sibling_path.with_name(f"{name}.exe" if sys.platform == "win32" else name)
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

    raise FileNotFoundError(
        f"'{name}' was not found on this PC. "
        "Install MongoDB Database Tools and add the bin folder to PATH, "
        "or enable remote restore to run on the Linux server."
    )
