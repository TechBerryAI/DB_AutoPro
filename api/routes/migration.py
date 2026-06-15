"""Migration API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_current_user, get_migration_service
from backend.core.exceptions import MigrationError
from backend.models.schemas import MigrationRequest, OperationResult, UserRole
from backend.services.migration_service import MigrationService

router = APIRouter(prefix="/migrations", tags=["Migrations"])


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
    except MigrationError as exc:
        raise HTTPException(500, str(exc)) from exc


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
