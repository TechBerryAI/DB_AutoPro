"""Role-based access control for API endpoints."""

from __future__ import annotations

from enum import Enum
from functools import wraps
from typing import Callable

from backend.core.exceptions import AuthorizationError
from backend.models.schemas import UserRole

ROLE_HIERARCHY = {
    UserRole.ADMIN: 3,
    UserRole.OPERATOR: 2,
    UserRole.VIEWER: 1,
}

PERMISSIONS: dict[str, set[UserRole]] = {
    "backup:create": {UserRole.ADMIN, UserRole.OPERATOR},
    "backup:read": {UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "restore:create": {UserRole.ADMIN, UserRole.OPERATOR},
    "restore:read": {UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "patch:create": {UserRole.ADMIN, UserRole.OPERATOR},
    "patch:read": {UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "patch:rollback": {UserRole.ADMIN},
    "fdw:manage": {UserRole.ADMIN, UserRole.OPERATOR},
    "migration:create": {UserRole.ADMIN, UserRole.OPERATOR},
    "migration:read": {UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "monitoring:read": {UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "scheduler:manage": {UserRole.ADMIN},
    "users:manage": {UserRole.ADMIN},
}


def check_permission(role: UserRole, permission: str) -> bool:
    allowed = PERMISSIONS.get(permission, set())
    return role in allowed


def require_permission(permission: str) -> Callable:
    """Decorator to enforce RBAC on route handlers."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_user = kwargs.get("current_user")
            if current_user is None:
                raise AuthorizationError("Authentication required")
            role = UserRole(current_user.get("role", "viewer"))
            if not check_permission(role, permission):
                raise AuthorizationError(
                    f"Role '{role.value}' lacks permission: {permission}"
                )
            return await func(*args, **kwargs)

        return wrapper

    return decorator
