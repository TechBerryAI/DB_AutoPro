"""SSH command runner restricted to an allowlist.

The only install-shaped command permitted is `apt-get -s` for one MySQL
server package pinned to an exact version. It is a simulation and is not
`apt-get install` without `-s`.
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel

from backend.services.mysql_patch.errors import MysqlPatchSafetyError

MAX_OUTPUT_CHARS = 64_000

CMD_OS_RELEASE = "cat /etc/os-release"
CMD_APT_GET = "command -v apt-get"
CMD_DPKG = (
    "dpkg-query -W -f '${Package}\\t${Version}\\t${db:Status-Status}\\n' "
    "mysql-server mysql-server-8.0 mysql-server-8.4 mysql-community-server"
)
CMD_SERVICE_MYSQL = "systemctl is-active mysql"
CMD_SERVICE_MYSQLD = "systemctl is-active mysqld"
CMD_ID = "id -u"
CMD_SUDO_CHECK = "sudo -n true"
CMD_DF = "df -P -B1 / /var /var/cache/apt"
CMD_DPKG_AUDIT = "dpkg --audit"
CMD_LOCKS = (
    "sh -c 'for f in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock "
    "/var/lib/apt/lists/lock /var/cache/apt/archives/lock; do "
    "if command -v fuser >/dev/null 2>&1; then "
    "if fuser \"$f\" >/dev/null 2>&1; then echo \"held $f\"; else echo \"free $f\"; fi; "
    "else echo \"unknown $f\"; fi; done'"
)

POLICY_PACKAGES = frozenset({"mysql-server", "mysql-server-8.0", "mysql-server-8.4"})

MUTATING_MARKERS = (
    "apt-get install",
    "apt-get upgrade",
    "apt-get dist-upgrade",
    "apt-get full-upgrade",
    "apt install",
    "apt upgrade",
    "apt full-upgrade",
    "apt dist-upgrade",
    "systemctl restart",
    "systemctl stop",
    "systemctl start",
    "reboot",
    "shutdown",
)


_VERSION = re.compile(r"^[0-9][A-Za-z0-9.+:~-]*$")
_SIMULATE = re.compile(
    r"^apt-get -s --no-install-recommends install "
    r"(mysql-server|mysql-server-8\.0|mysql-server-8\.4)="
    r"([0-9][A-Za-z0-9.+:~-]*)$"
)


def apt_policy_command(package: str) -> str:
    """Build an apt-cache policy command for an allowlisted package name."""
    if package not in POLICY_PACKAGES:
        raise MysqlPatchSafetyError(f"Package is not allowlisted for policy lookup: {package}")
    return f"apt-cache policy -- {package}"


def apt_simulate_command(package: str, version: str) -> str:
    """Build a no-act install preview for one allowlisted MySQL server package."""
    if package not in POLICY_PACKAGES:
        raise MysqlPatchSafetyError(f"Package is not allowlisted for simulation: {package}")
    if not version or _VERSION.fullmatch(version) is None:
        raise MysqlPatchSafetyError("Package version is not safe to place in a command")
    command = f"apt-get -s --no-install-recommends install {package}={version}"
    if _SIMULATE.fullmatch(command) is None:
        raise MysqlPatchSafetyError("Simulation command failed its own safety check")
    return command


def is_allowed_command(command: str) -> bool:
    """Return True for a fixed read-only command or one exact simulation."""
    if command in allowed_commands():
        return True
    if any(token in command for token in ("\n", "\r", ";", "&&", "|", "`", "$")):
        return False
    if _SIMULATE.fullmatch(command) is None:
        return False
    lowered = command.lower()
    return not any(marker in lowered for marker in MUTATING_MARKERS)


def allowed_commands() -> frozenset[str]:
    policy = {apt_policy_command(name) for name in POLICY_PACKAGES}
    return frozenset(
        {
            CMD_OS_RELEASE,
            CMD_APT_GET,
            CMD_DPKG,
            CMD_SERVICE_MYSQL,
            CMD_SERVICE_MYSQLD,
            CMD_ID,
            CMD_SUDO_CHECK,
            CMD_DF,
            CMD_DPKG_AUDIT,
            CMD_LOCKS,
            *policy,
        }
    )


class CommandResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Runs one allowlisted remote command."""

    def run(self, command: str, *, timeout: int = 20) -> CommandResult:
        """Return the remote exit code and captured output."""


class SshCommandRunner:
    """Calls the existing Paramiko helper. Refuses commands outside the allowlist."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        fallback_host: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self.fallback_host = fallback_host or host
        self.host_key_verified = False

    def __repr__(self) -> str:
        return (
            f"SshCommandRunner(host={self.host!r}, port={self.port}, username={self.username!r})"
        )

    def run(self, command: str, *, timeout: int = 20) -> CommandResult:
        if not is_allowed_command(command):
            raise MysqlPatchSafetyError("Remote command is not allowlisted")
        lowered = command.lower()
        if any(marker in lowered for marker in MUTATING_MARKERS):
            raise MysqlPatchSafetyError("Remote command is not read-only")

        from backend.services.ssh_client import run_ssh_command

        code, stdout, stderr = run_ssh_command(
            ssh_host=self.host,
            fallback_host=self.fallback_host,
            ssh_port=self.port,
            username=self.username,
            password=self._password,
            command=command,
            timeout=timeout,
        )
        if len(stdout) > MAX_OUTPUT_CHARS or len(stderr) > MAX_OUTPUT_CHARS:
            raise MysqlPatchSafetyError("Remote command output exceeded the size limit")
        self.host_key_verified = True
        return CommandResult(exit_code=code, stdout=stdout, stderr=stderr)
