"""Pydantic models and schemas for Database Automation Pro."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DatabaseType(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MONGODB = "mongodb"
    MSSQL = "mssql"
    ORACLE = "oracle"


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class BackupCategory(str, Enum):
    LOGICAL = "logical"
    PHYSICAL = "physical"


class BackupMethod(str, Enum):
    PG_DUMP = "pg_dump"
    PG_DUMPALL = "pg_dumpall"
    PG_BASEBACKUP = "pg_basebackup"
    MYSQLDUMP = "mysqldump"
    MONGODUMP = "mongodump"
    MSSQL_BACKUP = "mssql_backup"
    ORACLE_EXPDP = "expdp"


_BACKUP_METHODS: dict[tuple[DatabaseType, BackupCategory], list[BackupMethod]] = {
    (DatabaseType.POSTGRESQL, BackupCategory.LOGICAL): [
        BackupMethod.PG_DUMP,
        BackupMethod.PG_DUMPALL,
    ],
    (DatabaseType.POSTGRESQL, BackupCategory.PHYSICAL): [BackupMethod.PG_BASEBACKUP],
    (DatabaseType.MYSQL, BackupCategory.LOGICAL): [BackupMethod.MYSQLDUMP],
    (DatabaseType.MONGODB, BackupCategory.LOGICAL): [BackupMethod.MONGODUMP],
    (DatabaseType.MSSQL, BackupCategory.PHYSICAL): [BackupMethod.MSSQL_BACKUP],
    (DatabaseType.ORACLE, BackupCategory.LOGICAL): [BackupMethod.ORACLE_EXPDP],
}


def backup_methods_for(
    db_type: DatabaseType, category: BackupCategory
) -> list[BackupMethod]:
    """Return backup tools available for a database type and backup category."""
    return list(_BACKUP_METHODS.get((db_type, category), []))


def backup_method_category(method: BackupMethod) -> BackupCategory:
    """Classify a backup method as logical or physical."""
    if method in (BackupMethod.PG_BASEBACKUP, BackupMethod.MSSQL_BACKUP):
        return BackupCategory.PHYSICAL
    return BackupCategory.LOGICAL


class OperationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConnectionRequest(BaseModel):
    profile: str = "default"
    db_type: DatabaseType = DatabaseType.POSTGRESQL


class BackupRequest(BaseModel):
    profile: str = "default"
    db_type: DatabaseType = DatabaseType.POSTGRESQL
    method: BackupMethod = BackupMethod.PG_DUMP
    database: str | None = None
    compress: bool = True
    output_path: str | None = None
    local_output_dir: str | None = None
    use_remote_exec: bool = False
    remote_binary_path: str | None = None
    remote_output_dir: str | None = None
    backup_folder_name: str | None = None
    collection: str | None = None


class RestoreRequest(BaseModel):
    profile: str = "default"
    db_type: DatabaseType = DatabaseType.POSTGRESQL
    backup_path: str
    database: str | None = None
    validate_backup: bool = True


class PatchRequest(BaseModel):
    profile: str = "default"
    db_type: DatabaseType = DatabaseType.POSTGRESQL
    sql_file: str
    description: str = ""
    rollback_file: str | None = None


class FDWRequest(BaseModel):
    profile: str = "default"
    extension: str = "postgres_fdw"
    server_name: str
    foreign_db: str
    foreign_host: str
    foreign_port: int = 5432
    foreign_user: str
    foreign_password: str
    local_user: str
    schema_name: str | None = None


class MigrationRequest(BaseModel):
    profile: str = "default"
    source_type: DatabaseType
    target_type: DatabaseType
    source_profile: str = "default"
    target_profile: str = "default"
    schema_only: bool = False
    tables: list[str] = Field(default_factory=list)


class OperationResult(BaseModel):
    operation_id: str
    status: OperationStatus
    message: str
    started_at: datetime
    completed_at: datetime | None = None
    output_path: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PatchHistoryEntry(BaseModel):
    patch_id: str
    sql_file: str
    description: str
    executed_at: datetime
    status: OperationStatus
    executed_by: str
    rollback_available: bool = False


class HealthCheckResult(BaseModel):
    service: str
    status: str
    latency_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DashboardStats(BaseModel):
    total_backups: int = 0
    successful_backups: int = 0
    failed_backups: int = 0
    pending_operations: int = 0
    last_backup: datetime | None = None
    database_connections: int = 0
    patch_count: int = 0
    storage_used_mb: float = 0.0


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserLogin(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole = UserRole.VIEWER


class UserResponse(BaseModel):
    username: str
    role: UserRole
    active: bool = True
