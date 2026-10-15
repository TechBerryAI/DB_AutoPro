"""SSH command runner restricted to an allowlist.

The only install-shaped commands permitted are `apt-get -s` for one Ubuntu
MySQL package and one exact `dnf upgrade --assumeno` for the CentOS MySQL
package set. Neither command installs packages.
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
CMD_DF_DNF = "df -P -B1 / /var /var/cache/dnf"
CMD_DNF = "command -v dnf"
CMD_RPM_SERVER = (
    "rpm -q --qf '%{NAME}\\t%{NEVRA}\\t%{EVR}\\n' "
    "mysql8.4 mysql8.4-common mysql8.4-errmsg mysql8.4-server"
)
CMD_RPM_RELATED = CMD_RPM_SERVER
CMD_DNF_REPOQUERY = (
    "dnf --cacheonly --assumeno repoquery --latest-limit=1 "
    "--qf '%{name} %{evr}\\n' mysql8.4-server"
)
CMD_DNF_REPOINFO = "dnf --cacheonly --assumeno repoinfo --enabled"
CMD_DNF_HISTORY_LIST = (
    "sudo -n dnf --cacheonly --assumeno history list "
    "--contains-pkgs=mysql8.4,mysql8.4-common,mysql8.4-errmsg,mysql8.4-server"
)
CMD_DNF_CHECK = "dnf --cacheonly --assumeno check"
CMD_MACHINE_ID = "cat /etc/machine-id"
CMD_DNF_LOCKS = (
    "sh -c 'for f in /var/run/dnf.pid /var/lib/rpm/.rpm.lock /var/cache/dnf; do "
    "if command -v fuser >/dev/null 2>&1; then "
    "if fuser \"$f\" >/dev/null 2>&1; then echo \"held $f\"; else echo \"free $f\"; fi; "
    "else echo \"unknown $f\"; fi; done'"
)
CMD_DNF_UPGRADE_ASSUMENO = (
    "dnf upgrade --assumeno --cacheonly mysql8.4 mysql8.4-common mysql8.4-errmsg mysql8.4-server"
)
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
    "dnf upgrade -y",
    "dnf update -y",
    "dnf install",
    "dnf reinstall",
    "dnf remove",
    "dnf erase",
    "yum install",
    "yum update",
    "rpm -U",
    "rpm -i",
    "rpm --upgrade",
)


_VERSION = re.compile(r"^[0-9][A-Za-z0-9.+:~-]*$")
_SIMULATE = re.compile(
    r"^apt-get -s --no-install-recommends install "
    r"(mysql-server|mysql-server-8\.0|mysql-server-8\.4)="
    r"([0-9][A-Za-z0-9.+:~-]*)$"
)
_DNF_SIMULATE = re.compile(re.escape(CMD_DNF_UPGRADE_ASSUMENO))
_DNF_HISTORY_INFO = re.compile(
    r"^sudo -n dnf --cacheonly --assumeno history info [1-9][0-9]{0,8}$"
)


def apt_policy_command(package: str) -> str:
    """Build an apt-cache policy command for an allowlisted package name."""
    if package not in POLICY_PACKAGES:
        raise MysqlPatchSafetyError(f"Package is not allowlisted for policy lookup: {package}")
    return f"apt-cache policy -- {package}"


def dnf_history_info_command(transaction_id: int) -> str:
    """Read one numeric DNF transaction. The id is not a shell expression."""
    if isinstance(transaction_id, bool) or not isinstance(transaction_id, int):
        raise MysqlPatchSafetyError("DNF history transaction id is not valid")
    if transaction_id < 1 or transaction_id > 999_999_999:
        raise MysqlPatchSafetyError("DNF history transaction id is not valid")
    command = f"sudo -n dnf --cacheonly --assumeno history info {transaction_id}"
    if _DNF_HISTORY_INFO.fullmatch(command) is None:
        raise MysqlPatchSafetyError("DNF history command failed its own safety check")
    return command


def dnf_upgrade_assumeno_command() -> str:
    """Return the only DNF simulation. The package set cannot be widened."""
    command = CMD_DNF_UPGRADE_ASSUMENO
    if _DNF_SIMULATE.fullmatch(command) is None or "-y" in command or "--assumeyes" in command:
        raise MysqlPatchSafetyError("DNF simulation command failed its own safety check")
    return command


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
    if _DNF_HISTORY_INFO.fullmatch(command) is not None:
        return not any(marker in command.lower() for marker in MUTATING_MARKERS)
    if _DNF_SIMULATE.fullmatch(command) is not None:
        lowered = command.lower()
        if "-y" in lowered or "--assumeyes" in lowered or "--assumeno" not in lowered:
            return False
        return not any(marker in lowered for marker in MUTATING_MARKERS)
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
            CMD_DF_DNF,
            CMD_DNF,
            CMD_RPM_SERVER,
            CMD_RPM_RELATED,
            CMD_DNF_REPOQUERY,
            CMD_DNF_REPOINFO,
            CMD_DNF_HISTORY_LIST,
            CMD_DNF_CHECK,
            CMD_MACHINE_ID,
            CMD_DNF_LOCKS,
            CMD_DNF_UPGRADE_ASSUMENO,
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
