"""Monitoring service dashboard metrics tests."""

import json
from datetime import datetime, timezone

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import MonitoringService


def _audit_line(
    *,
    action: str,
    status: str,
    db_type: str,
    timestamp: str,
) -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "level": "INFO",
            "logger": "dap.audit",
            "message": f"{action} on sample by system: {status}",
            "data": {
                "action": action,
                "user": "system",
                "resource": "sample",
                "status": status,
                "details": {"db_type": db_type, "method": "mongodump"},
            },
        }
    )


def test_backup_audit_stats_counts_success_and_failure(tmp_path, monkeypatch):
    """Count MongoDB backup audit events from the audit log schema."""
    audit_path = tmp_path / "audit.log"
    audit_path.write_text(
        "\n".join(
            [
                _audit_line(
                    action="backup",
                    status="failed",
                    db_type="mongodb",
                    timestamp="2026-10-01T10:00:00+00:00",
                ),
                _audit_line(
                    action="backup",
                    status="success",
                    db_type="mongodb",
                    timestamp="2026-10-02T10:00:00+00:00",
                ),
                _audit_line(
                    action="backup",
                    status="success",
                    db_type="mysql",
                    timestamp="2026-10-03T10:00:00+00:00",
                ),
                _audit_line(
                    action="restore",
                    status="success",
                    db_type="mongodb",
                    timestamp="2026-10-04T10:00:00+00:00",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = MonitoringService()
    monkeypatch.setattr(service.settings.logging, "audit_log_path", audit_path)

    success, failed, last_success = service._backup_audit_stats(DatabaseType.MONGODB)
    assert success >= 1
    assert failed >= 1
    assert last_success is not None
    assert success == 1
    assert failed == 1
    assert last_success == datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)


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
