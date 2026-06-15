"""Backup API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_backup_service, get_current_user
from backend.core.exceptions import BackupError
from backend.models.schemas import BackupRequest, OperationResult, UserRole
from backend.services.backup_service import BackupService

router = APIRouter(prefix="/backups", tags=["Backups"])


@router.post("/", response_model=OperationResult)
async def create_backup(
    request: BackupRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> OperationResult:
    role = UserRole(current_user["role"])
    if not check_permission(role, "backup:create"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.create_backup(request, user=current_user["username"])
    except BackupError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/")
async def list_backups(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> list:
    role = UserRole(current_user["role"])
    if not check_permission(role, "backup:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.list_backups()


@router.get("/{operation_id}", response_model=OperationResult)
async def get_backup_operation(
    operation_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> OperationResult:
    result = service.get_operation(operation_id)
    if not result:
        raise HTTPException(404, "Operation not found")
    return result


@router.post("/validate")
async def validate_backup(
    backup_path: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> dict:
    try:
        valid = service.validate_backup(backup_path)
        return {"valid": valid, "path": backup_path}
    except BackupError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/retention/apply")
async def apply_retention(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "backup:create"):
        raise HTTPException(403, "Insufficient permissions")
    deleted = service.apply_retention_policy()
    return {"deleted_count": deleted}
