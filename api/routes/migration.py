"""Migration API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_current_user, get_migration_service
from backend.core.exceptions import MigrationError, MigrationRejected
from backend.models.schemas import MigrationRequest, OperationResult, UserRole
from backend.services.migration_service import MigrationService
from backend.services.mysql_patch.redaction import redact_text

router = APIRouter(prefix="/migrations", tags=["Migrations"])


@router.post("/preview", response_model=OperationResult)
async def preview_migration(
    request: MigrationRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MigrationService, Depends(get_migration_service)],
) -> OperationResult:
    role = UserRole(current_user["role"])
    if not check_permission(role, "migration:create"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.preview(request, user=current_user["username"])
    except MigrationRejected as exc:
        raise HTTPException(400, redact_text(str(exc)) or "Migration was rejected.") from exc
    except MigrationError as exc:
        raise HTTPException(500, redact_text(str(exc)) or "Migration failed.") from exc


@router.post("/", response_model=OperationResult)
async def run_migration(
    request: MigrationRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MigrationService, Depends(get_migration_service)],
) -> OperationResult:
    role = UserRole(current_user["role"])
    if not check_permission(role, "migration:create"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.migrate(request, user=current_user["username"])
    except MigrationRejected as exc:
        raise HTTPException(400, redact_text(str(exc)) or "Migration was rejected.") from exc
    except MigrationError as exc:
        raise HTTPException(500, redact_text(str(exc)) or "Migration failed.") from exc


@router.post("/{operation_id}/cancel")
async def cancel_migration(
    operation_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MigrationService, Depends(get_migration_service)],
) -> dict:
    """Request cancellation between batches. A running MySQL statement is not interrupted immediately, and committed DDL is not rolled back."""
    role = UserRole(current_user["role"])
    if not check_permission(role, "migration:create"):
        raise HTTPException(403, "Insufficient permissions")
    accepted = service.request_cancel(operation_id)
    if not accepted:
        raise HTTPException(404, "No running migration was found to cancel.")
    return {
        "operation_id": operation_id,
        "cancel_requested": True,
        "detail": "Cancellation will be checked between batches and tables. The current statement can finish, and committed MySQL DDL is not rolled back.",
    }


@router.post("/{operation_id}/reconcile")
async def reconcile_migration(
    operation_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MigrationService, Depends(get_migration_service)],
) -> dict:
    """Record that the operator reviewed an incomplete migration. This does not rerun it."""
    role = UserRole(current_user["role"])
    if not check_permission(role, "migration:create"):
        raise HTTPException(403, "Insufficient permissions")
    row = service.reconcile(operation_id)
    if row is None:
        raise HTTPException(404, "Migration operation not found.")
    return {
        "operation_id": operation_id,
        "status": row["status"],
        "reconciled": bool(row["reconciled"]),
        "recovery": row["recovery"],
    }


@router.get("/{operation_id}/progress")
async def get_migration_progress(
    operation_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MigrationService, Depends(get_migration_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "migration:read"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.get_progress(operation_id)
    except MigrationError as exc:
        raise HTTPException(404, str(exc)) from exc
