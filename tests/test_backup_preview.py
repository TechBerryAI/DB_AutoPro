"""Backup preview and PostgreSQL tool resolution tests."""

from unittest.mock import patch

import pytest

from backend.connectors.base import ConnectionInfo
from backend.models.schemas import BackupMethod, BackupMode, BackupRequest, DatabaseType
from backend.services.backup_catalog import describe_backup_command
from backend.services.pg_tools import resolve_local_pg_tool


def test_describe_backup_command_shows_remote_host_with_ssh_tunnel_note():
    conn = ConnectionInfo(
        host="127.0.0.1",
        port=59495,
        database="postgres",
        username="postgres",
        password="secret",
        extra_options={
            "ssh_enabled": True,
            "db_host": "192.168.1.57",
            "db_port": 5432,
            "mongo_host": "192.168.1.57",
            "mongo_port": 5432,
        },
    )
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="postgres",
    )
    with patch("backend.services.backup_catalog.resolve_connection", return_value=conn):
        preview = describe_backup_command(request)

    assert "pg_dump -h 192.168.1.57 -p 5432" in preview
    assert "127.0.0.1:59495" in preview
    assert "192.168.1.57:5432" in preview


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


@pytest.mark.skipif(
    not __import__("pathlib").Path(r"C:\Program Files\PostgreSQL").exists(),
    reason="PostgreSQL not installed in default location",
)
def test_resolve_local_pg_tool_finds_program_files_install():
    path = resolve_local_pg_tool("pg_dump")
    assert path.lower().endswith("pg_dump.exe")
    assert __import__("pathlib").Path(path).is_file()
