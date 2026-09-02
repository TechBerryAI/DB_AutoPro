"""Oracle metadata provider — CDB/PDB containers with users/schemas."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterator

from backend.connectors.base import get_connector_from_info
from backend.connectors.oracle import OracleConnector
from desktop.connection import ConnectionResult
from desktop.explorer.oracle_inventory import (
    fetch_container_users_via_ssh,
    fetch_oracle_inventory_via_ssh,
    fetch_pdb_users_via_ssh,
)
from desktop.explorer.provider import (
    BackupInfo,
    ConfigInfo,
    DatabaseSummary,
    DbStatistics,
    ExtensionInfo,
    MaintenanceInfo,
    ObjectRef,
    SchemaNode,
    StorageSummary,
    TableDetails,
)


def _norm_status(value: Any) -> str:
    text = str(value or "ONLINE").strip()
    return text if text else "ONLINE"


def _open_mode_status(open_mode: Any) -> str:
    text = str(open_mode or "ONLINE").strip().upper().replace(" ", "_")
    if text in {"READ_WRITE", "OPEN", "MOUNTED"}:
        return text.lower()
    if text == "READ_ONLY":
        return "read_only"
    return text.lower() or "online"


def _user_nodes(rows: list[dict[str, Any]]) -> list[SchemaNode]:
    nodes: list[SchemaNode] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or row.get("username") or "").strip()
        if not name or name.upper() in seen:
            continue
        seen.add(name.upper())
        nodes.append(
            SchemaNode(
                name=name,
                status=_norm_status(row.get("status") or row.get("account_status")),
            )
        )
    nodes.sort(key=lambda n: n.name.upper())
    return nodes


class OracleExplorerProvider:
    """Load Oracle CDB/PDB containers and schema users for the Databases page."""

    supports_object_explorer = True

    def __init__(self, connection: ConnectionResult) -> None:
        self.connection = connection
        self._containers: list[DatabaseSummary] | None = None
        self._users_by_container: dict[str, list[SchemaNode]] = {}

    def _connector(self, *, database: str | None = None) -> OracleConnector:
        scoped = self.connection
        if database and database.strip():
            scoped = replace(self.connection, database=database.strip())
        info = scoped.to_connection_info()
        connector = get_connector_from_info(scoped.db_type, info)
        assert isinstance(connector, OracleConnector)
        return connector

    @contextmanager
    def _session(self) -> Iterator[Any]:
        connector = self._connector()
        conn = connector.open_session()
        try:
            yield conn
        finally:
            conn.close()

    def _pdb_name(self) -> str:
        return (self.connection.database or "ORCLPDB1").strip().upper() or "ORCLPDB1"

    def _cdb_service_candidates(self) -> list[str]:
        current = (self.connection.database or "").strip()
        candidates = ["ORCLCDB", "ORCL", "CDB$ROOT", "XE"]
        out: list[str] = []
        for name in candidates:
            if name.upper() == current.upper():
                continue
            if name not in out:
                out.append(name)
        return out

    def _query_users(
        self,
        conn: Any,
        *,
        container: str | None = None,
    ) -> list[SchemaNode]:
        setup: list[str] = []
        if container:
            setup.append(f"ALTER SESSION SET CONTAINER = {container}")
        for sql in (
            "SELECT username AS name, account_status AS status FROM dba_users ORDER BY username",
            "SELECT username AS name, 'OPEN' AS status FROM all_users ORDER BY username",
        ):
            try:
                rows = OracleConnector._fetch_dicts(conn, setup, sql)
                nodes = _user_nodes(rows)
                if nodes:
                    return nodes
            except Exception:
                continue
        return []

    def _query_containers_sql(self, conn: Any) -> list[DatabaseSummary]:
        for sql in (
            "SELECT name, open_mode, con_id FROM v$pdbs ORDER BY con_id",
            "SELECT name, open_mode, con_id FROM v$containers WHERE con_id > 0 ORDER BY con_id",
        ):
            try:
                rows = OracleConnector._fetch_dicts(conn, [], sql)
            except Exception:
                continue
            out: list[DatabaseSummary] = []
            for row in rows:
                name = str(row.get("name") or "").strip().upper()
                if not name:
                    continue
                open_mode = str(row.get("open_mode") or "ONLINE")
                con_id = int(row.get("con_id") or 0)
                out.append(
                    DatabaseSummary(
                        name=name,
                        size="—",
                        owner="container",
                        status=_open_mode_status(open_mode),
                        raw={"con_id": con_id, "open_mode": open_mode},
                    )
                )
            if out:
                return out
        return []

    def _load_local_users(self) -> list[SchemaNode]:
        try:
            with self._session() as conn:
                return self._query_users(conn)
        except Exception:
            return []

    def _load_via_ssh(self) -> tuple[list[DatabaseSummary], dict[str, list[SchemaNode]]] | None:
        try:
            containers_raw, users_raw = fetch_oracle_inventory_via_ssh(self.connection)
        except Exception:
            containers_raw, users_raw = [], {}

        containers: list[DatabaseSummary] = []
        for row in containers_raw:
            name = str(row.get("name") or "").strip().upper()
            if not name:
                continue
            open_mode = str(row.get("open_mode") or "ONLINE")
            containers.append(
                DatabaseSummary(
                    name=name,
                    size="—",
                    owner="container",
                    status=_open_mode_status(open_mode),
                    raw={"con_id": int(row.get("con_id") or 0), "open_mode": open_mode},
                )
            )
        users_by: dict[str, list[SchemaNode]] = {}
        for cname, rows in users_raw.items():
            users_by[cname.upper()] = _user_nodes(rows)

        # If bulk inventory failed, still pull each container individually over SSH.
        if not users_by.get("CDB$ROOT"):
            try:
                rows = fetch_container_users_via_ssh(self.connection, "CDB$ROOT")
                if rows:
                    users_by["CDB$ROOT"] = _user_nodes(rows)
            except Exception:
                pass
        if "PDB$SEED" not in users_by:
            try:
                rows = fetch_container_users_via_ssh(self.connection, "PDB$SEED")
                users_by["PDB$SEED"] = _user_nodes(rows) if rows else []
            except Exception:
                users_by["PDB$SEED"] = []

        pdb = self._pdb_name()
        if not users_by.get(pdb):
            try:
                rows = fetch_container_users_via_ssh(self.connection, pdb)
                if rows:
                    users_by[pdb] = _user_nodes(rows)
            except Exception:
                pass

        if not containers and not users_by:
            return None
        return containers, users_by

    def _load_via_cdb_service(self) -> tuple[list[DatabaseSummary], dict[str, list[SchemaNode]]] | None:
        for service in self._cdb_service_candidates():
            try:
                connector = self._connector(database=service)
                conn = connector.open_session()
            except Exception:
                continue
            try:
                containers = self._query_containers_sql(conn)
                if not containers:
                    continue
                users_by: dict[str, list[SchemaNode]] = {}
                for container in containers:
                    users_by[container.name.upper()] = self._query_users(
                        conn, container=container.name
                    )
                return containers, users_by
            except Exception:
                continue
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        return None

    def _load_via_session_switch(
        self,
    ) -> tuple[list[DatabaseSummary], dict[str, list[SchemaNode]]] | None:
        try:
            with self._session() as conn:
                # Common users can switch to CDB$ROOT.
                try:
                    OracleConnector._fetch_dicts(
                        conn, ["ALTER SESSION SET CONTAINER = CDB$ROOT"], "SELECT 1 AS ok FROM dual"
                    )
                except Exception:
                    return None
                containers = self._query_containers_sql(conn)
                if not containers:
                    return None
                users_by: dict[str, list[SchemaNode]] = {}
                for container in containers:
                    users_by[container.name.upper()] = self._query_users(
                        conn, container=container.name
                    )
                return containers, users_by
        except Exception:
            return None

    def _ensure_all_containers(
        self,
        containers: list[DatabaseSummary],
        users_by: dict[str, list[SchemaNode]],
        pdb_name: str,
        local_users: list[SchemaNode],
    ) -> tuple[list[DatabaseSummary], dict[str, list[SchemaNode]]]:
        by_name: dict[str, DatabaseSummary] = {c.name.upper(): c for c in containers}
        users_out: dict[str, list[SchemaNode]] = {
            key.upper(): list(value) for key, value in users_by.items()
        }

        def upsert(name: str, *, con_id: int, open_mode: str, status: str) -> None:
            key = name.upper()
            existing = by_name.get(key)
            if existing is None:
                by_name[key] = DatabaseSummary(
                    name=name,
                    size="—",
                    owner="container",
                    status=status,
                    raw={"con_id": con_id, "open_mode": open_mode},
                )
            users_out.setdefault(key, [])

        upsert("CDB$ROOT", con_id=1, open_mode="READ WRITE", status="read_write")
        upsert("PDB$SEED", con_id=2, open_mode="READ ONLY", status="read_only")
        upsert(pdb_name, con_id=3, open_mode="READ WRITE", status="read_write")

        if local_users:
            key = pdb_name.upper()
            existing = users_out.get(key) or []
            if len(local_users) >= len(existing):
                users_out[key] = list(local_users)

        ordered_names = ["CDB$ROOT", "PDB$SEED", pdb_name.upper()]
        for name in sorted(by_name.keys()):
            if name not in ordered_names:
                ordered_names.append(name)

        summaries: list[DatabaseSummary] = []
        for name in ordered_names:
            summary = by_name.get(name)
            if summary is None:
                continue
            count = len(users_out.get(name, []))
            summary.size = f"{count} user{'s' if count != 1 else ''}"
            summaries.append(summary)
        return summaries, users_out

    def _merge_users(
        self,
        target: dict[str, list[SchemaNode]],
        incoming: dict[str, list[SchemaNode]],
    ) -> None:
        """Keep the richer user list per container (never wipe a good list with empty)."""
        for key, users in incoming.items():
            name = key.upper()
            current = target.get(name) or []
            if len(users) > len(current):
                target[name] = list(users)
            elif name not in target:
                target[name] = list(users)

    def _merge_containers(
        self,
        current: list[DatabaseSummary],
        incoming: list[DatabaseSummary],
    ) -> list[DatabaseSummary]:
        by_name: dict[str, DatabaseSummary] = {c.name.upper(): c for c in current}
        for item in incoming:
            by_name[item.name.upper()] = item
        return list(by_name.values())

    def _fill_missing_users_via_ssh(
        self,
        users_by: dict[str, list[SchemaNode]],
        container_names: list[str],
    ) -> None:
        if not self.connection.ssh_enabled:
            return
        for name in container_names:
            key = name.upper()
            # PDB$SEED is a template — 0 users is expected; don't force a fetch unless absent.
            if key == "PDB$SEED" and key in users_by:
                continue
            if users_by.get(key):
                continue
            try:
                rows = fetch_container_users_via_ssh(self.connection, key)
            except Exception:
                continue
            if rows:
                users_by[key] = _user_nodes(rows)

    def _load_inventory(self) -> None:
        if self._containers is not None:
            return

        pdb_name = self._pdb_name()
        local_users = self._load_local_users()
        containers: list[DatabaseSummary] = []
        users_by: dict[str, list[SchemaNode]] = {}

        if local_users:
            users_by[pdb_name.upper()] = list(local_users)

        # Merge every available source; prefer larger per-container user lists.
        for loader in (
            self._load_via_ssh,
            self._load_via_session_switch,
            self._load_via_cdb_service,
        ):
            try:
                result = loader()
            except Exception:
                result = None
            if not result:
                continue
            more_containers, more_users = result
            containers = self._merge_containers(containers, more_containers)
            self._merge_users(users_by, more_users)

        # Explicit per-container SSH fills (especially CDB$ROOT when cdb_users failed).
        self._fill_missing_users_via_ssh(
            users_by,
            ["CDB$ROOT", "PDB$SEED", pdb_name.upper()],
        )

        # Connected PDB: keep the richer of local SQL vs SSH.
        if local_users:
            key = pdb_name.upper()
            if len(local_users) >= len(users_by.get(key) or []):
                users_by[key] = list(local_users)

        if not users_by.get(pdb_name.upper()) and self.connection.ssh_enabled:
            try:
                rows = fetch_pdb_users_via_ssh(self.connection, pdb_name)
                if rows:
                    users_by[pdb_name.upper()] = _user_nodes(rows)
            except Exception:
                pass

        summaries, users_by = self._ensure_all_containers(
            containers, users_by, pdb_name, local_users
        )
        self._containers = summaries
        self._users_by_container = users_by

    def list_databases(self) -> list[DatabaseSummary]:
        self._load_inventory()
        return list(self._containers or [])

    def list_schemas(self, database: str) -> list[SchemaNode]:
        self._load_inventory()
        key = (database or "").strip().upper()
        if key in self._users_by_container:
            return list(self._users_by_container[key])
        for name, users in self._users_by_container.items():
            if name.upper() == key:
                return list(users)
        return []

    def get_general_info(self, database: str) -> dict[str, str]:
        self._load_inventory()
        summary = next(
            (c for c in (self._containers or []) if c.name.upper() == database.upper()),
            None,
        )
        users = self.list_schemas(database)
        open_mode = str((summary.raw or {}).get("open_mode") or summary.status if summary else "—")
        return {
            "Container": database,
            "Type": "Oracle CDB/PDB container",
            "Open Mode": open_mode,
            "Users / Schemas": str(len(users)),
            "Connected Service": self.connection.database or "—",
            "Note": (
                "Expand this container to list Oracle users. "
                "Application data for Banking is under ORCLPDB1 (e.g. BANKING schema)."
            ),
        }

    def list_objects(self, database: str, schema: str, object_type: str) -> list[ObjectRef]:
        if object_type != "tables":
            return []
        owner = (schema or "").strip().upper()
        if not owner:
            return []
        sql = (
            "SELECT table_name AS name FROM all_tables "
            "WHERE owner = :1 ORDER BY table_name"
        )
        try:
            # Prefer querying inside the selected container when possible.
            rows = self._connector().execute_with_setup(
                [f"ALTER SESSION SET CONTAINER = {database}"]
                if database and database.upper() not in {"", self._pdb_name()}
                else [],
                sql.replace(":1", f"'{owner}'"),
            )
        except Exception:
            try:
                rows = self._connector().execute_query(
                    f"SELECT table_name AS name FROM all_tables WHERE owner = '{owner}' ORDER BY table_name"
                )
            except Exception:
                return []
        out: list[ObjectRef] = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            out.append(
                ObjectRef(
                    name=name,
                    object_type="table",
                    schema=owner,
                    qualified_name=f"{owner}.{name}",
                )
            )
        return out

    def get_table_details(self, database: str, schema: str, table: str) -> TableDetails:
        return TableDetails(name=table, schema=schema)

    def get_storage(self, database: str) -> StorageSummary:
        return StorageSummary(growth_note="Oracle storage details use DBA views when available.")

    def get_statistics(self, database: str) -> DbStatistics:
        users = self.list_schemas(database)
        return DbStatistics(schemas=len(users))

    def get_configuration(self, database: str) -> ConfigInfo:
        return ConfigInfo(
            items=[
                ("Engine", "oracle"),
                ("Container", database),
                ("Service", self.connection.database or "—"),
            ]
        )

    def list_extensions(self, database: str) -> list[ExtensionInfo]:
        return []

    def get_maintenance(self, database: str) -> MaintenanceInfo:
        return MaintenanceInfo(autovacuum_status="—")

    def get_backup_info(self, database: str) -> BackupInfo:
        return BackupInfo(status="—")
