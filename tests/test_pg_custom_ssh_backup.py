"""Custom/tar formats must not stream pg_dump stdout over SSH."""

from unittest.mock import MagicMock, patch

from backend.models.schemas import (
    BackupMethod,
    BackupMode,
    BackupRequest,
    DatabaseType,
    PgDumpFormat,
)
from backend.services.backup_mode_handlers import _postgresql_logical_full_via_ssh


def test_custom_format_writes_remote_file_instead_of_stdout(tmp_path):
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        backup_mode=BackupMode.FULL,
        database="postgres",
        pg_dump_format=PgDumpFormat.CUSTOM,
        use_remote_exec=False,
    )
    conn = MagicMock()
    conn.host = "127.0.0.1"
    conn.port = 5432
    conn.username = "postgres"
    conn.password = "secret"
    conn.database = "postgres"
    conn.extra_options = {}
    ssh = {
        "ssh_host": "192.168.1.113",
        "fallback_host": "192.168.1.113",
        "ssh_port": 22,
        "username": "ubuntu",
        "password": "x",
    }
    output = tmp_path / "postgresql_postgres_full_test.dump"
    dump_cmds: list[str] = []

    def fake_run_ssh_command(command=None, timeout=None, on_progress=None, **kwargs):
        dump_cmds.append(command or "")
        return 0, "", ""

    with patch(
        "backend.services.ssh_client.resolve_remote_pg_dump_path",
        return_value="/usr/bin/pg_dump",
    ), patch(
        "backend.services.ssh_client.can_use_postgres_peer_auth",
        return_value=False,
    ), patch(
        "backend.services.ssh_client.pg_endpoint_from_connection",
        return_value=("127.0.0.1", 5432),
    ), patch(
        "backend.services.ssh_client.remove_remote_path",
    ), patch(
        "backend.services.ssh_client.remote_file_size",
        return_value=128,
    ), patch(
        "backend.services.ssh_client.download_remote_file",
        side_effect=lambda *a, **k: output.write_bytes(b"PGDMP" + b"\x00" * 20),
    ), patch(
        "backend.services.ssh_client.stream_ssh_stdout_to_file",
        side_effect=AssertionError("custom format must not stream stdout"),
    ), patch(
        "backend.services.ssh_client.run_ssh_command",
        side_effect=fake_run_ssh_command,
    ), patch(
        "backend.services.backup_mode_handlers._record_postgresql_chain",
    ):
        result = _postgresql_logical_full_via_ssh(
            request,
            output,
            conn,
            ssh,
            cli_not_found_error=lambda t: RuntimeError(t),
        )

    assert result == output
    assert dump_cmds, "expected a remote pg_dump file write"
    assert "-f -" not in dump_cmds[0]
    assert "--format=custom" in dump_cmds[0]
    assert "/tmp/" in dump_cmds[0]
