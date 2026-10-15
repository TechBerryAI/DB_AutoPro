"""Pinned SSH host keys for every Paramiko connection this app opens.

The first connection to a host stores the key the server presents, so login
can continue with the username and password. A later connection that presents
a different key is still rejected and does not replace the stored key.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from backend.core.paths import DATA_DIR

KNOWN_HOSTS_PATH = DATA_DIR / "ssh_known_hosts"


class SshHostKeyError(Exception):
    """Host key was not accepted. The connection must not continue."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        reason: str,
        key_type: str = "",
        fingerprint: str = "",
        key: object | None = None,
        detail: str = "",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.reason = reason
        self.key_type = key_type
        self.fingerprint = fingerprint
        self.key = key
        self.detail = detail
        super().__init__(self.public_message())

    def public_message(self) -> str:
        where = f"{self.host}:{self.port}"
        if self.reason == "mismatch":
            return (
                f"SSH host key mismatch for {where}. "
                f"The server presented {self.key_type} {self.fingerprint}. "
                "It does not match the trusted key. The connection was not opened "
                "and the stored key was not replaced."
            )
        if self.reason == "malformed":
            extra = f" {self.detail}" if self.detail else ""
            return (
                "SSH known hosts file is malformed."
                f"{extra} The connection was not opened."
            )
        if self.reason == "unverified":
            return (
                f"SSH host key for {where} was not saved. "
                "The fingerprint you entered does not match the key from this connection. "
                "Read the fingerprint on the server itself, then enter that value."
            )
        shown = f"{self.key_type} {self.fingerprint}".strip()
        return (
            f"SSH host key is not trusted for {where}. "
            f"The server presented {shown}. "
            "That value was not saved. Obtain the host key fingerprint from the "
            "server console and enter it in the trust dialog. "
            "The connection was not opened."
        )


def host_key_name(host: str, port: int) -> str:
    """OpenSSH lookup name. Port 22 is stored as the bare host."""
    cleaned = host.strip()
    if int(port) == 22:
        return cleaned
    return f"[{cleaned}]:{int(port)}"


def fingerprint_sha256(key) -> str:
    """OpenSSH-style SHA256 fingerprint, without base64 padding."""
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def normalize_fingerprint(value: str) -> str:
    compact = "".join(str(value or "").split())
    if compact.lower().startswith("sha256:"):
        compact = "SHA256:" + compact.split(":", 1)[1]
    return compact.rstrip("=")


def load_known_hosts(path: Path | None = None):
    """Load trusted keys. A bad line rejects the whole file."""
    import paramiko
    from paramiko.hostkeys import HostKeyEntry

    store = path or KNOWN_HOSTS_PATH
    keys = paramiko.HostKeys()
    if not store.exists():
        return keys
    try:
        lines = store.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SshHostKeyError(
            host="",
            port=0,
            reason="malformed",
            detail="The known hosts file could not be read.",
        ) from exc
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = HostKeyEntry.from_line(line, lineno)
        except Exception as exc:
            raise SshHostKeyError(
                host="",
                port=0,
                reason="malformed",
                detail=f"Line {lineno} is not a valid host key.",
            ) from exc
        if entry is None or not getattr(entry, "valid", False):
            raise SshHostKeyError(
                host="",
                port=0,
                reason="malformed",
                detail=f"Line {lineno} is not a valid host key.",
            )
        for hostname in entry.hostnames:
            keys.add(hostname, entry.key.get_name(), entry.key)
    return keys


def decide_host_key(keys, host: str, port: int, presented_key) -> None:
    """Accept only a key that is already stored for this host and port."""
    name = host_key_name(host, port)
    found = keys.lookup(name)
    fingerprint = fingerprint_sha256(presented_key)
    key_type = presented_key.get_name()
    if not found:
        raise SshHostKeyError(
            host=host,
            port=port,
            reason="unknown",
            key_type=key_type,
            fingerprint=fingerprint,
            key=presented_key,
        )
    if keys.check(name, presented_key):
        return
    raise SshHostKeyError(
        host=host,
        port=port,
        reason="mismatch",
        key_type=key_type,
        fingerprint=fingerprint,
        key=presented_key,
    )


class _RejectUnknownHostKey:
    """Fail closed. Never adds the presented key to the client."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def missing_host_key(self, client, hostname, key) -> None:
        raise SshHostKeyError(
            host=self.host,
            port=self.port,
            reason="unknown",
            key_type=key.get_name(),
            fingerprint=fingerprint_sha256(key),
            key=key,
        )


def configure_ssh_client(client, host: str, port: int, path: Path | None = None) -> None:
    """Load pinned keys and install a policy that rejects unknown hosts."""
    store = path or KNOWN_HOSTS_PATH
    load_known_hosts(store)
    if store.exists() and hasattr(client, "load_host_keys"):
        client.load_host_keys(str(store))
    client.set_missing_host_key_policy(_RejectUnknownHostKey(host, int(port)))


def record_verified_host_key(
    *,
    host: str,
    port: int,
    key,
    verified_fingerprint: str,
    path: Path | None = None,
) -> None:
    """Store a key only when the operator's fingerprint matches that key.

    An existing different key is left unchanged.
    """
    store = path or KNOWN_HOSTS_PATH
    actual = fingerprint_sha256(key)
    if normalize_fingerprint(verified_fingerprint) != normalize_fingerprint(actual):
        raise SshHostKeyError(
            host=host,
            port=port,
            reason="unverified",
            key_type=key.get_name(),
            fingerprint=actual,
            key=key,
        )
    keys = load_known_hosts(store)
    name = host_key_name(host, port)
    found = keys.lookup(name)
    if found and not keys.check(name, key):
        raise SshHostKeyError(
            host=host,
            port=port,
            reason="mismatch",
            key_type=key.get_name(),
            fingerprint=actual,
            key=key,
        )
    keys.add(name, key.get_name(), key)
    store.parent.mkdir(parents=True, exist_ok=True)
    keys.save(str(store))


def host_key_error_from_bad_key(exc, *, host: str, port: int) -> SshHostKeyError:
    """Turn Paramiko's mismatch into our error without trusting the new key."""
    presented = exc.key
    return SshHostKeyError(
        host=host,
        port=port,
        reason="mismatch",
        key_type=presented.get_name(),
        fingerprint=fingerprint_sha256(presented),
        key=presented,
    )


def remember_presented_host_key(
    *,
    host: str,
    port: int,
    key,
    path: Path | None = None,
) -> None:
    """Store the key from this connection without asking for a typed fingerprint.

    An existing different key is left unchanged.
    """
    record_verified_host_key(
        host=host,
        port=port,
        key=key,
        verified_fingerprint=fingerprint_sha256(key),
        path=path,
    )


def connect_verified_client(
    client,
    *,
    hostname: str,
    port: int,
    username: str,
    password: str | None,
    timeout: int = 15,
    banner_timeout: int = 30,
    auth_timeout: int = 30,
    known_hosts_path: Path | None = None,
) -> None:
    """Connect with username and password. Remember an unknown host key once."""
    import paramiko

    def _open() -> None:
        configure_ssh_client(client, hostname, int(port), known_hosts_path)
        try:
            client.connect(
                hostname=hostname,
                port=int(port),
                username=username,
                password=password or None,
                allow_agent=False,
                look_for_keys=False,
                timeout=timeout,
                banner_timeout=banner_timeout,
                auth_timeout=auth_timeout,
            )
        except paramiko.BadHostKeyException as exc:
            raise host_key_error_from_bad_key(exc, host=hostname, port=int(port)) from exc

    try:
        _open()
    except SshHostKeyError as exc:
        if exc.reason != "unknown" or exc.key is None:
            raise
        remember_presented_host_key(
            host=hostname,
            port=int(port),
            key=exc.key,
            path=known_hosts_path,
        )
        try:
            client.close()
        except Exception:
            pass
        _open()
