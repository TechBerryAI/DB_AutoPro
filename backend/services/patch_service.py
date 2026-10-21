"""SQL patch management with history tracking and rollback."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from backend.connectors.base import get_connector
from backend.core.paths import LOGS_DIR
from backend.core.exceptions import PatchError
from backend.core.logging import get_audit_logger, get_logger
from backend.models.schemas import OperationStatus, PatchHistoryEntry, PatchRequest
from backend.services.mysql_patch.redaction import redact_text

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
        payload = json.dumps(
            [entry.model_dump(mode="json") for entry in self._history],
            indent=2,
            default=str,
        )
        temporary = HISTORY_FILE.with_name(HISTORY_FILE.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(HISTORY_FILE)

    def execute_patch(
        self,
        request: PatchRequest,
        user: str = "system",
        *,
        target_database: str = "",
        target_host: str = "",
        checksum_sha256: str = "",
        confirmed_by: str = "",
        patch_name: str = "",
        system_identifier: str = "",
        database_oid: str = "",
        redact_literals: bool = False,
        executor: Callable[[str], None] | None = None,
        audit_result: bool = True,
    ) -> PatchHistoryEntry:
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
            target_database=target_database,
            target_host=target_host,
            checksum_sha256=checksum_sha256,
            confirmed_by=confirmed_by,
            patch_name=patch_name or sql_path.name,
            system_identifier=system_identifier,
            database_oid=database_oid,
        )
        stored_early = executor is not None
        if stored_early:
            self._history.append(entry)
            try:
                self._save_history()
            except OSError as exc:
                self._history.remove(entry)
                raise PatchError("Patch history could not be saved. The patch was not executed.") from exc

        try:
            sql_content = sql_path.read_text(encoding="utf-8")
            if executor is not None:
                executor(sql_content)
            else:
                connector = get_connector(request.db_type, request.profile)
                connector.execute_script(sql_content)

            entry.status = OperationStatus.COMPLETED
            entry.apply_result = "committed"
            if audit_result:
                audit.log("patch_execute", user, str(sql_path), "success", {"patch_id": patch_id})
            logger.info("Patch %s executed successfully", patch_id)

        except Exception as exc:
            entry.status = OperationStatus.FAILED
            entry.error = _sanitized_error(exc, redact_literals=redact_literals)
            audit.log("patch_execute", user, str(sql_path), "failed", {"error": entry.error, "patch_id": patch_id})
            raise PatchError(
                f"Patch execution failed: {entry.error}",
                details={"patch_id": patch_id},
            ) from exc
        finally:
            finished_at = datetime.now(timezone.utc)
            entry.finished_at = finished_at
            entry.duration_seconds = round((finished_at - executed_at).total_seconds(), 3)
            if not stored_early:
                self._history.append(entry)
            try:
                self._save_history()
            except OSError as exc:
                raise PatchError(
                    "Patch history could not be saved after execution. The run is not recorded as successful or verified.",
                    details={"patch_id": patch_id, "history_saved": False},
                ) from exc

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

    def annotate_patch(self, patch_id: str, **fields: object) -> PatchHistoryEntry:
        """Update fields already stored for a patch. Does not execute SQL."""
        entry = self._find_patch(patch_id)
        allowed = {
            "verification_status",
            "verification_summary",
            "apply_result",
            "error",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise PatchError(f"Unknown patch history field: {', '.join(sorted(unknown))}")
        for key, value in fields.items():
            setattr(entry, key, value)
        self._save_history()
        return entry

    def completed_patch_identities(self) -> set[tuple[str, str, str]]:
        """Checksum, cluster system identifier, and database OID for completed patches.

        Rows written before the cluster identity was stored are omitted. A database
        name alone cannot prove which server completed the patch.
        """
        found: set[tuple[str, str, str]] = set()
        for entry in self._history:
            if entry.status is not OperationStatus.COMPLETED:
                continue
            checksum = (entry.checksum_sha256 or "").strip().lower()
            system_identifier = (entry.system_identifier or "").strip()
            database_oid = (entry.database_oid or "").strip()
            if checksum and system_identifier and database_oid:
                found.add((checksum, system_identifier, database_oid))
        return found

    def running_patch_identities(self) -> set[tuple[str, str, str]]:
        """Full identities left running, usually because the process stopped mid-run."""
        found: set[tuple[str, str, str]] = set()
        for entry in self._history:
            if entry.status is not OperationStatus.RUNNING:
                continue
            checksum = (entry.checksum_sha256 or "").strip().lower()
            system_identifier = (entry.system_identifier or "").strip()
            database_oid = (entry.database_oid or "").strip()
            if checksum and system_identifier and database_oid:
                found.add((checksum, system_identifier, database_oid))
        return found

    def legacy_running_patches(self) -> set[tuple[str, str]]:
        """Running rows that have no cluster identity. Matched by checksum and exact database name."""
        found: set[tuple[str, str]] = set()
        for entry in self._history:
            if entry.status is not OperationStatus.RUNNING:
                continue
            if (entry.system_identifier or "").strip() and (entry.database_oid or "").strip():
                continue
            checksum = (entry.checksum_sha256 or "").strip().lower()
            database_name = entry.target_database or ""
            if checksum and database_name:
                found.add((checksum, database_name))
        return found

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


def _sanitized_error(exc: Exception, *, redact_literals: bool) -> str:
    text = redact_text(str(exc)) or "Patch execution failed"
    if redact_literals:
        text = re.sub(r"'(?:[^']|'')*'", "'[value]'", text)
    return text[:500]
