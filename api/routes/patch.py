"""Patch management API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_current_user, get_patch_service
from backend.core.exceptions import PatchError
from backend.models.schemas import PatchHistoryEntry, PatchRequest, UserRole
from backend.services.patch_service import PatchService

router = APIRouter(prefix="/patches", tags=["Patches"])


@router.post("/execute", response_model=PatchHistoryEntry)
async def execute_patch(
    request: PatchRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[PatchService, Depends(get_patch_service)],
) -> PatchHistoryEntry:
    role = UserRole(current_user["role"])
    if not check_permission(role, "patch:create"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.execute_patch(request, user=current_user["username"])
    except PatchError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/history")
async def get_patch_history(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[PatchService, Depends(get_patch_service)],
    limit: int = 50,
) -> list[PatchHistoryEntry]:
    role = UserRole(current_user["role"])
    if not check_permission(role, "patch:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.get_history(limit)


@router.post("/rollback/{patch_id}", response_model=PatchHistoryEntry)
async def rollback_patch(
    patch_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[PatchService, Depends(get_patch_service)],
) -> PatchHistoryEntry:
    role = UserRole(current_user["role"])
    if not check_permission(role, "patch:rollback"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.rollback_patch(patch_id, user=current_user["username"])
    except PatchError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/{patch_id}/report")
async def get_patch_report(
    patch_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[PatchService, Depends(get_patch_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "patch:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.get_report(patch_id)
