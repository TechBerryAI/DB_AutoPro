"""SSH tunnel helpers for reaching databases on remote Linux servers."""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from desktop.connection import ConnectionResult

_active_forwarder: Any = None


def _remote_bind_host(connection: ConnectionResult) -> str:
    ssh_host = connection.ssh_host or connection.host
    if ssh_host == connection.host:
        return "127.0.0.1"
    return connection.host


def probe_tcp(host: str, port: int, timeout: float = 5.0) -> str | None:
    """Return None if TCP connect succeeds, otherwise a short error reason."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, int(port)))
        return None
    except socket.timeout:
        return "timed out"
    except ConnectionRefusedError:
        return "connection refused"
    except OSError as exc:
        return str(exc) or exc.__class__.__name__
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _local_ipv4_prefixes() -> list[str]:
    """Return local interface IPv4 addresses (best-effort)."""
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def _host_network_hint(ssh_host: str) -> str:
    """Extra guidance when the target looks like a dead VirtualBox/host-only guest."""
    host = (ssh_host or "").strip()
    if not host.startswith("192.168.56."):
        return ""
    local = _local_ipv4_prefixes()
    on_host_only = any(ip.startswith("192.168.56.") for ip in local)
    if not on_host_only:
        return (
            "\n\nHint: 192.168.56.x is usually a VirtualBox Host-Only network. "
            "This PC has no 192.168.56.* adapter right now - use the Linux server's "
            "LAN/Wi-Fi IP instead (the one you can ping from this PC)."
        )
    return (
        "\n\nHint: 192.168.56.x is a VirtualBox Host-Only address. "
        f"No SSH response from {host} - start/boot that Linux VM, confirm its IP "
        "(ip a / ifconfig), or use the server's real LAN IP if Oracle runs there "
        "(for example your saved Banking_DB host)."
    )


def _ssh_unreachable_message(ssh_host: str, ssh_port: int, reason: str) -> str:
    return (
        f"SSH connection failed: {reason} reaching {ssh_host}:{ssh_port}.\n\n"
        "This is a network reachability problem - Linux username/password were "
        "not checked yet because the host never answered.\n"
        "Confirm: (1) the Linux host IP is correct and the machine is online, "
        "(2) SSH is listening on that port, (3) firewall allows it from this PC.\n"
        "If the database port is open directly, uncheck the SSH tunnel option."
        f"{_host_network_hint(ssh_host)}"
    )


class ParamikoTunnel:
    """Local TCP forward to a remote host:port through an SSH session."""

    def __init__(
        self,
        ssh_host: str,
        ssh_port: int,
        ssh_username: str,
        ssh_password: str,
        remote_host: str,
        remote_port: int,
    ) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError(
                "paramiko is not installed. Run: pip install paramiko"
            ) from exc

        self._stopped = False
        # Fail fast when the host/port is down (avoid Paramiko's long wait).
        probe_reason = probe_tcp(ssh_host, ssh_port, timeout=5.0)
        if probe_reason is not None:
            raise RuntimeError(
                _ssh_unreachable_message(ssh_host, ssh_port, probe_reason)
            )

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._client.connect(
                hostname=ssh_host,
                port=ssh_port,
                username=ssh_username,
                password=ssh_password or None,
                allow_agent=False,
                look_for_keys=False,
                timeout=15,
                banner_timeout=20,
                auth_timeout=20,
            )
        except paramiko.AuthenticationException as exc:
            raise RuntimeError(
                f"SSH authentication failed for Linux user '{ssh_username}' on "
                f"{ssh_host}:{ssh_port}."
            ) from exc
        except (socket.timeout, TimeoutError, OSError) as exc:
            reason = "timed out" if "timed out" in str(exc).lower() else str(exc)
            raise RuntimeError(
                _ssh_unreachable_message(ssh_host, ssh_port, reason)
            ) from exc
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            if "timed out" in detail.lower() or "timeout" in detail.lower():
                raise RuntimeError(
                    _ssh_unreachable_message(ssh_host, ssh_port, "timed out")
                ) from exc
            raise RuntimeError(f"SSH connection failed: {detail}") from exc

        self._transport = self._client.get_transport()
        if self._transport is None:
            raise RuntimeError("SSH transport could not be established.")

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(5)
        self.local_bind_port = self._server.getsockname()[1]
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._stopped:
            try:
                self._server.settimeout(1.0)
                client_sock, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._forward_connection,
                args=(client_sock,),
                daemon=True,
            ).start()

    def _forward_connection(self, client_sock: socket.socket) -> None:
        try:
            chan = self._transport.open_channel(
                "direct-tcpip",
                (self._remote_host, self._remote_port),
                client_sock.getpeername(),
            )
        except Exception:
            client_sock.close()
            return
        threading.Thread(
            target=self._pipe,
            args=(client_sock, chan),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pipe,
            args=(chan, client_sock),
            daemon=True,
        ).start()

    @staticmethod
    def _pipe(src: Any, dst: Any) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            for obj in (src, dst):
                try:
                    obj.close()
                except Exception:
                    pass

    def stop(self) -> None:
        self._stopped = True
        try:
            self._server.close()
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass


def _start_forwarder(connection: ConnectionResult) -> ParamikoTunnel:
    from backend.services.ssh_client import resolve_ssh_host

    ssh_host = resolve_ssh_host(connection.ssh_host, connection.host)
    remote_host = _remote_bind_host(connection)
    return ParamikoTunnel(
        ssh_host=ssh_host,
        ssh_port=connection.ssh_port,
        ssh_username=connection.linux_username,
        ssh_password=connection.linux_password,
        remote_host=remote_host,
        remote_port=connection.port,
    )


def _tunneled_connection(connection: ConnectionResult, local_port: int) -> ConnectionResult:
    return replace(
        connection,
        host="127.0.0.1",
        port=local_port,
        display_host=connection.endpoint_host,
        display_port=connection.endpoint_port,
    )


def open_ssh_tunnel(connection: ConnectionResult) -> ConnectionResult:
    """Start a persistent SSH tunnel for the desktop app session.

    If SSH is down but the DB port is open, fall back to a direct connection.
    """
    global _active_forwarder
    close_ssh_tunnel()
    if not connection.ssh_enabled or not connection.linux_username:
        return connection
    try:
        _active_forwarder = _start_forwarder(connection)
    except RuntimeError as exc:
        detail = str(exc)
        if probe_tcp(connection.host, connection.port, timeout=2.5) is None and (
            "SSH connection failed" in detail
            or "timed out" in detail.lower()
            or "connection refused" in detail.lower()
        ):
            return connection
        raise
    return _tunneled_connection(connection, _active_forwarder.local_bind_port)


def resolve_active_connection(connection: ConnectionResult) -> ConnectionResult:
    """Use the open SSH tunnel endpoint for DB operations when one is active."""
    global _active_forwarder
    if (
        _active_forwarder is not None
        and connection.ssh_enabled
        and connection.linux_username
    ):
        return _tunneled_connection(connection, _active_forwarder.local_bind_port)
    return connection


def close_ssh_tunnel() -> None:
    global _active_forwarder
    if _active_forwarder is not None:
        _active_forwarder.stop()
        _active_forwarder = None


@contextmanager
def ssh_tunnel_session(connection: ConnectionResult):
    """Temporary SSH tunnel for connection tests."""
    if not connection.ssh_enabled or not connection.linux_username:
        yield connection
        return
    forwarder = _start_forwarder(connection)
    try:
        yield _tunneled_connection(connection, forwarder.local_bind_port)
    finally:
        forwarder.stop()
