"""FDW management API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_current_user, get_fdw_service
from backend.core.exceptions import FDWError, ValidationError
from backend.models.schemas import FDWRequest, UserRole
from backend.services.fdw_service import FDWService

router = APIRouter(prefix="/fdw", tags=["Foreign Data Wrappers"])


@router.post("/setup")
async def setup_fdw(
    request: FDWRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[FDWService, Depends(get_fdw_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "fdw:manage"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.setup_complete_fdw(request)
    except (FDWError, ValidationError) as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/extension")
async def create_extension(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[FDWService, Depends(get_fdw_service)],
    profile: str = "default",
    extension: str = "postgres_fdw",
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "fdw:manage"):
        raise HTTPException(403, "Insufficient permissions")
    return service.create_extension(profile, extension)


@router.post("/validate")
async def validate_fdw(
    request: FDWRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[FDWService, Depends(get_fdw_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "fdw:manage"):
        raise HTTPException(403, "Insufficient permissions")
    try:
        return service.validate_fdw(request)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
