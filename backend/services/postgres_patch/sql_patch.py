"""Read a SQL patch file without executing it.

Keyword hints are warnings only. They are not a SQL parser or an access-control check.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.services.mysql_patch.redaction import redact_text
from backend.services.postgres_patch.identity import patch_identity_key
from backend.services.postgres_patch.script_runner import split_sql, statement_effect

_PREVIEW_CHARS = 1000
_HINTS = (
    (re.compile(r"(?i)\bdrop\b"), "DROP"),
    (re.compile(r"(?i)\btruncate\b"), "TRUNCATE"),
    (re.compile(r"(?i)\bdelete\b"), "DELETE"),
    (re.compile(r"(?i)\bupdate\b"), "UPDATE"),
    (re.compile(r"(?i)\balter\b"), "ALTER"),
    (re.compile(r"(?i)\bgrant\b|\brevoke\b"), "GRANT or REVOKE"),
)


@dataclass
class PatchFileReview:
    valid: bool
    name: str = ""
    path: str = ""
    target_database: str = ""
    description: str = ""
    checksum_sha256: str = ""
    preview: str = ""
    warnings: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    allow_repeat: bool = False
    needs_non_transactional: bool = False
    allow_non_transactional: bool = False
    statement_count: int = 0
    interrupted: bool = False
    reconcile_interrupted: bool = False
    system_identifier: str = ""
    database_oid: str = ""
    configured_endpoint: str = ""
    message: str = ""


def inspect_sql_patch(
    path: str,
    *,
    description: str,
    target_database: str,
    system_identifier: str,
    database_oid: str,
    configured_endpoint: str,
    completed_identities: set[tuple[str, str, str]],
    running_identities: set[tuple[str, str, str]] | None = None,
    legacy_running: set[tuple[str, str]] | None = None,
    allow_repeat: bool = False,
    allow_non_transactional: bool = False,
    reconcile_interrupted: bool = False,
) -> PatchFileReview:
    """Validate a local SQL file and build the review metadata. Does not connect or execute."""
    review = PatchFileReview(
        valid=False,
        description=description.strip(),
        target_database=target_database.strip(),
        system_identifier=system_identifier.strip(),
        database_oid=database_oid.strip(),
        configured_endpoint=configured_endpoint.strip(),
    )
    if not review.target_database or not review.system_identifier or not review.database_oid:
        review.message = "Live database identity is not confirmed. Run administration checks again."
        return review
    if not review.configured_endpoint:
        review.message = "The configured server endpoint is missing. The patch was not accepted."
        return review
    candidate = Path(path.strip()) if path and path.strip() else None
    if candidate is None:
        review.message = "Select a SQL file."
        return review
    review.path = str(candidate)
    review.name = candidate.name
    if not candidate.exists() or not candidate.is_file():
        review.message = "SQL file was not found."
        return review
    try:
        payload = candidate.read_bytes()
    except OSError as exc:
        review.message = redact_text(f"SQL file could not be read ({exc})") or "SQL file could not be read."
        return review
    if not payload.strip():
        review.message = "SQL file is empty."
        return review
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        review.message = "SQL file is not UTF-8 text."
        return review
    review.checksum_sha256 = hashlib.sha256(payload).hexdigest()
    review.preview = _preview(text)
    review.warnings = _keyword_warnings(text)
    review.allow_repeat = allow_repeat
    review.allow_non_transactional = allow_non_transactional
    review.reconcile_interrupted = reconcile_interrupted
    try:
        statements = split_sql(text)
    except Exception as exc:
        review.message = redact_text(f"SQL file could not be split into statements ({exc})") or "SQL file could not be read."
        return review
    review.statement_count = len(statements)
    if not statements:
        review.message = "The SQL file has no statements."
        return review
    effects = {statement_effect(item) for item in statements}
    if "explicit_transaction" in effects:
        review.message = "The script contains BEGIN, COMMIT, or ROLLBACK, so this page cannot track its transaction."
        return review
    if "non_transactional" in effects:
        review.needs_non_transactional = True
        review.warnings.append(
            "At least one statement cannot be rolled back automatically. "
            "An explicit non-transactional confirmation is required before execution."
        )
    identity = patch_identity_key(review.checksum_sha256, review.system_identifier, review.database_oid)
    if identity in (running_identities or set()) or (review.checksum_sha256, review.target_database) in (legacy_running or set()):
        review.interrupted = True
        review.warnings.append(
            "An earlier run of this checksum is still marked running. Reconcile that run before retrying. It will not run automatically."
        )
    if identity in completed_identities:
        review.duplicate_of = review.checksum_sha256
        if not allow_repeat:
            review.message = "This patch checksum already completed on the target database."
            return review
        review.warnings.append("This completed patch will run again because that was explicitly confirmed.")
    if review.interrupted and not reconcile_interrupted:
        review.message = "This patch was interrupted. Reconcile it before retrying. It was not run again."
        return review
    if review.needs_non_transactional and not allow_non_transactional:
        review.message = "Confirm a non-transactional run before execution. Nothing was executed."
        return review
    review.valid = True
    review.message = "SQL file is readable."
    return review


def format_patch_review(review: PatchFileReview) -> str:
    lines = [
        f"Patch name: {review.name or '(none)'}",
        f"Target database: {review.target_database or '(unknown)'}",
        f"Description: {review.description or '(none)'}",
        f"Checksum SHA-256: {review.checksum_sha256 or '(none)'}",
        f"Cluster: {review.system_identifier or '(unconfirmed)'}",
        f"Database OID: {review.database_oid or '(unconfirmed)'}",
        f"Configured server: {review.configured_endpoint or '(missing)'}",
        f"File: {review.path or '(none)'}",
    ]
    if review.duplicate_of:
        lines.append("Duplicate: this checksum already completed on the target database.")
    lines.append(f"Statements: {review.statement_count}")
    if review.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in review.warnings)
    else:
        lines.append("Warnings: none from the keyword hint scan.")
    lines.append("Keyword hints are not a SQL parser and are not a security control.")
    lines.append("SQL preview:")
    lines.append(review.preview or "(none)")
    if review.message:
        lines.append(review.message)
    return "\n".join(lines)


def _preview(text: str) -> str:
    clipped = text.strip()
    if len(clipped) > _PREVIEW_CHARS:
        clipped = clipped[:_PREVIEW_CHARS] + "\n..."
    return redact_text(clipped) or ""


def _keyword_warnings(text: str) -> list[str]:
    found = [label for pattern, label in _HINTS if pattern.search(text)]
    if not found:
        return []
    names = ", ".join(dict.fromkeys(found))
    return [f"Keyword hint only, not a parsed statement: {names}."]
