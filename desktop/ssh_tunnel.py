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
            )
        except paramiko.AuthenticationException as exc:
            raise RuntimeError(
                f"SSH authentication failed for Linux user '{ssh_username}'."
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"SSH connection failed: {exc}") from exc

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
                data = src.recv(4096)
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
        ssh_enabled=False,
    )


def open_ssh_tunnel(connection: ConnectionResult) -> ConnectionResult:
    """Start a persistent SSH tunnel for the desktop app session."""
    global _active_forwarder
    close_ssh_tunnel()
    if not connection.ssh_enabled or not connection.linux_username:
        return connection
    _active_forwarder = _start_forwarder(connection)
    return _tunneled_connection(connection, _active_forwarder.local_bind_port)


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
