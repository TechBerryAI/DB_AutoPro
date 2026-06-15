"""Restore API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_current_user, get_restore_service
from backend.core.exceptions import RestoreError
from backend.models.schemas import OperationResult, RestoreRequest, UserRole
from backend.services.restore_service import RestoreService

router = APIRouter(prefix="/restore", tags=["Restore"])


@router.post("/", response_model=OperationResult)
async def restore_backup(
    request: RestoreRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[RestoreService, Depends(get_restore_service)],
) -> OperationResult:
    role = UserRole(current_user["role"])
    if not check_permission(role, "restore:create"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.restore(request, user=current_user["username"])
    except RestoreError as exc:
        raise HTTPException(500, str(exc)) from exc
