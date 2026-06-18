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
        db = request.database or conn.database
        return (
            f"mongodump --host {host} --port {port} --username {user} "
            f"--password **** --db {db} --out <output_dir>"
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
