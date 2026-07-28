"""Database explorer provider contracts and shared dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class DatabaseSummary:
    name: str
    size: str = "—"
    owner: str = "—"
    encoding: str = "—"
    collation: str = "—"
    character_type: str = "—"
    tablespace: str = "—"
    connection_limit: str = "—"
    created_date: str = "—"
    last_connected: str = "—"
    server_version: str = "—"
    status: str = "online"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SchemaNode:
    name: str
    table_count: int = 0
    view_count: int = 0
    matview_count: int = 0
    function_count: int = 0
    procedure_count: int = 0
    sequence_count: int = 0
    trigger_count: int = 0
    index_count: int = 0
    foreign_key_count: int = 0


@dataclass
class ObjectRef:
    name: str
    object_type: str
    schema: str
    size: str = "—"
    row_count: str = "—"
    last_modified: str = "—"
    qualified_name: str = ""


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: str
    default_value: str = "—"
    identity: str = "—"
    is_primary_key: bool = False


@dataclass
class TableDetails:
    name: str
    schema: str
    estimated_rows: str = "—"
    table_size: str = "—"
    primary_key: str = "—"
    foreign_keys: list[str] = field(default_factory=list)
    indexes: list[str] = field(default_factory=list)
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass
class StorageSummary:
    used_space: str = "—"
    free_space: str = "—"
    index_size: str = "—"
    toast_size: str = "—"
    largest_tables: list[dict[str, str]] = field(default_factory=list)
    top_objects: list[dict[str, str]] = field(default_factory=list)
    growth_note: str = "—"


@dataclass
class DbStatistics:
    schemas: int = 0
    tables: int = 0
    views: int = 0
    functions: int = 0
    procedures: int = 0
    indexes: int = 0
    sequences: int = 0
    triggers: int = 0
    materialized_views: int = 0


@dataclass
class ConfigInfo:
    items: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ExtensionInfo:
    name: str
    version: str = "—"
    description: str = "—"


@dataclass
class MaintenanceInfo:
    last_vacuum: str = "—"
    last_analyze: str = "—"
    last_autovacuum: str = "—"
    last_reindex: str = "—"
    autovacuum_status: str = "—"
    details: list[dict[str, str]] = field(default_factory=list)


@dataclass
class BackupInfo:
    last_backup: str = "—"
    backup_type: str = "—"
    logical_physical: str = "—"
    backup_method: str = "—"
    backup_scope: str = "—"
    backup_format: str = "—"
    backup_size: str = "—"
    duration: str = "—"
    compression: str = "—"
    status: str = "—"
    last_restore: str = "—"


class ExplorerProvider(Protocol):
    """Engine-specific metadata provider for the Databases explorer page."""

    supports_object_explorer: bool

    def list_databases(self) -> list[DatabaseSummary]:
        ...

    def get_general_info(self, database: str) -> dict[str, str]:
        ...

    def list_schemas(self, database: str) -> list[SchemaNode]:
        ...

    def list_objects(self, database: str, schema: str, object_type: str) -> list[ObjectRef]:
        ...

    def get_table_details(self, database: str, schema: str, table: str) -> TableDetails:
        ...

    def get_storage(self, database: str) -> StorageSummary:
        ...

    def get_statistics(self, database: str) -> DbStatistics:
        ...

    def get_configuration(self, database: str) -> ConfigInfo:
        ...

    def list_extensions(self, database: str) -> list[ExtensionInfo]:
        ...

    def get_maintenance(self, database: str) -> MaintenanceInfo:
        ...

    def get_backup_info(self, database: str) -> BackupInfo:
        ...
