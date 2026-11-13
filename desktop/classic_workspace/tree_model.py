"""Engine-aware object-explorer tree model (no hardcoded object lists)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult
from desktop.connection_store import SavedServer
from desktop.db_catalog import fetch_mongodb_database_overview, fetch_server_databases
from desktop.explorer.factory import get_explorer_provider


class NodeKind(str, Enum):
    ROOT = "root"
    SERVER = "server"
    FOLDER = "folder"
    DATABASE = "database"
    SCHEMA = "schema"
    TABLE = "table"
    VIEW = "view"
    COLLECTION = "collection"
    INDEX = "index"
    FUNCTION = "function"
    PROCEDURE = "procedure"
    SEQUENCE = "sequence"
    TRIGGER = "trigger"
    PACKAGE = "package"
    USER = "user"
    ROLE = "role"
    PLACEHOLDER = "placeholder"


@dataclass
class TreeNodeData:
    kind: NodeKind
    label: str
    server_name: str = ""
    db_type: str = ""
    database: str = ""
    schema: str = ""
    object_name: str = ""
    object_type: str = ""
    expandable: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


def _folder(label: str, server: SavedServer, *, database: str = "", schema: str = "", object_type: str = "") -> TreeNodeData:
    return TreeNodeData(
        kind=NodeKind.FOLDER,
        label=label,
        server_name=server.name,
        db_type=server.db_type,
        database=database,
        schema=schema,
        object_type=object_type,
        expandable=True,
    )


def server_children_skeleton(server: SavedServer) -> list[TreeNodeData]:
    """Static top-level folders for a server (filled lazily on expand)."""
    engine = (server.db_type or "").lower()
    if engine == DatabaseType.MONGODB.value:
        return [
            _folder("Databases", server, object_type="databases"),
            _folder("Users", server, object_type="users"),
            _folder("Roles", server, object_type="roles"),
            _folder("Configuration", server, object_type="configuration"),
        ]
    if engine == DatabaseType.ORACLE.value:
        return [
            _folder("Schemas", server, object_type="oracle_schemas"),
            _folder("Configuration", server, object_type="configuration"),
        ]
    if engine == DatabaseType.MSSQL.value:
        return [
            _folder("Databases", server, object_type="databases"),
            _folder("Security", server, object_type="security"),
            _folder("Configuration", server, object_type="configuration"),
        ]
    if engine == DatabaseType.MYSQL.value:
        return [
            _folder("Databases", server, object_type="databases"),
            _folder("Configuration", server, object_type="configuration"),
        ]
    # PostgreSQL default
    return [
        _folder("Databases", server, object_type="databases"),
        _folder("Configuration", server, object_type="configuration"),
    ]


def database_children_skeleton(server: SavedServer, database: str) -> list[TreeNodeData]:
    engine = (server.db_type or "").lower()
    if engine == DatabaseType.MONGODB.value:
        return [
            _folder("Collections", server, database=database, object_type="collections"),
            _folder("Indexes", server, database=database, object_type="indexes"),
        ]
    if engine == DatabaseType.MYSQL.value:
        return [
            _folder("Tables", server, database=database, object_type="tables"),
            _folder("Views", server, database=database, object_type="views"),
            _folder("Procedures", server, database=database, object_type="procedures"),
            _folder("Functions", server, database=database, object_type="functions"),
        ]
    if engine == DatabaseType.MSSQL.value:
        return [
            _folder("Tables", server, database=database, schema="dbo", object_type="tables"),
            _folder("Views", server, database=database, schema="dbo", object_type="views"),
            _folder("Programmability", server, database=database, object_type="programmability"),
            _folder("Security", server, database=database, object_type="security"),
        ]
    if engine == DatabaseType.ORACLE.value:
        return [
            _folder("Tables", server, database=database, schema=database, object_type="tables"),
            _folder("Views", server, database=database, schema=database, object_type="views"),
            _folder("Packages", server, database=database, schema=database, object_type="packages"),
            _folder("Procedures", server, database=database, schema=database, object_type="procedures"),
            _folder("Triggers", server, database=database, schema=database, object_type="triggers"),
        ]
    # PostgreSQL
    return [
        _folder("Schemas", server, database=database, object_type="schemas"),
    ]


def schema_children_skeleton(server: SavedServer, database: str, schema: str) -> list[TreeNodeData]:
    return [
        _folder("Tables", server, database=database, schema=schema, object_type="tables"),
        _folder("Views", server, database=database, schema=schema, object_type="views"),
        _folder("Functions", server, database=database, schema=schema, object_type="functions"),
        _folder("Sequences", server, database=database, schema=schema, object_type="sequences"),
        _folder("Indexes", server, database=database, schema=schema, object_type="indexes"),
    ]


def load_folder_children(
    server: SavedServer,
    node: TreeNodeData,
    connection: ConnectionResult | None,
) -> list[TreeNodeData]:
    """Load live children for a folder node. Requires an active connection for live metadata."""
    if connection is None:
        return [
            TreeNodeData(
                kind=NodeKind.PLACEHOLDER,
                label="(Connect to load metadata)",
                server_name=server.name,
                db_type=server.db_type,
            )
        ]

    kind = (node.object_type or "").lower()
    engine = (server.db_type or "").lower()

    try:
        if kind == "databases":
            return _load_databases(server, connection)
        if kind == "schemas":
            return _load_schemas(server, connection, node.database)
        if kind == "oracle_schemas":
            return _load_oracle_schemas(server, connection)
        if kind == "collections":
            return _load_collections(server, connection, node.database)
        if kind == "indexes" and engine == DatabaseType.MONGODB.value:
            return _load_mongo_indexes(server, connection, node.database)
        if kind in {
            "tables",
            "views",
            "functions",
            "procedures",
            "sequences",
            "triggers",
            "packages",
            "indexes",
            "materialized_views",
            "foreign_keys",
        }:
            return _load_sql_objects(server, connection, node, kind)
        if kind == "programmability":
            return [
                _folder("Procedures", server, database=node.database, schema=node.schema or "dbo", object_type="procedures"),
                _folder("Functions", server, database=node.database, schema=node.schema or "dbo", object_type="functions"),
            ]
        if kind in {"users", "roles", "security"}:
            return [
                TreeNodeData(
                    kind=NodeKind.PLACEHOLDER,
                    label="(Use server tools / SQL to inspect security objects)",
                    server_name=server.name,
                    db_type=server.db_type,
                )
            ]
        if kind == "configuration":
            return _load_configuration(server, connection)
    except Exception as exc:
        return [
            TreeNodeData(
                kind=NodeKind.PLACEHOLDER,
                label=f"(Error: {exc})",
                server_name=server.name,
                db_type=server.db_type,
            )
        ]

    return [
        TreeNodeData(
            kind=NodeKind.PLACEHOLDER,
            label="(No objects)",
            server_name=server.name,
            db_type=server.db_type,
        )
    ]


def _load_databases(server: SavedServer, connection: ConnectionResult) -> list[TreeNodeData]:
    rows = fetch_server_databases(connection)
    out: list[TreeNodeData] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out.append(
            TreeNodeData(
                kind=NodeKind.DATABASE,
                label=name,
                server_name=server.name,
                db_type=server.db_type,
                database=name,
                expandable=True,
                meta={"size": str(row.get("size") or "—"), "status": str(row.get("status") or "")},
            )
        )
    return out


def _load_schemas(server: SavedServer, connection: ConnectionResult, database: str) -> list[TreeNodeData]:
    provider = get_explorer_provider(connection)
    schemas = provider.list_schemas(database)
    return [
        TreeNodeData(
            kind=NodeKind.SCHEMA,
            label=schema.name,
            server_name=server.name,
            db_type=server.db_type,
            database=database,
            schema=schema.name,
            expandable=True,
            meta={"tables": schema.table_count},
        )
        for schema in schemas
    ]


def _load_oracle_schemas(server: SavedServer, connection: ConnectionResult) -> list[TreeNodeData]:
    provider = get_explorer_provider(connection)
    # Oracle provider treats schemas similarly to databases/users.
    try:
        schemas = provider.list_schemas(connection.database or server.database or "")
    except Exception:
        schemas = []
    if schemas:
        return [
            TreeNodeData(
                kind=NodeKind.SCHEMA,
                label=schema.name,
                server_name=server.name,
                db_type=server.db_type,
                database=schema.name,
                schema=schema.name,
                expandable=True,
            )
            for schema in schemas
        ]
    # Fall back to database list as schema containers
    return _load_databases(server, connection)


def _load_collections(server: SavedServer, connection: ConnectionResult, database: str) -> list[TreeNodeData]:
    overview = fetch_mongodb_database_overview(connection, database)
    out: list[TreeNodeData] = []
    for coll in overview.get("collections") or []:
        name = str(coll.get("name") or "")
        if not name:
            continue
        out.append(
            TreeNodeData(
                kind=NodeKind.COLLECTION,
                label=name,
                server_name=server.name,
                db_type=server.db_type,
                database=database,
                object_name=name,
                object_type="collection",
                meta={
                    "count": coll.get("count"),
                    "size": coll.get("size"),
                    "indexes": coll.get("indexes"),
                },
            )
        )
    return out


def _load_mongo_indexes(server: SavedServer, connection: ConnectionResult, database: str) -> list[TreeNodeData]:
    from backend.connectors.base import get_connector_from_info

    connector = get_connector_from_info(connection.db_type, connection.to_connection_info())
    client = connector._get_client()  # noqa: SLF001
    try:
        db = client[database]
        out: list[TreeNodeData] = []
        for coll_name in sorted(db.list_collection_names()):
            try:
                for idx in db[coll_name].list_indexes():
                    name = str(idx.get("name") or "")
                    out.append(
                        TreeNodeData(
                            kind=NodeKind.INDEX,
                            label=f"{coll_name}.{name}",
                            server_name=server.name,
                            db_type=server.db_type,
                            database=database,
                            object_name=name,
                            object_type="index",
                            meta={"collection": coll_name, "key": str(idx.get("key") or "")},
                        )
                    )
            except Exception:
                continue
        return out or [
            TreeNodeData(
                kind=NodeKind.PLACEHOLDER,
                label="(No indexes)",
                server_name=server.name,
                db_type=server.db_type,
            )
        ]
    finally:
        client.close()


def _load_sql_objects(
    server: SavedServer,
    connection: ConnectionResult,
    node: TreeNodeData,
    object_type: str,
) -> list[TreeNodeData]:
    provider = get_explorer_provider(connection)
    database = node.database or connection.database
    schema = node.schema
    if not schema:
        # MySQL uses database-as-schema
        if (server.db_type or "").lower() == DatabaseType.MYSQL.value:
            schema = database
        else:
            schema = "public" if (server.db_type or "").lower() == DatabaseType.POSTGRESQL.value else "dbo"

    kind_map = {
        "tables": NodeKind.TABLE,
        "views": NodeKind.VIEW,
        "functions": NodeKind.FUNCTION,
        "procedures": NodeKind.PROCEDURE,
        "sequences": NodeKind.SEQUENCE,
        "triggers": NodeKind.TRIGGER,
        "packages": NodeKind.PACKAGE,
        "indexes": NodeKind.INDEX,
        "materialized_views": NodeKind.VIEW,
    }
    node_kind = kind_map.get(object_type, NodeKind.TABLE)
    try:
        objects = provider.list_objects(database, schema, object_type)
    except Exception as exc:
        return [
            TreeNodeData(
                kind=NodeKind.PLACEHOLDER,
                label=f"(Unavailable: {exc})",
                server_name=server.name,
                db_type=server.db_type,
            )
        ]
    return [
        TreeNodeData(
            kind=node_kind,
            label=obj.name,
            server_name=server.name,
            db_type=server.db_type,
            database=database,
            schema=schema,
            object_name=obj.name,
            object_type=object_type,
            meta={"row_count": obj.row_count, "size": obj.size},
        )
        for obj in objects
    ]


def _load_configuration(server: SavedServer, connection: ConnectionResult) -> list[TreeNodeData]:
    provider = get_explorer_provider(connection)
    try:
        cfg = provider.get_configuration(connection.database or server.database or "")
        items = list(cfg.items or [])
    except Exception:
        items = [("Engine", server.db_type)]
    return [
        TreeNodeData(
            kind=NodeKind.PLACEHOLDER,
            label=f"{k}: {v}",
            server_name=server.name,
            db_type=server.db_type,
            meta={"key": k, "value": v},
        )
        for k, v in items[:40]
    ]
