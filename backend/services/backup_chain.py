"""Backup chain metadata for incremental and differential restore points."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models.schemas import BackupMode, BackupRequest

CHAIN_DIR = ".db_autopro_chain"


def chain_key(request: BackupRequest) -> str:
    db = (request.database or "default").strip()
    return f"{request.profile}_{request.db_type.value}_{db}"


def chain_path(base_dir: Path, request: BackupRequest) -> Path:
    root = base_dir / CHAIN_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{chain_key(request)}.json"


def load_chain(base_dir: Path, request: BackupRequest) -> dict[str, Any]:
    path = chain_path(base_dir, request)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_chain(base_dir: Path, request: BackupRequest, chain: dict[str, Any]) -> None:
    chain_path(base_dir, request).write_text(
        json.dumps(chain, indent=2, default=str),
        encoding="utf-8",
    )


def record_backup(
    base_dir: Path,
    request: BackupRequest,
    record: dict[str, Any],
    mode: BackupMode,
) -> None:
    """Persist the latest backup anchor for incremental/differential chains."""
    chain = load_chain(base_dir, request)
    entry = {
        **record,
        "mode": mode.value,
        "method": request.method.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    chain["last"] = entry
    if mode == BackupMode.FULL:
        chain["full"] = entry
    save_chain(base_dir, request, chain)


def anchor_for_mode(chain: dict[str, Any], mode: BackupMode) -> dict[str, Any] | None:
    if mode == BackupMode.DIFFERENTIAL:
        return chain.get("full")
    if mode == BackupMode.INCREMENTAL:
        return chain.get("last") or chain.get("full")
    return None
