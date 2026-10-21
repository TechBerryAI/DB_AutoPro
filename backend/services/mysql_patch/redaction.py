"""Remove secret-like fragments from text that may be stored or shown."""

from __future__ import annotations

import re

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|mysql_pwd|private_key)\b(\s*[:=]\s*)\S+"
)
_SSHPASS = re.compile(r"(?i)(sshpass\s+-p\s+)\S+")
_URL_USERINFO = re.compile(r"(://[^:/\s@]+:)[^@\s]+@")


def redact_text(value: str | None) -> str | None:
    """Mask assigned secrets. Returns None when the input is None."""
    if value is None:
        return None
    masked = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    masked = _SSHPASS.sub(r"\1[REDACTED]", masked)
    return _URL_USERINFO.sub(r"\1[REDACTED]@", masked)
