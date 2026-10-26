"""Restore command descriptions for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from backend.connectors.base import resolve_connection
from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    DatabaseType,
    RestoreRequest,
    RestoreSourceType,
    RestoreTargetType,
)
from backend.services.mongo_restore import format_mongorestore_preview, plan_mongorestore
from backend.services.mongo_tools import (
    is_placeholder_remote_mongo_path,
    resolve_local_mongo_tool,
    sibling_mongo_tool_path,
)
from backend.services.ssh_client import ssh_options_from_connection


_METHOD_LABELS: dict[BackupMethod, str] = {
    BackupMethod.MONGORESTORE: "mongorestore (logical BSON import)",
    BackupMethod.PG_DUMP: "psql (SQL import)",
    BackupMethod.PG_BASEBACKUP: "pg_basebackup (physical cluster restore)",
    BackupMethod.MYSQLDUMP: "mysql (SQL import)",
    BackupMethod.MYSQL_PHYSICAL: "MySQL physical restore",
    BackupMethod.MSSQL_BACKUP: "RESTORE DATABASE (native .bak file)",
    BackupMethod.ORACLE_IMPDP: "impdp (Data Pump logical import)",
}


def restore_method_label(method: BackupMethod) -> str:
    return _METHOD_LABELS.get(method, method.value)


_RESTORE_METHODS: dict[tuple[DatabaseType, BackupCategory], list[BackupMethod]] = {
    (DatabaseType.MONGODB, BackupCategory.LOGICAL): [BackupMethod.MONGORESTORE],
    (DatabaseType.POSTGRESQL, BackupCategory.LOGICAL): [BackupMethod.PG_DUMP],
    (DatabaseType.POSTGRESQL, BackupCategory.PHYSICAL): [BackupMethod.PG_BASEBACKUP],
    (DatabaseType.MYSQL, BackupCategory.LOGICAL): [BackupMethod.MYSQLDUMP],
    (DatabaseType.MYSQL, BackupCategory.PHYSICAL): [BackupMethod.MYSQL_PHYSICAL],
    (DatabaseType.MSSQL, BackupCategory.PHYSICAL): [BackupMethod.MSSQL_BACKUP],
    (DatabaseType.ORACLE, BackupCategory.LOGICAL): [BackupMethod.ORACLE_IMPDP],
}


def restore_methods_for(
    db_type: DatabaseType, category: BackupCategory
) -> list[BackupMethod]:
    return list(_RESTORE_METHODS.get((db_type, category), []))


def _is_remote_restore_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("/") or normalized.startswith("mnt/")


def _local_mongorestore_bin(request: RestoreRequest) -> str:
    conn = resolve_connection(request.profile)
    opts = conn.extra_options or {}
    preferred = request.remote_binary_path or opts.get("mongorestore_path")
    if not preferred and opts.get("mongodump_path"):
        preferred = str(opts["mongodump_path"]).replace("mongodump", "mongorestore")
    try:
        return resolve_local_mongo_tool("mongorestore", preferred)
    except FileNotFoundError:
        return "mongorestore"


def _uses_server_restore(request: RestoreRequest) -> bool:
    if request.restore_source == RestoreSourceType.OTHER_SERVER:
        return request.restore_target == RestoreTargetType.LINUX_SERVER
    return request.restore_source == RestoreSourceType.CONNECTED_SERVER


def _ssh_preview_label(request: RestoreRequest) -> str:
    if request.restore_source == RestoreSourceType.OTHER_SERVER:
        host = (request.external_ssh_host or "other_host").strip()
        user = (request.external_ssh_username or "linux_user").strip()
        port = request.external_ssh_port or 22
        return f"ssh {user}@{host}:{port}"
    conn = resolve_connection(request.profile)
    opts = conn.extra_options or {}
    ssh_user = opts.get("linux_username", "linux_user")
    ssh_host = opts.get("ssh_host") or opts.get("db_host") or conn.host
    return f"ssh {ssh_user}@{ssh_host}"


def describe_restore_command(request: RestoreRequest) -> str:
    """Human-readable restore command preview."""
    conn = resolve_connection(request.profile)
    opts = conn.extra_options or {}
    auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
    mongo_host = opts.get("mongo_host") or conn.host
    mongo_port = int(opts.get("mongo_port", conn.port))
    backup_dir = request.backup_path or "<restore source>"
    db_name = request.database or conn.database or "<database>"

    if request.db_type == DatabaseType.POSTGRESQL:
        tool = "pg_restore" if backup_dir.lower().endswith((".dump", ".backup")) else "psql"
        if _uses_server_restore(request):
            return (
                f"{_ssh_preview_label(request)} → {tool} into {db_name} "
                f"from {backup_dir}"
            )
        if request.restore_source == RestoreSourceType.OTHER_SERVER:
            return (
                f"Download from {_ssh_preview_label(request)} → local {tool} "
                f"into {db_name}"
            )
        return f"Local {tool} into {db_name} from {backup_dir}"

    if request.db_type == DatabaseType.MYSQL:
        if _uses_server_restore(request):
            return (
                f"{_ssh_preview_label(request)} → mysql into {db_name} "
                f"from {backup_dir}"
            )
        if request.restore_source == RestoreSourceType.OTHER_SERVER:
            return (
                f"Download from {_ssh_preview_label(request)} → local mysql "
                f"into {db_name}"
            )
        return f"Local mysql into {db_name} from {backup_dir}"

    if request.db_type != DatabaseType.MONGODB:
        return f"Restore from {backup_dir} into {request.db_type.value}"

    mongorestore = sibling_mongo_tool_path(
        request.remote_binary_path or opts.get("mongorestore_path"),
        "mongorestore",
    )
    if not mongorestore:
        mongorestore = sibling_mongo_tool_path(opts.get("mongodump_path"), "mongorestore")
    if not mongorestore or is_placeholder_remote_mongo_path(mongorestore):
        mongorestore = "mongorestore"

    try:
        plan = plan_mongorestore(request, Path(request.backup_path))
    except Exception as exc:
        return str(exc)

    uses_server = _uses_server_restore(request)
    preview_host = mongo_host if uses_server else conn.host
    preview_port = mongo_port if uses_server else conn.port
    preview_bin = mongorestore if uses_server else _local_mongorestore_bin(request)

    preview = format_mongorestore_preview(
        mongorestore_bin=preview_bin,
        host=str(preview_host),
        port=preview_port,
        username=conn.username,
        auth_database=str(auth_db),
        plan=plan,
    )

    if not uses_server:
        if request.restore_source == RestoreSourceType.OTHER_SERVER:
            return f"Download from {_ssh_preview_label(request)} → {preview}"
        return preview

    if request.remote_source_dir and _is_remote_restore_path(request.remote_source_dir):
        if plan.source_path and not plan.uses_archive_flags:
            preview = preview.replace(plan.source_path, request.remote_source_dir)

    return f"{_ssh_preview_label(request)} → {preview}"
