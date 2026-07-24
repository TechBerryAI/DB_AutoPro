"""Backup category labels and command descriptions for the UI and API."""

from __future__ import annotations

from backend.connectors.base import resolve_connection
from backend.models.schemas import BackupCategory, BackupMethod, BackupRequest, DatabaseType


_METHOD_LABELS: dict[BackupMethod, str] = {
    BackupMethod.PG_DUMP: "pg_dump (single database SQL export)",
    BackupMethod.PG_DUMPALL: "pg_dumpall (cluster-wide SQL export)",
    BackupMethod.PG_BASEBACKUP: "pg_basebackup (physical cluster copy)",
    BackupMethod.MYSQLDUMP: "mysqldump (logical SQL export)",
    BackupMethod.MONGODUMP: "mongodump (logical BSON export)",
    BackupMethod.MSSQL_BACKUP: "BACKUP DATABASE (native .bak file)",
    BackupMethod.ORACLE_EXPDP: "expdp (Data Pump logical export)",
}


def method_label(method: BackupMethod) -> str:
    return _METHOD_LABELS.get(method, method.value)


def _local_output_hint(request: BackupRequest, filename: str) -> str:
    base = request.local_output_dir or "<backups folder>"
    return f"{base}/{filename}"


def _mongo_folder_preview(request: BackupRequest) -> str:
    if request.backup_folder_name and request.backup_folder_name.strip():
        name = request.backup_folder_name.strip()
        if "<timestamp>" in name.lower():
            return name
        return f"{name}_<timestamp>"
    return "db_backup_<timestamp>"


def _mongo_collection_arg(request: BackupRequest) -> str:
    if request.collection:
        return f" --collection {request.collection}"
    return ""


def _mongo_gzip_arg(request: BackupRequest) -> str:
    return " --gzip" if request.gzip_archive else ""


def _mongo_target_database(request: BackupRequest) -> str | None:
    """Explicit database from backup form only; None means dump all databases."""
    if request.database and request.database.strip():
        return request.database.strip()
    return None


def _mongo_db_arg(request: BackupRequest) -> str:
    db = _mongo_target_database(request)
    return f" --db {db}" if db else ""


def describe_backup_command(request: BackupRequest) -> str:
    """Human-readable command preview for the selected backup request."""
    conn = resolve_connection(request.profile)
    host, port, user = conn.host, conn.port, conn.username

    if request.db_type == DatabaseType.POSTGRESQL:
        if request.method == BackupMethod.PG_BASEBACKUP:
            return (
                f"pg_basebackup -h {host} -p {port} -U {user} "
                f"-D <output_dir> -Ft -z -P"
            )
        if request.method == BackupMethod.PG_DUMPALL:
            return f"pg_dumpall -h {host} -p {port} -U {user} -f <output.sql>"
        db = request.database or conn.database
        return (
            f"pg_dump -h {host} -p {port} -U {user} -d {db} "
            f"-f <output.sql> --format=plain"
        )

    if request.db_type == DatabaseType.MYSQL:
        db = request.database or conn.database
        return (
            f"mysqldump -h {host} -P {port} -u {user} -p**** {db} "
            f"--result-file <output.sql>"
        )

    if request.db_type == DatabaseType.MONGODB:
        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        mongo_host = opts.get("mongo_host") or host
        mongo_port = opts.get("mongo_port", port)
        folder_name = _mongo_folder_preview(request)
        coll = _mongo_collection_arg(request)
        gzip = _mongo_gzip_arg(request)
        db_arg = _mongo_db_arg(request)
        auth = (
            f"--username {user} --password **** "
            f"--authenticationDatabase {auth_db}"
        )
        if request.use_remote_exec or opts.get("ssh_enabled"):
            mongodump = (
                request.remote_binary_path
                or opts.get("mongodump_path")
                or "/mnt/mongodb-database-tools-ubuntu2204-x86_64-100.17.0/bin/mongodump"
            )
            out_dir = request.remote_output_dir or opts.get("remote_backup_dir") or "/mnt/backup"
            ssh_user = opts.get("linux_username", "linux_user")
            ssh_host = opts.get("ssh_host") or mongo_host
            if request.use_remote_exec:
                local_dir = request.local_output_dir or "<this PC backups folder>"
                return (
                    f"ssh {ssh_user}@{ssh_host} → "
                    f"{mongodump} --host {mongo_host} --port {mongo_port} "
                    f"{auth}{db_arg}{coll}{gzip} "
                    f"--out {out_dir}/{folder_name}\n"
                    f"Local output dir (this PC): {local_dir}"
                )
            local_dir = request.local_output_dir or "<backups folder>"
            return (
                f"ssh {ssh_user}@{ssh_host} → run mongodump on Linux server, "
                f"then download to this PC:\n"
                f"{mongodump} --host {mongo_host} --port {mongo_port} "
                f"{auth}{db_arg}{coll}{gzip} "
                f"--out {local_dir}/{folder_name}"
            )
        return (
            f"mongodump --host {host} --port {port} "
            f"{auth}{db_arg}{coll}{gzip} "
            f"--out {_local_output_hint(request, folder_name)}"
        )

    if request.db_type == DatabaseType.MSSQL:
        db = request.database or conn.database
        return f"BACKUP DATABASE [{db}] TO DISK = N'<output.bak>' WITH FORMAT, INIT"

    if request.db_type == DatabaseType.ORACLE:
        return (
            f"expdp {user}/****@{host}:{port}/{conn.database} "
            f"DIRECTORY=DATA_PUMP_DIR DUMPFILE=<output.dmp> FULL=Y"
        )

    return request.method.value


def category_description(category: BackupCategory) -> str:
    if category == BackupCategory.LOGICAL:
        return "Logical — SQL/object exports (pg_dump, mysqldump, mongodump, expdp)"
    return "Physical — filesystem or native binary backups (pg_basebackup, MSSQL .bak)"
