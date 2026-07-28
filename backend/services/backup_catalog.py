"""Backup category labels and command descriptions for the UI and API."""

from __future__ import annotations

import shlex

from backend.connectors.base import ConnectionInfo, resolve_connection
from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    BackupMode,
    BackupRequest,
    DatabaseType,
    MySQLBackupScope,
    MySQLDumpFormat,
    PgBackupScope,
    PgDumpFormat,
)


_METHOD_LABELS: dict[BackupMethod, str] = {
    BackupMethod.PG_DUMP: "pg_dump",
    BackupMethod.PG_DUMPALL: "pg_dumpall (cluster-wide SQL export)",
    BackupMethod.PG_BASEBACKUP: "pg_basebackup (physical cluster copy)",
    BackupMethod.MYSQLDUMP: "mysqldump",
    BackupMethod.MYSQL_PHYSICAL: "MySQL Enterprise Backup / filesystem snapshot",
    BackupMethod.MONGODUMP: "mongodump (logical BSON export)",
    BackupMethod.MSSQL_BACKUP: "BACKUP DATABASE (native .bak file)",
    BackupMethod.ORACLE_EXPDP: "expdp (Data Pump logical export)",
}


def method_label(method: BackupMethod) -> str:
    return _METHOD_LABELS.get(method, method.value)


def resolve_pg_dump_format(request: BackupRequest) -> PgDumpFormat:
    """Resolve pg_dump output format from request (including legacy extension values)."""
    if isinstance(request.pg_dump_format, PgDumpFormat):
        return request.pg_dump_format
    if request.pg_dump_format:
        try:
            return PgDumpFormat(str(request.pg_dump_format).strip().lower())
        except ValueError:
            pass

    raw = (request.pg_file_extension or "").strip().lower().lstrip(".")
    if raw in ("sql", "plain"):
        return PgDumpFormat.PLAIN
    if raw in ("tar",):
        return PgDumpFormat.TAR
    if raw in ("directory", "dir"):
        return PgDumpFormat.DIRECTORY
    if raw in ("dump", "custom", "backup"):
        return PgDumpFormat.CUSTOM
    return PgDumpFormat.CUSTOM


def resolve_pg_file_extension(request: BackupRequest) -> str:
    """Return output suffix for PostgreSQL logical backups (empty for directory format)."""
    if request.method == BackupMethod.PG_DUMPALL:
        return "sql"
    fmt = resolve_pg_dump_format(request)
    if fmt == PgDumpFormat.PLAIN:
        return "sql"
    if fmt == PgDumpFormat.TAR:
        return "tar"
    if fmt == PgDumpFormat.DIRECTORY:
        return ""
    return "dump"


def pg_dump_uses_custom_format(request: BackupRequest) -> bool:
    """True when pg_dump should use --format=custom."""
    return (
        request.db_type == DatabaseType.POSTGRESQL
        and request.method == BackupMethod.PG_DUMP
        and resolve_pg_dump_format(request) == PgDumpFormat.CUSTOM
    )


def build_pg_dump_format_args(request: BackupRequest) -> list[str]:
    """Return --format / --compress arguments for pg_dump."""
    if request.db_type != DatabaseType.POSTGRESQL or request.method != BackupMethod.PG_DUMP:
        return []
    fmt = resolve_pg_dump_format(request)
    if fmt == PgDumpFormat.PLAIN:
        return ["--format=plain"]
    if fmt == PgDumpFormat.TAR:
        # Tar supports -Z on modern PostgreSQL clients; keep moderate compression.
        return ["--format=tar", "--compress=6"]
    if fmt == PgDumpFormat.DIRECTORY:
        return ["--format=directory", "--compress=6"]
    return ["--format=custom", "--compress=6"]


def build_pg_dump_common_args(request: BackupRequest) -> list[str]:
    """Flags applied to every pg_dump run (preview and execution)."""
    if request.db_type != DatabaseType.POSTGRESQL or request.method != BackupMethod.PG_DUMP:
        return []
    return ["-v"]


def build_pg_dump_extra_args(request: BackupRequest) -> list[str]:
    """Combined verbose + scope + format args for pg_dump."""
    return (
        build_pg_dump_common_args(request)
        + build_pg_dump_scope_args(request)
        + build_pg_dump_format_args(request)
    )


def resolve_pg_backup_scope(request: BackupRequest) -> PgBackupScope:
    """Normalize the selected PostgreSQL logical backup scope."""
    if isinstance(request.pg_backup_scope, PgBackupScope):
        return request.pg_backup_scope
    raw = (request.pg_backup_scope or PgBackupScope.ENTIRE_DATABASE.value).strip().lower()
    try:
        return PgBackupScope(raw)
    except ValueError:
        return PgBackupScope.ENTIRE_DATABASE


def build_pg_dump_scope_args(request: BackupRequest) -> list[str]:
    """Return additional pg_dump arguments for PostgreSQL scope selection."""
    if request.db_type != DatabaseType.POSTGRESQL or request.method != BackupMethod.PG_DUMP:
        return []

    scope = resolve_pg_backup_scope(request)
    targets = [item.strip() for item in request.pg_scope_targets if item and item.strip()]

    if scope == PgBackupScope.ENTIRE_DATABASE:
        return []
    if scope == PgBackupScope.SINGLE_SCHEMA:
        return [f"--schema={targets[0]}"] if targets else []
    if scope == PgBackupScope.MULTIPLE_SCHEMAS:
        return [f"--schema={name}" for name in targets]
    if scope == PgBackupScope.SELECTED_TABLES:
        return [f"--table={name}" for name in targets]
    if scope == PgBackupScope.DATA_ONLY:
        return ["--data-only"]
    if scope == PgBackupScope.SCHEMA_ONLY:
        return ["--schema-only"]
    if scope == PgBackupScope.SELECTED_OBJECTS:
        raw = (request.pg_advanced_args or "").strip()
        return shlex.split(raw) if raw else []
    return []


def resolve_mysql_dump_format(request: BackupRequest) -> MySQLDumpFormat:
    """Resolve mysqldump output format (plain SQL or gzip-compressed SQL)."""
    if isinstance(request.mysql_dump_format, MySQLDumpFormat):
        return request.mysql_dump_format
    if request.mysql_dump_format:
        try:
            return MySQLDumpFormat(str(request.mysql_dump_format).strip().lower())
        except ValueError:
            pass
    return MySQLDumpFormat.PLAIN


def resolve_mysql_file_extension(request: BackupRequest) -> str:
    """Return the logical mysqldump file extension (before optional post-gzip)."""
    return "sql"


def mysql_wants_gzip(request: BackupRequest) -> bool:
    return resolve_mysql_dump_format(request) == MySQLDumpFormat.COMPRESSED


def resolve_mysql_backup_scope(request: BackupRequest) -> MySQLBackupScope:
    if isinstance(request.mysql_backup_scope, MySQLBackupScope):
        return request.mysql_backup_scope
    raw = (request.mysql_backup_scope or MySQLBackupScope.ENTIRE_DATABASE.value).strip().lower()
    try:
        return MySQLBackupScope(raw)
    except ValueError:
        return MySQLBackupScope.ENTIRE_DATABASE


def build_mysqldump_scope_args(request: BackupRequest) -> list[str]:
    """Return mysqldump flag args for scope (tables are positional — see table list helper)."""
    if request.db_type != DatabaseType.MYSQL or request.method != BackupMethod.MYSQLDUMP:
        return []
    scope = resolve_mysql_backup_scope(request)
    if scope == MySQLBackupScope.DATA_ONLY:
        return ["--no-create-info"]
    if scope == MySQLBackupScope.SCHEMA_ONLY:
        return ["--no-data"]
    return []


def mysqldump_table_targets(request: BackupRequest) -> list[str]:
    """Table names for Selected Tables scope (positional mysqldump args after DB name)."""
    if request.db_type != DatabaseType.MYSQL or request.method != BackupMethod.MYSQLDUMP:
        return []
    if resolve_mysql_backup_scope(request) != MySQLBackupScope.SELECTED_TABLES:
        return []
    return [item.strip() for item in request.mysql_scope_targets if item and item.strip()]


def build_mysqldump_command_args(
    *,
    host: str,
    port: int,
    user: str,
    database: str,
    request: BackupRequest,
    result_file: str | None = None,
    include_password_placeholder: bool = False,
) -> list[str]:
    """Build mysqldump argv (without the binary name)."""
    args = [
        "-h", host,
        "-P", str(port),
        "-u", user,
    ]
    if include_password_placeholder:
        args.append("-p****")
    args.extend(build_mysqldump_scope_args(request))
    args.extend(
        [
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            database,
        ]
    )
    tables = mysqldump_table_targets(request)
    args.extend(tables)
    if result_file:
        args.extend(["--result-file", result_file])
    return args


def category_description(category: BackupCategory) -> str:
    if category == BackupCategory.LOGICAL:
        return "Logical — SQL/object exports (pg_dump, mysqldump, mongodump, expdp)"
    return (
        "Physical — filesystem or native binary backups "
        "(pg_basebackup, MySQL Enterprise Backup / snapshot, MSSQL .bak)"
    )


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
            ext = resolve_pg_file_extension(request)
            out_token = f"<output.{ext}>" if ext else "<output_dir>"
            local_hint = _local_output_hint(request, out_token)
            if request.method == BackupMethod.PG_DUMPALL:
                if ssh_user in ("root", "postgres"):
                    remote_cmd = "sudo -u postgres pg_dumpall -f /tmp/<output.sql>"
                else:
                    remote_cmd = (
                        f"pg_dumpall -h {pg_host} -p {pg_port} -U {user} "
                        f"-f /tmp/<output.sql>"
                    )
            else:
                db = request.database or conn.database
                extra_args = build_pg_dump_extra_args(request)
                if ssh_user in ("root", "postgres"):
                    remote_cmd = (
                        f"sudo -u postgres pg_dump -d {db} "
                        f"-f - {' '.join(extra_args)}"
                    )
                else:
                    remote_cmd = (
                        f"pg_dump -h {pg_host} -p {pg_port} -U {user} -d {db} "
                        f"-f - {' '.join(extra_args)}"
                    )
            note = (
                "# Streams pg_dump over SSH to this PC (no /tmp copy + download); "
                f"saved under {local_hint}"
                if ssh_user in ("root", "postgres")
                else (
                    "# Streams pg_dump over SSH to this PC (no /tmp copy + download); "
                    f"saved under {local_hint}"
                )
            )
            return f"ssh {ssh_user}@{ssh_host} → {remote_cmd}\n{note}"
        if request.method == BackupMethod.PG_DUMPALL:
            return f"pg_dumpall -h {host} -p {port} -U {user} -f <output.sql>{tunnel}"
        db = request.database or conn.database
        ext = resolve_pg_file_extension(request)
        extra_args = build_pg_dump_extra_args(request)
        out_token = f"<output.{ext}>" if ext else "<output_dir>"
        return (
            f"pg_dump -h {host} -p {port} -U {user} -d {db} "
            f"-f {out_token} {' '.join(extra_args)}{tunnel}"
        )

    if request.db_type == DatabaseType.MYSQL:
        db = request.database or conn.database
        if request.method == BackupMethod.MYSQL_PHYSICAL:
            return (
                f"mysqlbackup / filesystem snapshot of MySQL datadir "
                f"(host {host}:{port}){tunnel}\n"
                "# Prefers MySQL Enterprise Backup (mysqlbackup) when installed; "
                "otherwise creates a consistent filesystem archive of @@datadir over SSH."
            )
        if request.backup_mode == BackupMode.INCREMENTAL:
            return (
                f"mysqlbinlog -h {host} -P {port} -u {user} -p**** "
                f"--start-position=<last_pos> --stop-position=<current_pos> <binlog_file>{tunnel}"
            )
        dump_args = build_mysqldump_command_args(
            host=host,
            port=port,
            user=user,
            database=db,
            request=request,
            result_file="<output.sql>",
            include_password_placeholder=True,
        )
        fmt = resolve_mysql_dump_format(request)
        suffix = " → gzip (.sql.gz)" if fmt == MySQLDumpFormat.COMPRESSED else ""
        if ssh_enabled:
            opts = conn.extra_options or {}
            ssh_user = opts.get("linux_username", "linux_user")
            ssh_host = opts.get("ssh_host") or host
            return (
                f"ssh {ssh_user}@{ssh_host} → mysqldump {' '.join(dump_args)}"
                f"{suffix}\n# Runs on Linux server; downloaded to this PC"
            )
        return f"mysqldump {' '.join(dump_args)}{suffix}{tunnel}"

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
