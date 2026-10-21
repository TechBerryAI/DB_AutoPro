"""Shared migration registry and discovery. Engines stay in their own packages."""

from backend.services.migration_framework.destination import (
    create_mysql_destination,
    destination_copy_name,
    destination_identity_error,
    destination_name_error,
    server_probe_database,
)
from backend.services.migration_framework.discovery import list_accessible_databases
from backend.services.migration_framework.registry import (
    PairCapability,
    implemented_pairs,
    pair_capability,
    source_types,
    target_types_for,
    unsupported_pair_reason,
)

__all__ = [
    "PairCapability",
    "create_mysql_destination",
    "destination_copy_name",
    "destination_identity_error",
    "destination_name_error",
    "implemented_pairs",
    "list_accessible_databases",
    "pair_capability",
    "server_probe_database",
    "source_types",
    "target_types_for",
    "unsupported_pair_reason",
]
