"""Errors for MySQL package-patch discovery and later patch stages."""

from __future__ import annotations


class MysqlPatchError(Exception):
    """Base error for the MySQL package-patch workflow."""


class MysqlPatchSafetyError(MysqlPatchError):
    """Raised when a call would break a safety rule before any remote command."""
