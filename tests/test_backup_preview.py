"""Backup preview and PostgreSQL tool resolution tests."""

from unittest.mock import patch

import pytest

from backend.connectors.base import ConnectionInfo
from backend.models.schemas import BackupMethod, BackupMode, BackupRequest, DatabaseType, PgDumpFormat
from backend.services.backup_catalog import describe_backup_command
from backend.services.pg_tools import resolve_local_pg_tool


def test_describe_backup_command_shows_remote_pg_dump_over_ssh():
    conn = ConnectionInfo(
        host="127.0.0.1",
        port=59495,
        database="postgres",
        username="postgres",
        password="secret",
        extra_options={
            "ssh_enabled": True,
            "ssh_host": "192.168.1.57",
            "linux_username": "ubuntu",
            "db_host": "192.168.1.57",
            "db_port": 5432,
        },
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="postgres",
        pg_dump_format=PgDumpFormat.CUSTOM,
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "ssh ubuntu@192.168.1.57" in preview
    assert "pg_dump -h 127.0.0.1 -p 5432" in preview
    assert "--format=custom" in preview
    assert "-f -" in preview
    assert "Streams pg_dump over SSH" in preview
    assert "SSH tunnel" not in preview


def test_describe_backup_command_pg_plain_sql_format():
    conn = ConnectionInfo(
        host="192.168.1.95",
        port=5432,
        database="labdb",
        username="labdb",
        password="secret",
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="labdb",
        pg_dump_format=PgDumpFormat.PLAIN,
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "--format=plain" in preview
    assert "<output.sql>" in preview
    assert "--format=custom" not in preview


def test_describe_backup_command_pg_tar_format():
    conn = ConnectionInfo(
        host="192.168.1.95",
        port=5432,
        database="labdb",
        username="labdb",
        password="secret",
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="labdb",
        pg_dump_format=PgDumpFormat.TAR,
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "--format=tar" in preview
    assert "<output.tar>" in preview


def test_describe_backup_command_pg_directory_format():
    conn = ConnectionInfo(
        host="192.168.1.95",
        port=5432,
        database="labdb",
        username="labdb",
        password="secret",
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="labdb",
        pg_dump_format=PgDumpFormat.DIRECTORY,
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "--format=directory" in preview
    assert "<output_dir>" in preview


def test_describe_backup_command_pg_selected_tables_scope():
    conn = ConnectionInfo(
        host="192.168.1.95",
        port=5432,
        database="labdb",
        username="labdb",
        password="secret",
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="labdb",
        pg_dump_format=PgDumpFormat.CUSTOM,
        pg_backup_scope="selected_tables",
        pg_scope_targets=["public.users", "public.orders"],
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "--table=public.users" in preview
    assert "--table=public.orders" in preview
    assert "--format=custom" in preview


def test_describe_backup_command_legacy_sql_extension_maps_to_plain():
    conn = ConnectionInfo(
        host="192.168.1.95",
        port=5432,
        database="labdb",
        username="labdb",
        password="secret",
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="labdb",
        pg_file_extension="sql",
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "--format=plain" in preview
    assert "<output.sql>" in preview


def test_describe_backup_command_direct_connection_no_tunnel_note():
    conn = ConnectionInfo(
        host="192.168.1.57",
        port=5432,
        database="postgres",
        username="postgres",
        password="secret",
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "pg_dump -h 192.168.1.57 -p 5432" in preview
    assert "SSH tunnel" not in preview


def test_is_pg_custom_dump_detects_archive(tmp_path):
    from backend.services.pg_tools import is_pg_custom_dump

    plain = tmp_path / "plain.sql"
    plain.write_text("SELECT 1;", encoding="utf-8")
    assert not is_pg_custom_dump(plain)

    custom = tmp_path / "backup.dump"
    custom.write_bytes(b"PGDMP" + b"\x00" * 20)
    assert is_pg_custom_dump(custom)


@pytest.mark.skipif(
    not __import__("pathlib").Path(r"C:\Program Files\PostgreSQL").exists(),
    reason="PostgreSQL not installed in default location",
)
def test_resolve_local_pg_tool_finds_program_files_install():
    path = resolve_local_pg_tool("pg_dump")
    assert path.lower().endswith("pg_dump.exe")
    assert __import__("pathlib").Path(path).is_file()
