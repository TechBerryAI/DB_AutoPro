"""SSH host keys are stored on connect. The fingerprint prompt is not shown."""

from __future__ import annotations


def maybe_offer_host_key_trust(master, error: BaseException) -> None:
    """Do not open a host-key window. Connection uses the saved credentials."""
    return
