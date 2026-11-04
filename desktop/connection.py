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


def coerce_db_type_for_port(declared: DatabaseType, port: int) -> DatabaseType:
    """Fix common engine-tile mistakes when the port clearly belongs to another engine.

    Historically the Servers form showed an Oracle hint for every non-Mongo engine,
    so Connect often ran the PostgreSQL driver against Oracle port 1521.
    """
    if declared == DatabaseType.POSTGRESQL and port == 1521:
        return DatabaseType.ORACLE
    if declared == DatabaseType.ORACLE and port == 5432:
        return DatabaseType.POSTGRESQL
    if declared == DatabaseType.POSTGRESQL and port == 3306:
        return DatabaseType.MYSQL
    if declared == DatabaseType.POSTGRESQL and port in {27017, 27018}:
        return DatabaseType.MONGODB
    if declared == DatabaseType.POSTGRESQL and port == 1433:
        return DatabaseType.MSSQL
    return declared


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
            "connect_timeout": 5,
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
                # Prefer the real Linux host, never the local tunnel bind address.
                "ssh_host": self.ssh_host or self.endpoint_host,
                "ssh_port": self.ssh_port,
                "linux_username": self.linux_username,
                "linux_password": self.linux_password,
            })
        if self.mongodump_path:
            opts["mongodump_path"] = self.mongodump_path
        if self.remote_backup_dir:
            opts["remote_backup_dir"] = self.remote_backup_dir
        return opts

    def _ssl_mode(self) -> str:
        """SSH tunnels already encrypt traffic; do not require Postgres SSL as well."""
        if self.ssh_enabled:
            return "disable"
        return "prefer"

    def to_connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            ssl_mode=self._ssl_mode(),
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
        ssl_mode=connection._ssl_mode(),
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
    if "ssl was required" in message.lower() or (
        "does not support ssl" in message.lower()
    ):
        return (
            f"{message}\n\n"
            "This PostgreSQL host is not using SSL. With an SSH tunnel the app should "
            "connect without Postgres SSL — retry Connect. For a direct (non-SSH) link, "
            "the server must either enable SSL or allow non-SSL clients."
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


def _reachable_saved_oracle_hint(failed_host: str) -> str:
    """If a saved Oracle host answers on the wire, mention it (no passwords)."""
    try:
        from desktop.connection_store import load_saved_servers
        from desktop.ssh_tunnel import probe_tcp
    except Exception:
        return ""

    hints: list[str] = []
    for server in load_saved_servers():
        if getattr(server, "db_type", None) != DatabaseType.ORACLE.value:
            continue
        host = (server.host or "").strip()
        if not host or host == failed_host:
            continue
        port = int(server.port or 1521)
        ssh_port = int(getattr(server, "ssh_port", None) or 22)
        db_ok = probe_tcp(host, port, timeout=2.0) is None
        ssh_ok = probe_tcp(host, ssh_port, timeout=2.0) is None
        if db_ok or ssh_ok:
            parts = []
            if ssh_ok:
                parts.append(f"SSH :{ssh_port}")
            if db_ok:
                parts.append(f"Oracle :{port}")
            hints.append(f"'{server.name}' -> {host} ({', '.join(parts)} open)")
    if not hints:
        return ""
    return (
        "\n\nA saved Oracle server is reachable from this PC - open it under "
        "Saved Connection instead of the unreachable host:\n  - "
        + "\n  - ".join(hints)
    )


def _has_linux_ssh_credentials(connection: ConnectionResult) -> bool:
    return bool((connection.linux_username or "").strip() and connection.linux_password)


def _enrich_ssh_from_saved(connection: ConnectionResult) -> ConnectionResult:
    """Fill missing Linux SSH fields from a saved profile with the same name."""
    if _has_linux_ssh_credentials(connection):
        from dataclasses import replace

        return replace(
            connection,
            ssh_host=connection.ssh_host or connection.host,
            ssh_port=connection.ssh_port or 22,
        )
    try:
        from desktop.connection_store import load_saved_servers
    except Exception:
        return connection

    lookup = (connection.saved_name or connection.name or "").strip().lower()
    if not lookup:
        return connection
    for server in load_saved_servers():
        if (server.name or "").strip().lower() != lookup:
            continue
        if not (server.linux_username and server.linux_password):
            return connection
        from dataclasses import replace

        return replace(
            connection,
            linux_username=server.linux_username,
            linux_password=server.linux_password,
            ssh_host=connection.ssh_host or server.ssh_host or connection.host,
            ssh_port=connection.ssh_port or server.ssh_port or 22,
        )
    return connection


def _is_credential_error(message: str) -> bool:
    """True when the DB rejected the login itself (SSH will not help)."""
    low = (message or "").lower()
    needles = (
        "password authentication failed",
        "authentication failed for",
        "ora-01017",
        "invalid username/password",
        "login failed for user",
        "ora-28000",
        "account is locked",
        "password has expired",
        "ora-28001",
    )
    return any(n in low for n in needles)


def _direct_access_likely_blocked(message: str) -> bool:
    """True when a direct DB login failed for host/network/firewall reasons SSH can bypass."""
    if _is_credential_error(message):
        return False
    low = (message or "").lower()
    needles = (
        "pg_hba.conf",
        "no pg_hba",
        "no encryption",
        "not allowed to connect",
        "is not allowed",
        "access denied for user",
        "connection timed out",
        "timed out",
        "connection refused",
        "could not connect",
        "can't connect",
        "cannot connect",
        "no route to host",
        "network is unreachable",
        "server closed the connection",
        "forcibly closed",
        "dpy-4011",
        "dpy-6005",
        "ora-12541",
        "ora-12170",
        "ora-12543",
        "ora-12535",
        "login timeout",
        "name or service not known",
        "getaddrinfo failed",
        "ssl was required",
        "does not support ssl",
        "oracle port closed",
        "start the listener",
        "lsnrctl",
        "no listener",
        "enable ssh tunnel",
        "firewalled",
        "10054",
        "10053",
        "10061",
        "odbc driver",
        "adaptive server is unavailable",
        "server is not found",
        "unable to connect",
    )
    return any(n in low for n in needles)


def _adopt_working_path(target: ConnectionResult, working: ConnectionResult) -> None:
    """Copy the path that succeeded onto the caller's connection object."""
    target.ssh_enabled = working.ssh_enabled
    target.linux_username = working.linux_username
    target.linux_password = working.linux_password
    target.ssh_host = working.ssh_host
    target.ssh_port = working.ssh_port
    # Keep typed DB host; open_ssh_tunnel rewrites the bind for the live session.


def test_connection(connection: ConnectionResult) -> tuple[bool, str]:
    """Test connectivity for any engine with automatic path recovery.

    Strategy (fresh-user resilient Connect):
      1. Honor the form (direct or SSH).
      2. If that fails and the other path is available, try it.
      3. For Oracle, auto-start the TNS listener over SSH when it is down.
      4. On success, mutate ``connection`` so the live session uses the working path.
    """
    from dataclasses import replace

    from backend.services.ssh_host_keys import SshHostKeyError
    from desktop.oracle_listener import listener_appears_down, try_start_oracle_listener
    from desktop.ssh_tunnel import ssh_tunnel_session

    enriched = _enrich_ssh_from_saved(connection)
    has_ssh = _has_linux_ssh_credentials(enriched)

    def _with_ssh(base: ConnectionResult, enabled: bool) -> ConnectionResult:
        return replace(
            base,
            ssh_enabled=enabled,
            ssh_host=(base.ssh_host or base.host) if enabled else base.ssh_host,
            ssh_port=(base.ssh_port or 22) if enabled else base.ssh_port,
            linux_username=base.linux_username,
            linux_password=base.linux_password,
        )

    def _probe_db(conn: ConnectionResult) -> tuple[bool, str]:
        with ssh_tunnel_session(conn) as effective:
            apply_connection(effective)
            info = effective.to_connection_info()
            connector = get_connector_from_info(effective.db_type, info)
            health = connector.health_check()
            if health.get("status") == "healthy":
                version = health.get("version", "unknown")
                latency = health.get("latency_ms", 0)
                via = " via SSH" if conn.ssh_enabled else ""
                return True, f"Connected successfully{via} ({version}, {latency:.0f} ms)"
            error = health.get("error", "Connection failed")
            return False, _friendly_error(Exception(error), conn)

    def _safe_probe(conn: ConnectionResult) -> tuple[bool, str]:
        try:
            return _probe_db(conn)
        except SshHostKeyError:
            raise
        except Exception as exc:
            return False, _friendly_error(exc, conn)

    # Preferred order: what the user asked for first, then the alternate path.
    attempts: list[ConnectionResult] = []
    prefer_ssh = bool(connection.ssh_enabled and has_ssh)
    if prefer_ssh:
        attempts.append(_with_ssh(enriched, True))
        attempts.append(_with_ssh(enriched, False))
    else:
        attempts.append(_with_ssh(enriched, False))
        if has_ssh:
            attempts.append(_with_ssh(enriched, True))

    last_error = "Connection failed"
    oracle_listener_tried = False

    def _try_oracle_listener_recovery() -> tuple[bool, str] | None:
        nonlocal oracle_listener_tried, last_error
        if oracle_listener_tried or not has_ssh:
            return None
        if connection.db_type != DatabaseType.ORACLE:
            return None
        if not listener_appears_down(last_error):
            return None
        oracle_listener_tried = True
        if not try_start_oracle_listener(enriched):
            return None
        for retry_ssh in (True, False):
            retry = _with_ssh(enriched, retry_ssh)
            ok2, msg2 = _safe_probe(retry)
            if ok2:
                _adopt_working_path(connection, retry)
                return True, msg2
            last_error = msg2
        return None

    for candidate in attempts:
        ok, message = _safe_probe(candidate)
        if ok:
            _adopt_working_path(connection, candidate)
            return True, message
        last_error = message

        recovered = _try_oracle_listener_recovery()
        if recovered is not None:
            return recovered

        # Wrong DB password / locked account — other network paths will not help.
        if _is_credential_error(message):
            break

    recovered = _try_oracle_listener_recovery()
    if recovered is not None:
        return recovered

    if connection.db_type == DatabaseType.ORACLE:
        last_error = f"{last_error}{_reachable_saved_oracle_hint(connection.host)}"
    return False, last_error
