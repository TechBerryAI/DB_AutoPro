"""Confirmation checks for the PostgreSQL-to-MySQL API. No connection is opened here."""

from __future__ import annotations

from backend.models.schemas import DatabaseType
from backend.services.pg_migration.precheck import SCOPE_SCHEMA, SCOPES, STRATEGY_REPLACE, STRATEGY_FAIL, STRATEGY_SKIP


def pg_mysql_request_block(request, *, execute: bool) -> str | None:
    """Return a rejection before any connector is created."""
    if request.source_type != DatabaseType.POSTGRESQL or request.target_type != DatabaseType.MYSQL:
        return None
    if not request.source_database.strip() or not request.target_database.strip():
        return (
            "postgresql-to-mysql migration requires the source database name and the target database name. "
            "No schema or data was copied."
        )
    if request.scope not in SCOPES:
        return "postgresql-to-mysql migration requires a scope. No schema or data was copied."
    if request.strategy not in {STRATEGY_SKIP, STRATEGY_FAIL, STRATEGY_REPLACE}:
        return "postgresql-to-mysql migration requires a conflict policy. No schema or data was copied."
    if request.batch_size < 1 or request.statement_timeout_ms < 0:
        return "Batch size must be at least 1 and the statement timeout cannot be negative. No schema or data was copied."
    if execute and request.strategy == STRATEGY_REPLACE and not request.confirm_replace:
        return "Replacing existing MySQL tables requires explicit confirmation. Nothing was dropped."
    if execute and request.scope != SCOPE_SCHEMA and not request.accept_unproven_backup:
        return "Restore has not been proven. Confirm that you accept this before copying data. No schema or data was copied."
    if execute and not request.confirm_plan:
        return "Confirm the source, target, and plan before starting. No schema or data was copied."
    return None
