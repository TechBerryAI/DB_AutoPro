"""Stub explorer provider for engines without a full object browser yet."""

from __future__ import annotations

from desktop.connection import ConnectionResult
from desktop.db_catalog import fetch_server_databases
from desktop.explorer.provider import (
    BackupInfo,
    ConfigInfo,
    DatabaseSummary,
    DbStatistics,
    ExtensionInfo,
    MaintenanceInfo,
    ObjectRef,
    SchemaNode,
    StorageSummary,
    TableDetails,
)


class StubExplorerProvider:
    """Lists databases only; object explorer sections return empty/placeholder data."""

    supports_object_explorer = False

    def __init__(self, connection: ConnectionResult) -> None:
        self.connection = connection

    def list_databases(self) -> list[DatabaseSummary]:
        rows = fetch_server_databases(self.connection)
        out: list[DatabaseSummary] = []
        for row in rows:
            out.append(
                DatabaseSummary(
                    name=str(row.get("name", "—")),
                    size=str(row.get("size") or "—"),
                    owner=str(row.get("owner") or "—"),
                    encoding=str(row.get("encoding") or "—"),
                    status=str(row.get("status") or "online"),
                    raw=dict(row),
                )
            )
        return out

    def get_general_info(self, database: str) -> dict[str, str]:
        return {
            "Database Name": database,
            "Note": (
                f"{self.connection.db_type.value.upper()} object explorer is not "
                "available yet. Use Backup / SQL tools for deeper inspection."
            ),
        }

    def list_schemas(self, database: str) -> list[SchemaNode]:
        return []

    def list_objects(self, database: str, schema: str, object_type: str) -> list[ObjectRef]:
        return []

    def get_table_details(self, database: str, schema: str, table: str) -> TableDetails:
        return TableDetails(name=table, schema=schema)

    def get_storage(self, database: str) -> StorageSummary:
        return StorageSummary(growth_note="Not available for this database engine yet.")

    def get_statistics(self, database: str) -> DbStatistics:
        return DbStatistics()

    def get_configuration(self, database: str) -> ConfigInfo:
        return ConfigInfo(items=[("Engine", self.connection.db_type.value)])

    def list_extensions(self, database: str) -> list[ExtensionInfo]:
        return []

    def get_maintenance(self, database: str) -> MaintenanceInfo:
        return MaintenanceInfo(autovacuum_status="—")

    def get_backup_info(self, database: str) -> BackupInfo:
        return BackupInfo(status="—")
