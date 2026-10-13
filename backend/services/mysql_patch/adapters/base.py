"""Read-only discovery adapter contract."""

from __future__ import annotations

from typing import Protocol

from backend.services.mysql_patch.command_runner import CommandRunner
from backend.services.mysql_patch.models import EnvironmentReport


class ReadOnlyDiscoveryAdapter(Protocol):
    """Identifies the server environment without changing it."""

    def discover(self, runner: CommandRunner, *, server_id: str) -> EnvironmentReport:
        """Return a fail-closed environment report."""
