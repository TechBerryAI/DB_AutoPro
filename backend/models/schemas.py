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


class BackupMode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"


class PgBackupScope(str, Enum):
    ENTIRE_DATABASE = "entire_database"
    SINGLE_SCHEMA = "single_schema"
    MULTIPLE_SCHEMAS = "multiple_schemas"
    SELECTED_TABLES = "selected_tables"
    DATA_ONLY = "data_only"
    SCHEMA_ONLY = "schema_only"
    SELECTED_OBJECTS = "selected_objects"


class PgDumpFormat(str, Enum):
    PLAIN = "plain"
    CUSTOM = "custom"
    TAR = "tar"
    DIRECTORY = "directory"


class MySQLBackupScope(str, Enum):
    ENTIRE_DATABASE = "entire_database"
    SELECTED_TABLES = "selected_tables"
    DATA_ONLY = "data_only"
    SCHEMA_ONLY = "schema_only"


class MySQLDumpFormat(str, Enum):
    PLAIN = "plain"
    COMPRESSED = "compressed"


class MongoRestoreScope(str, Enum):
    ALL_DATABASES = "all_databases"
    SPECIFIC_DATABASE = "specific_database"
    SINGLE_COLLECTION = "single_collection"
    ARCHIVE = "archive"


class RestoreSourceType(str, Enum):
    LOCAL = "local"
    CONNECTED_SERVER = "connected_server"
    OTHER_SERVER = "other_server"


class RestoreTargetType(str, Enum):
    LOCAL = "local"
    LINUX_SERVER = "linux_server"


class BackupSourceType(str, Enum):
    LOCAL = "local"
    CONNECTED_SERVER = "connected_server"
    OTHER_SERVER = "other_server"


class BackupMethod(str, Enum):
    PG_DUMP = "pg_dump"
    PG_DUMPALL = "pg_dumpall"
    PG_BASEBACKUP = "pg_basebackup"
    MYSQLDUMP = "mysqldump"
    MYSQL_PHYSICAL = "mysql_physical"
    MONGODUMP = "mongodump"
    MONGORESTORE = "mongorestore"
    MSSQL_BACKUP = "mssql_backup"
    ORACLE_EXPDP = "expdp"
    ORACLE_IMPDP = "impdp"


_BACKUP_METHODS: dict[tuple[DatabaseType, BackupCategory], list[BackupMethod]] = {
    (DatabaseType.POSTGRESQL, BackupCategory.LOGICAL): [
        BackupMethod.PG_DUMP,
        BackupMethod.PG_DUMPALL,
    ],
    (DatabaseType.POSTGRESQL, BackupCategory.PHYSICAL): [BackupMethod.PG_BASEBACKUP],
    (DatabaseType.MYSQL, BackupCategory.LOGICAL): [BackupMethod.MYSQLDUMP],
    (DatabaseType.MYSQL, BackupCategory.PHYSICAL): [BackupMethod.MYSQL_PHYSICAL],
    (DatabaseType.MONGODB, BackupCategory.LOGICAL): [BackupMethod.MONGODUMP],
    (DatabaseType.MSSQL, BackupCategory.PHYSICAL): [BackupMethod.MSSQL_BACKUP],
    (DatabaseType.ORACLE, BackupCategory.LOGICAL): [BackupMethod.ORACLE_EXPDP],
}


def backup_methods_for(
    db_type: DatabaseType, category: BackupCategory
) -> list[BackupMethod]:
    """Return backup tools available for a database type and backup category."""
    return list(_BACKUP_METHODS.get((db_type, category), []))


def backup_modes_for(
    db_type: DatabaseType, category: BackupCategory
) -> list[BackupMode]:
    """Return backup modes supported for a database type and backup category."""
    if db_type == DatabaseType.MONGODB:
        return [BackupMode.FULL]
    if category == BackupCategory.PHYSICAL:
        if db_type == DatabaseType.POSTGRESQL:
            return [BackupMode.FULL, BackupMode.INCREMENTAL]
        if db_type == DatabaseType.MSSQL:
            return [BackupMode.FULL, BackupMode.DIFFERENTIAL]
        return [BackupMode.FULL]
    if db_type == DatabaseType.POSTGRESQL:
        return [BackupMode.FULL, BackupMode.INCREMENTAL, BackupMode.DIFFERENTIAL]
    if db_type == DatabaseType.MYSQL:
        return [BackupMode.FULL, BackupMode.INCREMENTAL]
    if db_type == DatabaseType.MSSQL:
        return [BackupMode.FULL, BackupMode.DIFFERENTIAL]
    if db_type == DatabaseType.ORACLE:
        return [BackupMode.FULL, BackupMode.INCREMENTAL]
    return [BackupMode.FULL]


def backup_method_category(method: BackupMethod) -> BackupCategory:
    """Classify a backup method as logical or physical."""
    if method in (
        BackupMethod.PG_BASEBACKUP,
        BackupMethod.MSSQL_BACKUP,
        BackupMethod.MYSQL_PHYSICAL,
    ):
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
    backup_mode: BackupMode = BackupMode.FULL
    database: str | None = None
    compress: bool = True
    output_path: str | None = None
    local_output_dir: str | None = None
    backup_source: BackupSourceType = BackupSourceType.LOCAL
    use_remote_exec: bool = False
    remote_binary_path: str | None = None
    remote_output_dir: str | None = None
    external_ssh_host: str | None = None
    external_ssh_port: int = 22
    external_ssh_username: str | None = None
    external_ssh_password: str | None = None
    backup_folder_name: str | None = None
    collection: str | None = None
    gzip_archive: bool = False
    # PostgreSQL logical pg_dump format: plain | custom | tar | directory
    # Legacy aliases still accepted via pg_file_extension: sql→plain, dump→custom.
    pg_dump_format: PgDumpFormat | None = None
    pg_file_extension: str | None = None
    pg_backup_scope: PgBackupScope | None = None
    pg_scope_targets: list[str] = Field(default_factory=list)
    pg_advanced_args: str | None = None
    # MySQL logical mysqldump options
    mysql_dump_format: MySQLDumpFormat | None = None
    mysql_backup_scope: MySQLBackupScope | None = None
    mysql_scope_targets: list[str] = Field(default_factory=list)


class RestoreRequest(BaseModel):
    profile: str = "default"
    db_type: DatabaseType = DatabaseType.POSTGRESQL
    backup_path: str
    database: str | None = None
    collection: str | None = None
    mongo_restore_scope: MongoRestoreScope | None = None
    gzip_archive: bool = False
    validate_backup: bool = True
    restore_source: RestoreSourceType = RestoreSourceType.LOCAL
    restore_target: RestoreTargetType = RestoreTargetType.LINUX_SERVER
    use_remote_exec: bool = False
    remote_binary_path: str | None = None
    remote_source_dir: str | None = None
    external_ssh_host: str | None = None
    external_ssh_port: int = 22
    external_ssh_username: str | None = None
    external_ssh_password: str | None = None
    drop_existing: bool = False


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
