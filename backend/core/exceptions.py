"""Custom exception hierarchy for Database Automation Pro."""

from __future__ import annotations


class DatabaseAutomationError(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(DatabaseAutomationError):
    """Raised when configuration is invalid or missing."""


class ConnectorError(DatabaseAutomationError):
    """Raised when database connector operations fail."""


class BackupError(DatabaseAutomationError):
    """Raised when backup operations fail."""


class RestoreError(DatabaseAutomationError):
    """Raised when restore operations fail."""


class PatchError(DatabaseAutomationError):
    """Raised when patch/SQL execution fails."""


class MigrationError(DatabaseAutomationError):
    """Raised when migration operations fail."""


class FDWError(DatabaseAutomationError):
    """Raised when FDW management operations fail."""


class ValidationError(DatabaseAutomationError):
    """Raised when validation checks fail."""


class AuthenticationError(DatabaseAutomationError):
    """Raised when authentication fails."""


class AuthorizationError(DatabaseAutomationError):
    """Raised when user lacks required permissions."""
