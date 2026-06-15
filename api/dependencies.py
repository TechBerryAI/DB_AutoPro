"""FastAPI dependencies for authentication and services."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth.jwt_handler import decode_token, get_user
from backend.services.backup_service import BackupService
from backend.services.fdw_service import FDWService
from backend.services.migration_service import MigrationService
from backend.services.monitoring_service import MonitoringService
from backend.services.patch_service import PatchService
from backend.services.restore_service import RestoreService
from backend.services.scheduler_service import SchedulerService

security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict[str, Any]:
    try:
        payload = decode_token(credentials.credentials)
        username = payload.get("sub")
        if not username:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        user = get_user(username)
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
        return {"username": username, "role": payload.get("role", "viewer")}
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


def get_backup_service() -> BackupService:
    return BackupService()


def get_restore_service() -> RestoreService:
    return RestoreService()


def get_patch_service() -> PatchService:
    return PatchService()


def get_fdw_service() -> FDWService:
    return FDWService()


def get_migration_service() -> MigrationService:
    return MigrationService()


def get_monitoring_service() -> MonitoringService:
    return MonitoringService()


def get_scheduler_service() -> SchedulerService:
    return SchedulerService()
