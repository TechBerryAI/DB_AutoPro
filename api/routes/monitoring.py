"""Monitoring and health check API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth.rbac import check_permission
from api.dependencies import get_current_user, get_monitoring_service, get_scheduler_service
from backend.models.schemas import DashboardStats, HealthCheckResult, UserRole
from backend.services.monitoring_service import MonitoringService
from backend.services.scheduler_service import SchedulerService

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


@router.get("/health", response_model=list[HealthCheckResult])
async def health_check(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MonitoringService, Depends(get_monitoring_service)],
) -> list[HealthCheckResult]:
    role = UserRole(current_user["role"])
    if not check_permission(role, "monitoring:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.health_check()


@router.get("/dashboard", response_model=DashboardStats)
async def dashboard_stats(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MonitoringService, Depends(get_monitoring_service)],
) -> DashboardStats:
    role = UserRole(current_user["role"])
    if not check_permission(role, "monitoring:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.get_dashboard_stats()


@router.get("/metrics")
async def system_metrics(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MonitoringService, Depends(get_monitoring_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "monitoring:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.get_system_metrics()


@router.get("/backups/report")
async def backup_report(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MonitoringService, Depends(get_monitoring_service)],
) -> dict:
    role = UserRole(current_user["role"])
    if not check_permission(role, "monitoring:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.get_backup_report()


@router.get("/audit-logs")
async def audit_logs(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[MonitoringService, Depends(get_monitoring_service)],
    limit: int = 100,
) -> list:
    role = UserRole(current_user["role"])
    if not check_permission(role, "monitoring:read"):
        raise HTTPException(403, "Insufficient permissions")
    return service.get_audit_logs(limit)


@router.get("/scheduler/jobs")
async def list_scheduled_jobs(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    service: Annotated[SchedulerService, Depends(get_scheduler_service)],
) -> list:
    role = UserRole(current_user["role"])
    if not check_permission(role, "scheduler:manage"):
        raise HTTPException(403, "Insufficient permissions")
    return service.list_jobs()
