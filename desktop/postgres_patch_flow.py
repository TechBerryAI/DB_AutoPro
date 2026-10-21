"""Button gates for the PostgreSQL administration patch page. No window and no SQL execution."""

from __future__ import annotations


def execution_block_reason(
    *,
    connected: bool,
    checks_ready: bool,
    file_validated: bool,
    duplicate: bool,
    non_transactional_unconfirmed: bool = False,
    interrupted_unreconciled: bool = False,
    review_shown: bool,
    confirmed: bool,
    operator_known: bool,
    busy: bool,
) -> str | None:
    """Return why Execute Patch stays disabled, or None when execution is allowed."""
    if busy:
        return "A patch operation is already running."
    if not connected:
        return "PostgreSQL is not connected."
    if not checks_ready:
        return "Execution stays closed until administration checks pass with no critical failure."
    if interrupted_unreconciled:
        return "An earlier run is still marked running. Reconcile that run before retrying. It will not run automatically."
    if duplicate:
        return "This patch checksum already completed on the target database. Confirm an intentional re-run to execute it again."
    if non_transactional_unconfirmed:
        return "This script contains statements that cannot be rolled back. Confirm that run before execution."
    if not file_validated:
        return "Validate the SQL file before review."
    if not review_shown:
        return "Open the review before execution."
    if not operator_known:
        return "Operator identity is missing, so the patch cannot be confirmed."
    if not confirmed:
        return "Confirm the review as the signed-in operator before execution."
    return None
