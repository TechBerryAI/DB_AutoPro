"""Tests for backup mode chain metadata and validation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.exceptions import BackupError
from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    BackupMode,
    BackupRequest,
    DatabaseType,
    backup_modes_for,
)
from backend.services.backup_chain import anchor_for_mode, load_chain, record_backup
from backend.services.backup_service import BackupService


def test_backup_modes_for_postgresql_logical():
    modes = backup_modes_for(DatabaseType.POSTGRESQL, BackupCategory.LOGICAL)
    assert BackupMode.FULL in modes
    assert BackupMode.INCREMENTAL in modes
    assert BackupMode.DIFFERENTIAL in modes


def test_backup_chain_records_full_and_incremental(tmp_path):
    request = BackupRequest(
        profile="default",
        db_type=DatabaseType.POSTGRESQL,
        method=BackupMethod.PG_DUMP,
        database="emp",
    )
    record_backup(
        tmp_path,
        request,
        {"path": str(tmp_path / "full.sql"), "lsn": "0/1000000"},
        BackupMode.FULL,
    )
    chain = load_chain(tmp_path, request)
    assert chain["full"]["lsn"] == "0/1000000"

    record_backup(
        tmp_path,
        request,
        {"path": str(tmp_path / "inc_wal"), "lsn": "0/2000000"},
        BackupMode.INCREMENTAL,
    )
    chain = load_chain(tmp_path, request)
    assert chain["last"]["lsn"] == "0/2000000"
    assert anchor_for_mode(chain, BackupMode.DIFFERENTIAL)["lsn"] == "0/1000000"
    assert anchor_for_mode(chain, BackupMode.INCREMENTAL)["lsn"] == "0/2000000"


@pytest.fixture
def backup_service(tmp_path):
    with patch("backend.services.backup_service.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.backup.output_dir = tmp_path
        settings.backup.compression = False
        settings.backup.compression_format = "none"
        settings.backup.retention_days = 7
        yield BackupService()


def test_validate_backup_mode_rejects_unsupported(backup_service):
    request = BackupRequest(
        db_type=DatabaseType.MONGODB,
        method=BackupMethod.MONGODUMP,
        backup_mode=BackupMode.INCREMENTAL,
    )
    with pytest.raises(BackupError, match="not supported"):
        backup_service._validate_backup_mode(request)


def test_mssql_differential_requires_full_backup(backup_service, tmp_path):
    request = BackupRequest(
        db_type=DatabaseType.MSSQL,
        method=BackupMethod.MSSQL_BACKUP,
        backup_mode=BackupMode.DIFFERENTIAL,
        database="testdb",
    )
    output = tmp_path / "mssql_testdb_differential_20260101.bak"
    with patch("backend.services.backup_service.get_connector"):
        with pytest.raises(BackupError, match="Full backup first"):
            backup_service._backup_mssql(request, output, None)
