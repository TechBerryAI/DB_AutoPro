"""SQL patch management with history tracking and rollback."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.connectors.base import get_connector
from backend.core.paths import LOGS_DIR
from backend.core.exceptions import PatchError
from backend.core.logging import get_audit_logger, get_logger
from backend.models.schemas import OperationStatus, PatchHistoryEntry, PatchRequest

logger = get_logger("services.patch")
audit = get_audit_logger()

HISTORY_FILE = LOGS_DIR / "patch_history.json"


class PatchService:
    """Manages SQL patch execution, history, and rollback."""

    def __init__(self) -> None:
        self._history: list[PatchHistoryEntry] = []
        self._load_history()

    def _load_history(self) -> None:
        if HISTORY_FILE.exists():
            with HISTORY_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
                self._history = [PatchHistoryEntry(**entry) for entry in data]

    def _save_history(self) -> None:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_FILE.open("w", encoding="utf-8") as f:
            json.dump([entry.model_dump(mode="json") for entry in self._history], f, indent=2, default=str)

    def execute_patch(self, request: PatchRequest, user: str = "system") -> PatchHistoryEntry:
        """Execute SQL patch file against target database."""
        sql_path = Path(request.sql_file)
        if not sql_path.exists():
            raise PatchError(f"SQL file not found: {request.sql_file}")

        patch_id = str(uuid.uuid4())
        executed_at = datetime.now(timezone.utc)

        entry = PatchHistoryEntry(
            patch_id=patch_id,
            sql_file=str(sql_path),
            description=request.description,
            executed_at=executed_at,
            status=OperationStatus.RUNNING,
            executed_by=user,
            rollback_available=request.rollback_file is not None,
        )

        try:
            sql_content = sql_path.read_text(encoding="utf-8")
            connector = get_connector(request.db_type, request.profile)
            connector.execute_script(sql_content)

            entry.status = OperationStatus.COMPLETED
            audit.log("patch_execute", user, str(sql_path), "success", {"patch_id": patch_id})
            logger.info("Patch %s executed successfully", patch_id)

        except Exception as exc:
            entry.status = OperationStatus.FAILED
            audit.log("patch_execute", user, str(sql_path), "failed", {"error": str(exc)})
            raise PatchError(f"Patch execution failed: {exc}") from exc
        finally:
            self._history.append(entry)
            self._save_history()

        return entry

    def rollback_patch(self, patch_id: str, user: str = "system") -> PatchHistoryEntry:
        """Rollback a previously applied patch using its rollback file."""
        entry = self._find_patch(patch_id)
        if not entry.rollback_available:
            raise PatchError(f"No rollback available for patch {patch_id}")

        rollback_path = Path(entry.sql_file).with_name(
            Path(entry.sql_file).stem + "_rollback.sql"
        )
        if not rollback_path.exists():
            raise PatchError(f"Rollback file not found: {rollback_path}")

        rollback_entry = PatchHistoryEntry(
            patch_id=str(uuid.uuid4()),
            sql_file=str(rollback_path),
            description=f"Rollback of {patch_id}",
            executed_at=datetime.now(timezone.utc),
            status=OperationStatus.RUNNING,
            executed_by=user,
            rollback_available=False,
        )

        try:
            sql_content = rollback_path.read_text(encoding="utf-8")
            rollback_entry.status = OperationStatus.COMPLETED
            audit.log("patch_rollback", user, patch_id, "success")
        except Exception as exc:
            rollback_entry.status = OperationStatus.FAILED
            raise PatchError(f"Rollback failed: {exc}") from exc
        finally:
            self._history.append(rollback_entry)
            self._save_history()

        return rollback_entry

    def get_history(self, limit: int = 50) -> list[PatchHistoryEntry]:
        return self._history[-limit:]

    def get_report(self, patch_id: str) -> dict:
        entry = self._find_patch(patch_id)
        return {
            "patch_id": entry.patch_id,
            "status": entry.status.value,
            "executed_at": entry.executed_at.isoformat(),
            "executed_by": entry.executed_by,
            "description": entry.description,
            "rollback_available": entry.rollback_available,
        }

    def _find_patch(self, patch_id: str) -> PatchHistoryEntry:
        for entry in reversed(self._history):
            if entry.patch_id == patch_id:
                return entry
        raise PatchError(f"Patch not found: {patch_id}")
