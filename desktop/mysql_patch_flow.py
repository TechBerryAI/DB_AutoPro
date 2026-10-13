"""Enable or disable MySQL patch actions. No SSH and no package install."""

from __future__ import annotations


def precheck_allowed(*, server_selected: bool, backup_path: str, busy: bool) -> bool:
    return server_selected and bool(backup_path.strip()) and not busy


def refresh_allowed(*, precheck_ready: bool, busy: bool) -> bool:
    return precheck_ready and not busy


def plan_allowed(*, preview_accepted: bool, busy: bool) -> bool:
    return preview_accepted and not busy


def approve_allowed(*, plan_ready: bool, already_approved: bool, busy: bool) -> bool:
    return plan_ready and not already_approved and not busy


def apply_allowed(*, precheck_ready: bool, preview_accepted: bool, approval, busy: bool) -> bool:
    """Apply stays off until prechecks, preview, and high-risk approval all pass."""
    if busy or not precheck_ready or not preview_accepted or approval is None:
        return False
    if not getattr(approval, "high_risk_confirmed", False):
        return False
    plan_digest = getattr(approval, "plan_digest", "")
    return bool(plan_digest)
