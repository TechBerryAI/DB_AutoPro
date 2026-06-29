"""Active connection helpers for the desktop application."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr

from backend.connectors.base import ConnectionInfo, get_connector, get_connector_from_info
from backend.core.config import DatabaseConnectionConfig, get_settings
from backend.models.schemas import DatabaseType

DB_DEFAULTS: dict[DatabaseType, dict[str, str | int]] = {
    DatabaseType.POSTGRESQL: {"port": 5432, "username": "postgres", "database": "postgres"},
    DatabaseType.MYSQL: {"port": 3306, "username": "root", "database": "mysql"},
    DatabaseType.MONGODB: {"port": 27017, "username": "", "database": "admin"},
    DatabaseType.MSSQL: {"port": 1433, "username": "sa", "database": "master"},
    DatabaseType.ORACLE: {"port": 1521, "username": "system", "database": "ORCL"},
}


@dataclass
class ConnectionResult:
    """Connection details accepted by the startup dialog."""

    name: str
    db_type: DatabaseType
    host: str
    port: int
    username: str
    password: str
    database: str
    saved_name: str | None = None
    auth_database: str | None = None
    ssh_enabled: bool = False
    linux_username: str = ""
    linux_password: str = ""
    ssh_host: str | None = None
    ssh_port: int = 22
    mongodump_path: str = ""
    remote_backup_dir: str = ""
    display_host: str | None = None
    display_port: int | None = None

    @property
    def summary(self) -> str:
        return (
            f"{self.name}  |  {self.db_type.value}  |  "
            f"{self.endpoint_label}  |  {self.database}"
        )

    @property
    def endpoint_label(self) -> str:
        return f"{self.endpoint_host}:{self.endpoint_port}"

    @property
    def endpoint_host(self) -> str:
        return self.display_host or self.host

    @property
    def endpoint_port(self) -> int:
        return self.display_port if self.display_port is not None else self.port

    def _extra_options(self) -> dict:
        remote_host = self.endpoint_host
        remote_port = self.endpoint_port
        opts: dict = {
            "connect_timeout": 10,
            "mongo_host": remote_host,
            "mongo_port": remote_port,
            "db_host": remote_host,
            "db_port": remote_port,
        }
        if self.db_type == DatabaseType.MONGODB and self.auth_database:
            opts["auth_source"] = self.auth_database
        if self.ssh_enabled:
            opts.update({
                "ssh_enabled": True,
                "ssh_host": self.ssh_host or self.host,
                "ssh_port": self.ssh_port,
                "linux_username": self.linux_username,
                "linux_password": self.linux_password,
            })
        if self.mongodump_path:
            opts["mongodump_path"] = self.mongodump_path
        if self.remote_backup_dir:
            opts["remote_backup_dir"] = self.remote_backup_dir
        return opts

    def to_connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            ssl_mode="prefer",
            extra_options=self._extra_options(),
        )


def apply_connection(connection: ConnectionResult, profile: str = "default") -> None:
    """Apply runtime connection settings used by backend services."""
    settings = get_settings()
    settings.database_type = connection.db_type.value
    settings.connections[profile] = DatabaseConnectionConfig(
        host=connection.host,
        port=connection.port,
        database=connection.database,
        username=connection.username,
        password=SecretStr(connection.password),
        ssl_mode="prefer",
        extra_options=connection._extra_options(),
    )


def _friendly_error(exc: Exception, connection: ConnectionResult | None = None) -> str:
    import re

    message = str(exc).strip()
    if "pg_hba.conf" in message:
        client_match = re.search(r'host "([^"]+)"', message)
        client_ip = client_match.group(1) if client_match else "YOUR_PC_IP"
        server_host = connection.host if connection else "DATABASE_SERVER"
        database = connection.database if connection else "emp"
        username = connection.username if connection else "postgres"
        return (
            f"{message}\n\n"
            "This is a PostgreSQL server access rule issue — your password may be correct, "
            "but the server is not allowing your PC to connect.\n"
            f"On the database server ({server_host}), add a pg_hba.conf entry and reload PostgreSQL:\n"
            f"  host    {database}    {username}    {client_ip}/32    scram-sha-256\n"
            "Then run: sudo systemctl reload postgresql"
        )
    if "Connection refused" in message:
        return (
            f"{message}\n\n"
            "Check that PostgreSQL is running on the server, port 5432 is open in the firewall, "
            "and listen_addresses in postgresql.conf allows remote connections."
        )
    if "authentication failed" in message.lower() or "authenticationfailed" in message.lower():
        if connection and connection.db_type == DatabaseType.MONGODB:
            return (
                f"{message}\n\n"
                "MongoDB reached the server but rejected the login. Common fixes:\n"
                "• Set Auth database to where the user was created (usually 'admin')\n"
                "• Verify username and password on the MongoDB server\n"
                "• Confirm the user has access to the target database"
            )
        return f"{message}\n\nVerify the username and password."
    if "does not exist" in message.lower() and "database" in message.lower():
        return f"{message}\n\nVerify the database name exists on the server."
    return message


def test_connection(connection: ConnectionResult) -> tuple[bool, str]:
    """Test connectivity and return (success, message)."""
    from desktop.ssh_tunnel import ssh_tunnel_session

    try:
        with ssh_tunnel_session(connection) as effective:
            apply_connection(effective)
            info = effective.to_connection_info()
            connector = get_connector_from_info(effective.db_type, info)
            health = connector.health_check()
            if health.get("status") == "healthy":
                version = health.get("version", "unknown")
                latency = health.get("latency_ms", 0)
                via = " via SSH" if connection.ssh_enabled else ""
                return True, f"Connected successfully{via} ({version}, {latency:.0f} ms)"
            error = health.get("error", "Connection failed")
            return False, _friendly_error(Exception(error), connection)
    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, _friendly_error(exc, connection)
