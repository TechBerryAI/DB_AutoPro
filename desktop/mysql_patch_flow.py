"""Enable or disable MySQL patch actions. No SSH and no package install."""

from __future__ import annotations


def precheck_allowed(
    *,
    server_selected: bool,
    backup_path: str,
    busy: bool,
    backup_checked: bool = True,
) -> bool:
    return server_selected and bool(backup_path.strip()) and backup_checked and not busy


def refresh_allowed(*, precheck_ready: bool, busy: bool, update_available: bool = True) -> bool:
    return precheck_ready and update_available and not busy


def plan_allowed(*, preview_accepted: bool, busy: bool, update_available: bool = True) -> bool:
    return preview_accepted and update_available and not busy


def approve_allowed(
    *,
    plan_ready: bool,
    already_approved: bool,
    busy: bool,
    update_available: bool = True,
) -> bool:
    return plan_ready and update_available and not already_approved and not busy


def apply_allowed(
    *,
    precheck_ready: bool,
    preview_accepted: bool,
    approval,
    busy: bool,
    update_available: bool = True,
) -> bool:
    """Apply stays off until prechecks, preview, and high-risk approval all pass."""
    if busy or not update_available or not precheck_ready or not preview_accepted or approval is None:
        return False
    if not getattr(approval, "high_risk_confirmed", False):
        return False
    plan_digest = getattr(approval, "plan_digest", "")
    return bool(plan_digest)


def version_status_text(discovery) -> str:
    """Show a no-update state instead of repeating the installed package as a candidate."""
    from backend.services.mysql_patch.precheck import verified_no_update

    current = getattr(discovery, "mysql_server_version", None) or "-"
    if verified_no_update(discovery):
        return f"No update available. Installed {current} matches the repository candidate."
    candidate = getattr(discovery, "candidate_version", None) or "-"
    return f"Current {current}  |  Candidate {candidate}"


def preview_status_text(discovery) -> str:
    from backend.services.mysql_patch.precheck import verified_no_update

    if verified_no_update(discovery):
        return "No update available. A patch plan is not created."
    return "Run Refresh Patch Information after prechecks pass."
