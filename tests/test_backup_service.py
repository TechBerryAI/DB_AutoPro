"""Backup service tests."""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.exceptions import BackupError
from backend.models.schemas import BackupMethod, BackupRequest, DatabaseType
from backend.services.backup_service import BackupService


@pytest.fixture
def backup_service(tmp_path):
    with patch("backend.services.backup_service.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.backup.output_dir = tmp_path
        settings.backup.compression = False
        settings.backup.compression_format = "none"
        settings.backup.retention_days = 7
        yield BackupService()


def test_list_backups_empty(backup_service):
    assert backup_service.list_backups() == []


def test_validate_backup_missing(backup_service):
    with pytest.raises(BackupError):
        backup_service.validate_backup("/nonexistent/path.sql")


def test_validate_backup_valid_file(backup_service, tmp_path):
    backup_file = tmp_path / "test.sql"
    backup_file.write_text("SELECT 1;")
    assert backup_service.validate_backup(str(backup_file)) is True


def test_apply_retention_policy(backup_service, tmp_path):
    old_file = tmp_path / "old_backup.sql"
    old_file.write_text("data")
    import os
    import time
    old_time = time.time() - (40 * 86400)
    os.utime(old_file, (old_time, old_time))
    deleted = backup_service.apply_retention_policy()
    assert deleted >= 1
