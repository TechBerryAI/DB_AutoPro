"""Backup category labels and command descriptions for the UI and API."""

from __future__ import annotations

from backend.connectors.base import ConnectionInfo, resolve_connection
from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    BackupMode,
    BackupRequest,
    DatabaseType,
)


_METHOD_LABELS: dict[BackupMethod, str] = {
    BackupMethod.PG_DUMP: "pg_dump (compressed custom format)",
    BackupMethod.PG_DUMPALL: "pg_dumpall (cluster-wide SQL export)",
    BackupMethod.PG_BASEBACKUP: "pg_basebackup (physical cluster copy)",
    BackupMethod.MYSQLDUMP: "mysqldump (logical SQL export)",
    BackupMethod.MONGODUMP: "mongodump (logical BSON export)",
    BackupMethod.MSSQL_BACKUP: "BACKUP DATABASE (native .bak file)",
    BackupMethod.ORACLE_EXPDP: "expdp (Data Pump logical export)",
}


def method_label(method: BackupMethod) -> str:
    return _METHOD_LABELS.get(method, method.value)


_MODE_LABELS: dict[BackupMode, str] = {
    BackupMode.FULL: "Full Backup",
    BackupMode.INCREMENTAL: "Incremental Backup",
    BackupMode.DIFFERENTIAL: "Differential Backup",
}


def mode_label(mode: BackupMode) -> str:
    return _MODE_LABELS.get(mode, mode.value.replace("_", " ").title())


def mode_description(
    db_type: DatabaseType,
    category: BackupCategory,
    mode: BackupMode,
) -> str:
    if mode == BackupMode.FULL:
        return "Full — Complete backup of the selected database or cluster"
    if mode == BackupMode.INCREMENTAL:
        if db_type == DatabaseType.POSTGRESQL:
            return (
                "Incremental — Backs up only changes since the last backup "
                "(WAL based for PostgreSQL)"
            )
        if db_type == DatabaseType.MYSQL:
            return "Incremental — Backs up changes using binary log position since last backup"
        if db_type == DatabaseType.ORACLE:
            return "Incremental — Data Pump incremental export (requires prior full backup)"
        return "Incremental — Backs up only changes since the last backup"
    if mode == BackupMode.DIFFERENTIAL:
        if db_type == DatabaseType.MSSQL:
            return "Differential — Backs up changes since the last full backup (.bak)"
        return "Differential — Backs up changes since the last full backup"
    return ""


def mode_requires_wal_note(
    db_type: DatabaseType,
    category: BackupCategory,
    mode: BackupMode,
) -> bool:
    return (
        db_type == DatabaseType.POSTGRESQL
        and category == BackupCategory.LOGICAL
        and mode in (BackupMode.INCREMENTAL, BackupMode.DIFFERENTIAL)
    )


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


def _mongo_target_database(request: BackupRequest) -> str | None:
    """Explicit database from backup form only; None means dump all databases."""
    if request.database and request.database.strip():
        return request.database.strip()
    return None


def _mongo_db_arg(request: BackupRequest) -> str:
    db = _mongo_target_database(request)
    return f" --db {db}" if db else ""


def _connection_endpoints(conn: ConnectionInfo) -> tuple[str, int, str, int, bool]:
    """Return (display_host, display_port, exec_host, exec_port, ssh_enabled)."""
    opts = conn.extra_options or {}
    exec_host, exec_port = conn.host, conn.port
    display_host = str(opts.get("db_host") or opts.get("mongo_host") or exec_host)
    display_port = int(opts.get("db_port") or opts.get("mongo_port") or exec_port)
    ssh_enabled = bool(opts.get("ssh_enabled"))
    return display_host, display_port, exec_host, exec_port, ssh_enabled


def _tunnel_note(
    display_host: str,
    display_port: int,
    exec_host: str,
    exec_port: int,
    *,
    ssh_enabled: bool,
) -> str:
    if not ssh_enabled:
        return ""
    if exec_host == display_host and exec_port == display_port:
        return ""
    return (
        f"\n# SSH tunnel on this PC: {exec_host}:{exec_port} → "
        f"{display_host}:{display_port}"
    )


def describe_backup_command(request: BackupRequest) -> str:
    """Human-readable command preview for the selected backup request."""
    conn = resolve_connection(request.profile)
    display_host, display_port, exec_host, exec_port, ssh_enabled = _connection_endpoints(conn)
    host, port, user = display_host, display_port, conn.username
    tunnel = _tunnel_note(display_host, display_port, exec_host, exec_port, ssh_enabled=ssh_enabled)

    if request.db_type == DatabaseType.POSTGRESQL:
        if request.method == BackupMethod.PG_BASEBACKUP:
            stream = (
                " -X stream"
                if request.backup_mode == BackupMode.INCREMENTAL
                else ""
            )
            return (
                f"pg_basebackup -h {host} -p {port} -U {user} "
                f"-D <output_dir> -Ft -z -P{stream}{tunnel}"
            )
        if request.backup_mode in (BackupMode.INCREMENTAL, BackupMode.DIFFERENTIAL):
            anchor = "last full backup" if request.backup_mode == BackupMode.DIFFERENTIAL else "last backup"
            return (
                f"pg_receivewal -h {host} -p {port} -U {user} "
                f"-D <wal_output_dir>  # WAL segments since {anchor}\n"
                f"# Requires wal_level=replica|logical; run Full backup first to establish chain"
                f"{tunnel}"
            )
        if ssh_enabled and request.method in (BackupMethod.PG_DUMP, BackupMethod.PG_DUMPALL):
            from backend.services.ssh_client import pg_endpoint_from_connection

            opts = conn.extra_options or {}
            ssh_user = opts.get("linux_username", "linux_user")
            ssh_host = opts.get("ssh_host") or host
            pg_host, pg_port = pg_endpoint_from_connection(conn)
            local_hint = _local_output_hint(
                request,
                "<output.dump>" if request.method == BackupMethod.PG_DUMP else "<output.sql>",
            )
            if request.method == BackupMethod.PG_DUMPALL:
                remote_cmd = (
                    f"pg_dumpall -h {pg_host} -p {pg_port} -U {user} "
                    f"-f /tmp/<output.sql>"
                )
            else:
                db = request.database or conn.database
                remote_cmd = (
                    f"pg_dump -h {pg_host} -p {pg_port} -U {user} -d {db} "
                    f"-f /tmp/<output.dump> --format=custom --compress=1"
                )
            return (
                f"ssh {ssh_user}@{ssh_host} → {remote_cmd}\n"
                f"# Runs on Linux server (local to PostgreSQL); downloaded to {local_hint}"
            )
        if request.method == BackupMethod.PG_DUMPALL:
            return f"pg_dumpall -h {host} -p {port} -U {user} -f <output.sql>{tunnel}"
        db = request.database or conn.database
        return (
            f"pg_dump -h {host} -p {port} -U {user} -d {db} "
            f"-f <output.dump> --format=custom --compress=1{tunnel}"
        )

    if request.db_type == DatabaseType.MYSQL:
        db = request.database or conn.database
        base = (
            f"mysqldump -h {host} -P {port} -u {user} -p**** {db} "
            f"--result-file <output.sql>{tunnel}"
        )
        if request.backup_mode == BackupMode.INCREMENTAL:
            return (
                f"mysqlbinlog -h {host} -P {port} -u {user} -p**** "
                f"--start-position=<last_pos> --stop-position=<current_pos> <binlog_file>{tunnel}"
            )
        return base

    if request.db_type == DatabaseType.MONGODB:
        opts = conn.extra_options or {}
        auth_db = opts.get("auth_source") or opts.get("authSource") or "admin"
        mongo_host = opts.get("mongo_host") or host
        mongo_port = opts.get("mongo_port", port)
        folder_name = _mongo_folder_preview(request)
        coll = _mongo_collection_arg(request)
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
                    f"{auth}{db_arg}{coll} "
                    f"--out {out_dir}/{folder_name}\n"
                    f"Local output dir (this PC): {local_dir}"
                )
            local_dir = request.local_output_dir or "<backups folder>"
            return (
                f"ssh {ssh_user}@{ssh_host} → run mongodump on Linux server, "
                f"then download to this PC:\n"
                f"{mongodump} --host {mongo_host} --port {mongo_port} "
                f"{auth}{db_arg}{coll} "
                f"--out {local_dir}/{folder_name}"
            )
        return (
            f"mongodump --host {host} --port {port} "
            f"{auth}{db_arg}{coll} "
            f"--out {_local_output_hint(request, folder_name)}"
        )

    if request.db_type == DatabaseType.MSSQL:
        db = request.database or conn.database
        if request.backup_mode == BackupMode.DIFFERENTIAL:
            return f"BACKUP DATABASE [{db}] TO DISK = N'<output.bak>' WITH DIFFERENTIAL, INIT"
        if request.backup_mode == BackupMode.INCREMENTAL:
            return f"BACKUP LOG [{db}] TO DISK = N'<output.trn>'"
        return f"BACKUP DATABASE [{db}] TO DISK = N'<output.bak>' WITH FORMAT, INIT"

    if request.db_type == DatabaseType.ORACLE:
        if request.backup_mode == BackupMode.INCREMENTAL:
            return (
                f"expdp {user}/****@{host}:{port}/{conn.database} "
                f"DIRECTORY=DATA_PUMP_DIR DUMPFILE=<output.dmp> "
                f"FULL=N INCREMENTAL=YES FLASHBACK_SCN=<last_full_scn>"
            )
        return (
            f"expdp {user}/****@{host}:{port}/{conn.database} "
            f"DIRECTORY=DATA_PUMP_DIR DUMPFILE=<output.dmp> FULL=Y"
        )

    return request.method.value


def category_description(category: BackupCategory) -> str:
    if category == BackupCategory.LOGICAL:
        return "Logical — SQL/object exports (pg_dump, mysqldump, mongodump, expdp)"
    return "Physical — filesystem or native binary backups (pg_basebackup, MSSQL .bak)"
