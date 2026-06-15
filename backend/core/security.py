"""Secrets management and credential utilities."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr


def get_secret(key: str, default: str = "") -> SecretStr:
    """Resolve secret from environment or secrets file."""
    env_value = os.getenv(key)
    if env_value:
        return SecretStr(env_value)

    secrets_file = os.getenv("DAP_SECRETS_FILE")
    if secrets_file and Path(secrets_file).exists():
        with open(secrets_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        return SecretStr(v.strip())

    return SecretStr(default)


def mask_secret(value: str, visible_chars: int = 4) -> str:
    """Mask sensitive values for logging."""
    if len(value) <= visible_chars:
        return "****"
    return value[:visible_chars] + "*" * (len(value) - visible_chars)
