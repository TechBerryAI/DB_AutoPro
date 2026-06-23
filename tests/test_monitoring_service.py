"""Monitoring service dashboard metrics tests."""

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService


def test_backup_audit_stats_counts_success_and_failure():
    service = MonitoringService()
    success, failed, last_success = service._backup_audit_stats(DatabaseType.MONGODB)
    assert success >= 1
    assert failed >= 1
    assert last_success is not None


def test_dashboard_stats_uses_inventory_and_audit(monkeypatch, tmp_path):
    service = MonitoringService()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "sample_backup").mkdir()

    monkeypatch.setattr(service.backup_service, "list_local_backups", lambda _local_dir=None: [
        {
            "name": "sample_backup",
            "path": str(backup_dir / "sample_backup"),
            "size_bytes": 4096,
            "created_at": "2026-06-23T10:00:00",
            "remote": False,
        }
    ])
    monkeypatch.setattr(
        service,
        "_backup_audit_stats",
        lambda _db_type=None: (3, 2, None),
    )
    monkeypatch.setattr(
        service,
        "_storage_used_mb",
        lambda **_kwargs: 4.0,
    )

    stats = service.get_dashboard_stats(db_type=DatabaseType.MONGODB, list_remote=False)
    assert stats.total_backups == 1
    assert stats.successful_backups == 3
    assert stats.failed_backups == 2


def test_storage_usage_percent_accepts_dashboard_context():
    service = MonitoringService()
    pct = service.get_storage_usage_percent(
        profile="default",
        db_type=DatabaseType.MONGODB,
        list_remote=False,
        remote_dir="/mnt/backup",
        local_dir=None,
    )
    assert isinstance(pct, float)
