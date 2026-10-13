"""Public read-only environment discovery for MySQL on Ubuntu."""

from __future__ import annotations

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import UbuntuMysqlAptAdapter
from backend.services.mysql_patch.command_runner import CommandRunner
from backend.services.mysql_patch.errors import MysqlPatchSafetyError
from backend.services.mysql_patch.models import EnvironmentReport


class EnvironmentDiscoveryService:
    """Runs the Ubuntu APT adapter. Does not install packages or restart services."""

    def __init__(self, adapter: UbuntuMysqlAptAdapter | None = None) -> None:
        self._adapter = adapter or UbuntuMysqlAptAdapter()

    def discover(self, runner: CommandRunner, *, server_id: str) -> EnvironmentReport:
        """Discover one registered server. `server_id` is a label, not a shell argument."""
        cleaned = server_id.strip()
        if not cleaned:
            raise MysqlPatchSafetyError("Server id is required")
        return self._adapter.discover(runner, server_id=cleaned)
