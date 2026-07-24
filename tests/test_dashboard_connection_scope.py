"""Dashboard connection-scoped audit filtering tests."""

from backend.models.schemas import DatabaseType
from backend.services.monitoring_service import (
    DashboardConnectionScope,
    audit_record_matches_scope,
)


def _scope(**kwargs) -> DashboardConnectionScope:
    defaults = {
        "resource": "prod-postgres",
        "host": "127.0.0.1",
        "db_type": DatabaseType.POSTGRESQL,
        "profile": "default",
        "local_dir": r"C:\backups\prod",
    }
    defaults.update(kwargs)
    return DashboardConnectionScope(**defaults)


def test_connect_audit_matches_resource_and_host():
    scope = _scope()
    assert audit_record_matches_scope(
        {
            "action": "connect",
            "resource": "prod-postgres",
            "details": {"db_type": "postgresql", "host": "127.0.0.1"},
        },
        scope,
    )
    assert not audit_record_matches_scope(
        {
            "action": "connect",
            "resource": "other-server",
            "details": {"db_type": "postgresql", "host": "10.0.0.5"},
        },
        scope,
    )


def test_backup_audit_matches_db_type_and_local_dir():
    scope = _scope()
    assert audit_record_matches_scope(
        {
            "action": "backup",
            "resource": r"C:\backups\prod\dump.sql",
            "details": {"db_type": "postgresql"},
        },
        scope,
    )
    assert not audit_record_matches_scope(
        {
            "action": "backup",
            "resource": "default",
            "details": {"db_type": "mongodb"},
        },
        scope,
    )


def test_scope_none_excludes_records():
    assert not audit_record_matches_scope({"action": "connect", "resource": "x"}, None)
