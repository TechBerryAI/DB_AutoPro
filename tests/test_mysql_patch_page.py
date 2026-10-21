"""Button rules and report export for the MySQL patch page. No window and no SSH."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.services.mysql_patch.models import MysqlPatchAuditRecord, PackageOrigin, UpgradeKind
from desktop.app import (
    CONNECTION_REQUIRED_KEYS,
    NAV_GROUPS,
    NAV_ITEMS,
    PATCH_SUBNAV,
    mysql_software_patch_visible,
)
from backend.services.mysql_patch.precheck import verified_no_update
from desktop.mysql_patch_export import export_patch_csv, export_patch_pdf
from desktop.mysql_patch_flow import (
    apply_allowed,
    approve_allowed,
    plan_allowed,
    precheck_allowed,
    preview_status_text,
    refresh_allowed,
    version_status_text,
)


def test_mysql_patch_page_is_in_operations_and_does_not_require_a_db_session():
    group_keys = [key for _group, items in NAV_GROUPS for key, _label, _cls in items]
    child_keys = [key for key, _label, _cls in PATCH_SUBNAV]
    item_keys = [key for key, _label, _cls in NAV_ITEMS]
    assert "patching" in group_keys
    assert "mysql_patch" not in group_keys
    assert child_keys == ["mysql_patch"]
    assert "mysql_patch" in item_keys
    assert "mysql_patch" not in CONNECTION_REQUIRED_KEYS


def test_mysql_patch_menu_is_only_for_a_connected_mysql_server():
    mysql = SimpleNamespace(db_type=SimpleNamespace(value="mysql"))
    postgres = SimpleNamespace(db_type=SimpleNamespace(value="postgresql"))

    assert mysql_software_patch_visible(mysql) is True
    assert mysql_software_patch_visible(postgres) is False
    assert mysql_software_patch_visible(SimpleNamespace(db_type="oracle")) is False
    assert mysql_software_patch_visible(None) is False


def test_apply_stays_disabled_until_precheck_preview_and_approval():
    approval = SimpleNamespace(high_risk_confirmed=True, plan_digest="abc")
    assert apply_allowed(precheck_ready=True, preview_accepted=True, approval=approval, busy=False) is True
    assert apply_allowed(precheck_ready=False, preview_accepted=True, approval=approval, busy=False) is False
    assert apply_allowed(precheck_ready=True, preview_accepted=False, approval=approval, busy=False) is False
    assert apply_allowed(precheck_ready=True, preview_accepted=True, approval=None, busy=False) is False
    weak = SimpleNamespace(high_risk_confirmed=False, plan_digest="abc")
    assert apply_allowed(precheck_ready=True, preview_accepted=True, approval=weak, busy=False) is False
    assert apply_allowed(precheck_ready=True, preview_accepted=True, approval=approval, busy=True) is False


def test_other_actions_follow_the_same_gates():
    assert precheck_allowed(server_selected=True, backup_path="C:/backup.sql", busy=False) is True
    assert precheck_allowed(server_selected=True, backup_path="  ", busy=False) is False
    assert refresh_allowed(precheck_ready=True, busy=False) is True
    assert refresh_allowed(precheck_ready=False, busy=False) is False
    assert plan_allowed(preview_accepted=True, busy=False) is True
    assert approve_allowed(plan_ready=True, already_approved=False, busy=False) is True
    assert approve_allowed(plan_ready=True, already_approved=True, busy=False) is False


def _discovery(**overrides):
    base = dict(
        mysql_server_version="8.4.11-1.el10",
        candidate_version="8.4.11-1.el10",
        upgrade_kind=UpgradeKind.NONE,
        repository_origin=PackageOrigin.CENTOS_REPOSITORIES,
        patch_available=False,
        v1_patch_eligible=False,
        stop_reason="No newer MySQL package is available from the CentOS repositories",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_no_update_state_blocks_plan_approval_and_apply():
    discovery = _discovery()
    approval = SimpleNamespace(high_risk_confirmed=True, plan_digest="abc")

    assert verified_no_update(discovery) is True
    assert version_status_text(discovery) == (
        "No update available. Installed 8.4.11-1.el10 matches the repository candidate."
    )
    assert preview_status_text(discovery) == "No update available. A patch plan is not created."
    assert refresh_allowed(precheck_ready=True, busy=False, update_available=False) is False
    assert plan_allowed(preview_accepted=True, busy=False, update_available=False) is False
    assert approve_allowed(plan_ready=True, already_approved=False, busy=False, update_available=False) is False
    assert apply_allowed(
        precheck_ready=True,
        preview_accepted=True,
        approval=approval,
        busy=False,
        update_available=False,
    ) is False


def test_available_update_keeps_the_candidate_actions():
    discovery = _discovery(
        candidate_version="8.4.12-1.el10",
        upgrade_kind=UpgradeKind.MAINTENANCE,
        patch_available=True,
        v1_patch_eligible=True,
        stop_reason=None,
    )

    assert verified_no_update(discovery) is False
    assert "Candidate 8.4.12-1.el10" in version_status_text(discovery)
    assert refresh_allowed(precheck_ready=True, busy=False, update_available=True) is True
    assert plan_allowed(preview_accepted=True, busy=False, update_available=True) is True


def test_repository_query_failure_is_not_a_no_update_state():
    discovery = _discovery(
        repository_origin=PackageOrigin.UNKNOWN,
        candidate_version=None,
        stop_reason="DNF repository metadata is unavailable. The installed MySQL package was still detected.",
    )

    assert verified_no_update(discovery) is False
    assert "No update available" not in version_status_text(discovery)
    assert refresh_allowed(precheck_ready=False, busy=False, update_available=False) is False


def test_patch_report_export_omits_secrets(tmp_path):
    record = MysqlPatchAuditRecord(
        job_id="job-1",
        server_id="db-01",
        initiated_by="alex",
        started_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        finished_at=datetime(2026, 10, 13, 0, 5, tzinfo=timezone.utc),
        current_version="8.0.36-0ubuntu0.24.04.1",
        target_version="8.0.40-0ubuntu0.24.04.1",
        plan_id="plan-1",
        plan_digest="a" * 64,
        approved_by="alex",
        stage="completed",
        execution_outcome="dry_run_passed",
        verification_outcome="verification_failed",
        final_status="dry_run_passed",
        error_message="password=[REDACTED]",
        packages_installed=False,
        dry_run=True,
    )
    csv_path = export_patch_csv(record, tmp_path / "report.csv")
    pdf_path = export_patch_pdf(record, tmp_path / "report.pdf")
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert "hunter2" not in csv_text
    assert "db-01" in csv_text
    assert "dry_run_passed" in csv_text
    assert pdf_path.stat().st_size > 0
