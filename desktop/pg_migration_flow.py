"""Button gates and connection selection for the Migration page."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Iterator

from backend.services.migration_framework.destination import (
    destination_copy_name,
    destination_identity_error,
    destination_name_error,
    server_probe_database,
)
from backend.services.migration_framework.registry import (
    MYSQL,
    POSTGRESQL,
    pair_capability,
    source_types,
    target_types_for,
    unsupported_pair_reason,
    servers_matching_type,
)
from backend.services.pg_migration.precheck import STRATEGY_REPLACE

SOURCE_TYPES = source_types()
TARGET_TYPES = target_types_for(POSTGRESQL)
METHOD_TYPED = "Enter server details"
METHOD_SAVED = "Use a saved server"
CONNECTION_METHODS = (METHOD_TYPED, METHOD_SAVED)
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 100000

BACKUP_ACK_TEXT = (
    "I accept that restore readiness is not proven. "
    "This acknowledgement does not prove that a backup exists or can be restored."
)
REPLACE_CONFIRM_TEXT = "I confirm replacing existing tables on the connected target database."
PLAN_CONFIRM_TEXT = "I confirm this source, target, and the current preview plan."


def _db_type_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def live_session_matches_server(live: Any, server: Any) -> bool:
    """True when the Migration server row is the desktop session already connected."""
    if live is None or server is None:
        return False
    if _db_type_value(live.db_type) != _db_type_value(server.db_type):
        return False
    names = {name for name in (getattr(live, "saved_name", None), getattr(live, "name", None)) if name}
    if getattr(server, "name", None) in names:
        return True
    live_hosts = {
        host
        for host in (
            getattr(live, "host", None),
            getattr(live, "display_host", None),
            getattr(live, "endpoint_host", None),
        )
        if host
    }
    if getattr(server, "host", None) not in live_hosts:
        return False
    if getattr(live, "username", None) != getattr(server, "username", None):
        return False
    live_ports = {int(live.port), int(live.endpoint_port)}
    return int(server.port) in live_ports


def server_choice_label(server: Any) -> str:
    return f"{getattr(server, 'name', '')} · {getattr(server, 'db_type', '')}"


def servers_for_type(servers: list[Any], db_type: str) -> list[Any]:
    """Return saved connections whose stored db_type matches, not the display name."""
    return servers_matching_type(servers, db_type)


def saved_server_choices(servers: list[Any]) -> list[str]:
    labels = [server_choice_label(server) for server in servers]
    return labels or ["(none)"]


def find_saved_server(servers: list[Any], choice: str) -> Any | None:
    if not choice or choice == "(none)":
        return None
    for server in servers:
        if server_choice_label(server) == choice:
            return server
    return next((server for server in servers if getattr(server, "name", None) == choice), None)


def default_listen_port(db_type: str) -> int:
    return 3306 if str(db_type or "").strip().lower() == MYSQL else 5432


def secret_entry_show(visible: bool) -> str:
    """CTkEntry show= value. Port and revealed passwords use an empty mask."""
    return "" if visible else "*"


def typed_endpoint_error(
    host: str,
    port: str,
    username: str,
    *,
    ssh_enabled: bool = False,
    linux_username: str = "",
) -> str | None:
    if not (host or "").strip():
        return "Enter the server host."
    try:
        value = int(str(port).strip())
    except ValueError:
        return "Port must be a whole number."
    if value < 1 or value > 65535:
        return "Port must be between 1 and 65535."
    if not (username or "").strip():
        return "Enter the database username."
    if ssh_enabled and not (linux_username or "").strip():
        return "SSH is enabled. Enter the Linux username."
    return None


def manual_endpoint(
    *,
    db_type: str,
    host: str,
    port: str | int,
    username: str,
    password: str,
    ssh_enabled: bool = False,
    linux_username: str = "",
    linux_password: str = "",
    ssh_host: str = "",
    ssh_port: int = 22,
):
    """Build a one-off connection. It is not written to saved_servers.json."""
    from desktop.connection_store import SavedServer

    error = typed_endpoint_error(
        host,
        str(port),
        username,
        ssh_enabled=ssh_enabled,
        linux_username=linux_username,
    )
    if error:
        raise ValueError(error)
    host = host.strip()
    return SavedServer(
        name="typed",
        db_type=db_type,
        host=host,
        port=int(port),
        username=username.strip(),
        password=password,
        database="",
        ssh_enabled=bool(ssh_enabled),
        linux_username=linux_username.strip(),
        linux_password=linux_password,
        ssh_host=(ssh_host or "").strip() or host,
        ssh_port=int(ssh_port or 22),
    )


def describe_server_endpoint(server: Any) -> str:
    if server is None:
        return (
            "Enter the server host, port, username, and password, or choose a saved connection. "
            "Typed credentials are used for this migration only and are not saved."
        )
    extra = " via SSH" if getattr(server, "ssh_enabled", False) else ""
    typed = "Typed connection. " if getattr(server, "name", "") == "typed" else ""
    default = getattr(server, "database", "") or ""
    default_text = f" Saved default database: {default}." if default else ""
    return (
        f"{typed}Type {server.db_type}. Host {server.host}:{int(server.port)}{extra}.{default_text} "
        "The destination copy name is created on this server if it is missing."
    )


def migration_connection_result(server: Any, database: str, live: Any = None, *, allow_empty: bool = False):
    """Build the connection used by Test Source / Validate / Start.

    When the selected saved server is the current desktop session, reuse that
    session so SSH tunnel bind addresses and ssl_mode stay in effect.
    An empty database is allowed only for a target server probe.
    """
    if not database and not allow_empty:
        raise ValueError("Enter the database name.")
    if live_session_matches_server(live, server):
        return replace(live, database=database)
    return replace(server.to_connection_result(), database=database)


@contextmanager
def open_migration_endpoint(server: Any, database: str, live: Any = None, *, allow_empty: bool = False) -> Iterator[Any]:
    """Yield the connection used for tests and migration, including SSH when required."""
    result = migration_connection_result(server, database, live, allow_empty=allow_empty)
    if live_session_matches_server(live, server):
        yield result
        return
    if getattr(result, "ssh_enabled", False) and getattr(result, "linux_username", ""):
        from desktop.ssh_tunnel import ssh_tunnel_session

        with ssh_tunnel_session(result) as effective:
            yield effective
        return
    yield result


def unsupported_migration_reason(source_type: str, target_type: str) -> str | None:
    """Return a user-facing block for pairs that are not in the implemented registry."""
    return unsupported_pair_reason(source_type, target_type)


def pair_summary(source_type: str, target_type: str) -> str:
    capability = pair_capability(source_type, target_type)
    if capability is None:
        return unsupported_pair_reason(source_type, target_type) or "This pair is not implemented."
    return capability.summary()


@dataclass(frozen=True)
class MigrationFormState:
    source_type: str
    source_server: str
    source_database: str
    target_type: str
    target_server: str
    target_database: str
    scope: str
    strategy: str
    tables: str
    batch_size: str
    timeout_ms: str
    source_method: str = METHOD_SAVED
    target_method: str = METHOD_TYPED
    source_endpoint: str = ""
    target_endpoint: str = ""

    def source_key(self) -> tuple:
        return (self.source_type, self.source_method, self.source_server, self.source_endpoint, self.source_database.strip())

    def target_key(self) -> tuple:
        return (self.target_type, self.target_method, self.target_server, self.target_endpoint, self.target_database.strip())

    def plan_key(self) -> tuple:
        return (
            self.source_key(),
            self.target_key(),
            self.scope,
            self.strategy,
            self.tables.strip(),
            self.batch_size.strip(),
            self.timeout_ms.strip(),
        )


def parse_batch_size(raw: str) -> tuple[int | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, "Enter a batch size of at least 1."
    try:
        value = int(text)
    except ValueError:
        return None, "Batch size must be a whole number."
    if value < MIN_BATCH_SIZE or value > MAX_BATCH_SIZE:
        return None, f"Batch size must be between {MIN_BATCH_SIZE} and {MAX_BATCH_SIZE}."
    return value, None


def parse_timeout_ms(raw: str) -> tuple[int | None, str | None]:
    text = (raw or "").strip() or "0"
    try:
        value = int(text)
    except ValueError:
        return None, "Statement timeout must be a whole number of milliseconds."
    if value < 0:
        return None, "Statement timeout cannot be negative."
    return value, None


def same_instance_reason(source: Any, target: Any) -> str | None:
    """Reject source and target that resolve to the same engine instance and database.

    Saved connection names are not used. Two names can point at one database,
    and one name does not prove two instances are different.
    """
    if source is None or target is None:
        return None
    if _db_type_value(source.db_type) != _db_type_value(target.db_type):
        return None
    source_id = (
        str(source.endpoint_host).casefold(),
        int(source.endpoint_port),
        str(source.database),
    )
    target_id = (
        str(target.endpoint_host).casefold(),
        int(target.endpoint_port),
        str(target.database),
    )
    if source_id != target_id:
        return None
    return (
        "Source and target are the same database instance "
        f"({source.endpoint_host}:{source.endpoint_port}/{source.database}). "
        "The migration was not started."
    )


def target_identity_mismatch(expected_database: str, observed_database: str) -> str | None:
    expected = (expected_database or "").strip()
    observed = (observed_database or "").strip()
    if not expected or not observed:
        return None
    if expected == observed:
        return None
    return (
        f"The connected target database is {observed}, not {expected}. "
        "The migration was not started."
    )


def describe_verified_target(
    db_type: str,
    host: str,
    port: int,
    database: str,
    version: str = "",
) -> str:
    text = f"{db_type} {host}:{port} / {database}"
    if version:
        return f"{text} ({version})"
    return text


def replace_requires_confirmation(strategy: str, conflicts: list[str] | None) -> bool:
    return strategy == STRATEGY_REPLACE and bool(conflicts)


def outcome_counts(outcomes: list[Any]) -> dict[str, int]:
    mapping = {
        "schema_created": "created",
        "data_copied": "copied",
        "skipped": "skipped",
        "failed": "failed",
        "dropped": "replaced",
        "replaced": "replaced",
    }
    counts = {"created": 0, "copied": 0, "skipped": 0, "failed": 0, "replaced": 0}
    for item in outcomes:
        key = mapping.get(getattr(item, "action", ""))
        if key:
            counts[key] += 1
    return counts


def start_block_reason(
    *,
    source_type: str,
    target_type: str,
    precheck_ready: bool,
    plan_current: bool,
    confirmed: bool,
    busy: bool,
    source_tested: bool = True,
    target_tested: bool = True,
    configuration_current: bool = True,
    same_instance: bool = False,
    replace_needs_confirm: bool = False,
    replace_confirmed: bool = False,
    settings_error: str | None = None,
    identity_error: str | None = None,
) -> str | None:
    if busy:
        return "A migration operation is already running on this page."
    unsupported = unsupported_migration_reason(source_type, target_type)
    if unsupported:
        return unsupported
    if settings_error:
        return settings_error
    if same_instance:
        return "Source and target are the same database instance. The migration was not started."
    if identity_error:
        return identity_error
    if not configuration_current:
        return (
            "Source or target settings changed after the last successful test or preview. "
            "Test both sides and preview the current plan again."
        )
    if not source_tested:
        return "Test the source connection before validating or starting."
    if not target_tested:
        return "Test the target connection before validating or starting."
    if not precheck_ready:
        return "Validate the migration before previewing or starting it."
    if not plan_current:
        return "Preview the migration plan and confirm it before starting."
    if replace_needs_confirm and not replace_confirmed:
        return "Replacing existing target tables requires explicit confirmation. Nothing was dropped."
    if not confirmed:
        return "Confirm the source, target, and plan before starting."
    return None
