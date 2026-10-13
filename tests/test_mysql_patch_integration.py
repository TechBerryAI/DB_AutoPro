"""Opt-in read-only check against a disposable Ubuntu MySQL VM.

Normal pytest runs skip this file. Nothing here installs or upgrades packages.
"""

from __future__ import annotations

import os

import pytest

from backend.services.mysql_patch.command_runner import SshCommandRunner
from backend.services.mysql_patch.discovery import EnvironmentDiscoveryService
from backend.services.patch_service import HISTORY_FILE
from backend.services.mysql_patch.audit import default_audit_path


def integration_enabled() -> bool:
    return os.environ.get("DB_AUTOPRO_MYSQL_PATCH_IT") == "1"


def test_live_check_stays_off_unless_opted_in(monkeypatch):
    monkeypatch.delenv("DB_AUTOPRO_MYSQL_PATCH_IT", raising=False)
    assert integration_enabled() is False


def test_sql_patch_history_stays_separate_from_package_jobs():
    assert HISTORY_FILE.name == "patch_history.json"
    assert default_audit_path().name == "mysql_patch_jobs.json"
    assert HISTORY_FILE != default_audit_path()


@pytest.mark.skipif(not integration_enabled(), reason="Set DB_AUTOPRO_MYSQL_PATCH_IT=1 for a read-only Ubuntu check")
def test_disposable_ubuntu_vm_can_be_read_without_installing():
    host = os.environ.get("DB_AUTOPRO_MYSQL_PATCH_HOST", "").strip()
    username = os.environ.get("DB_AUTOPRO_MYSQL_PATCH_USER", "").strip()
    password = os.environ.get("DB_AUTOPRO_MYSQL_PATCH_PASSWORD", "")
    port = int(os.environ.get("DB_AUTOPRO_MYSQL_PATCH_PORT", "22"))
    if not host or not username or not password:
        pytest.fail("Host, user, and password environment variables are required for the opt-in check")

    report = EnvironmentDiscoveryService().discover(
        SshCommandRunner(host=host, port=port, username=username, password=password, fallback_host=host),
        server_id="mysql-patch-integration",
    )

    assert report.transport_error is None
    assert all("apt-get install" not in command for command in report.commands_run)
    assert all("apt-get upgrade" not in command for command in report.commands_run)
    assert "password" not in report.model_dump()
    assert password not in (report.output_excerpt or "")
    assert password not in (report.stop_reason or "")
