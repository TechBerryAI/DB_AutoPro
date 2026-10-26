"""Locate MongoDB Database Tools binaries on the local machine."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Legacy default shipped in early Backup / Restore forms. It is not present on
# current Linux hosts, so treat it as unset and discover mongorestore instead.
_PLACEHOLDER_MONGO_TOOLS_MARKERS = (
    "mongodb-database-tools-ubuntu2204-x86_64-100.17.0",
)


def is_placeholder_remote_mongo_path(path: str | None) -> bool:
    """True when the path is empty or the old /mnt Database Tools default."""
    text = (path or "").strip().replace("\\", "/")
    if not text:
        return True
    return any(marker in text for marker in _PLACEHOLDER_MONGO_TOOLS_MARKERS)


def sibling_mongo_tool_path(path: str | None, tool: str) -> str | None:
    """Swap mongodump ↔ mongorestore in a configured binary path.

    Remote Linux paths stay POSIX even when this app is running on Windows.
    """
    text = (path or "").strip().replace("\\", "/")
    if not text or is_placeholder_remote_mongo_path(text):
        return None
    # Path() on Windows would turn /riya/... into \\riya\\...; keep the original
    # slashes and only rewrite the final component.
    name = text.rsplit("/", 1)[-1]
    parent = text[: -len(name)].rstrip("/") if name else text.rstrip("/")
    if tool in name:
        return text
    if "mongodump" in name:
        return f"{parent}/{name.replace('mongodump', tool)}" if parent else name.replace("mongodump", tool)
    if "mongorestore" in name:
        return f"{parent}/{name.replace('mongorestore', tool)}" if parent else name.replace("mongorestore", tool)
    return f"{parent}/{tool}" if parent else tool


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
