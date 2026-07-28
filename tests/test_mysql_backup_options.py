from backend.models.schemas import (
    BackupMethod,
    BackupRequest,
    DatabaseType,
    MySQLBackupScope,
    MySQLDumpFormat,
)
from backend.services.backup_catalog import (
    build_mysqldump_scope_args,
    describe_backup_command,
    mysqldump_table_targets,
    mysql_wants_gzip,
)


def test_mysql_schema_only_args():
    req = BackupRequest(
        db_type=DatabaseType.MYSQL,
        method=BackupMethod.MYSQLDUMP,
        database="employees",
        mysql_backup_scope=MySQLBackupScope.SCHEMA_ONLY,
        mysql_dump_format=MySQLDumpFormat.PLAIN,
    )
    assert build_mysqldump_scope_args(req) == ["--no-data"]
    assert mysqldump_table_targets(req) == []
    assert mysql_wants_gzip(req) is False


def test_mysql_data_only_and_tables():
    req = BackupRequest(
        db_type=DatabaseType.MYSQL,
        method=BackupMethod.MYSQLDUMP,
        database="employees",
        mysql_backup_scope=MySQLBackupScope.DATA_ONLY,
        mysql_dump_format=MySQLDumpFormat.COMPRESSED,
    )
    assert build_mysqldump_scope_args(req) == ["--no-create-info"]
    assert mysql_wants_gzip(req) is True


def test_mysql_selected_tables():
    req = BackupRequest(
        db_type=DatabaseType.MYSQL,
        method=BackupMethod.MYSQLDUMP,
        database="dba",
        mysql_backup_scope=MySQLBackupScope.SELECTED_TABLES,
        mysql_scope_targets=["t1", "t2"],
    )
    assert mysqldump_table_targets(req) == ["t1", "t2"]


def test_mysql_physical_preview_mentions_snapshot():
    req = BackupRequest(
        db_type=DatabaseType.MYSQL,
        method=BackupMethod.MYSQL_PHYSICAL,
        database="employees",
    )
    text = describe_backup_command(req)
    assert "filesystem snapshot" in text.lower() or "mysqlbackup" in text.lower()
