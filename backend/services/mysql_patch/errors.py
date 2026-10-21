"""Errors for MySQL package-patch discovery and later patch stages."""

from __future__ import annotations


class MysqlPatchError(Exception):
    """Base error for the MySQL package-patch workflow."""


class MysqlPatchSafetyError(MysqlPatchError):
    """Raised when a call would break a safety rule before any remote command."""


class OutputLimitError(MysqlPatchSafetyError):
    """A remote command returned more output than this check will accept."""

    def __init__(self, *, category: str, stream: str) -> None:
        self.category = category
        self.stream = stream
        super().__init__(f"{category} {stream} exceeded the size limit")
