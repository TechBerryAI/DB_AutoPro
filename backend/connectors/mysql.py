"""MySQL database connector."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class MySQLConnector(BaseConnector):
    """MySQL connector using mysql-connector-python."""

    db_type = DatabaseType.MYSQL

    @contextmanager
    def reused_connection(self) -> Iterator[None]:
        """Run many reads on one connection. Each query otherwise opens a new one."""
        conn = self._connect()
        self._held_conn = conn
        try:
            yield
        finally:
            self._held_conn = None
            try:
                conn.close()
            except Exception:
                pass

    def _connect(self) -> Any:
        try:
            import mysql.connector

            kwargs: dict[str, Any] = {
                "host": self.config.host,
                "port": self.config.port,
                "user": self.config.username,
                "password": self.config.password,
                "connection_timeout": (self.config.extra_options or {}).get(
                    "connect_timeout", 5
                ),
            }
            # Omit database for server-level ops (SHOW DATABASES / SCHEMATA).
            if self.config.database:
                kwargs["database"] = self.config.database
            return mysql.connector.connect(**kwargs)
        except ImportError as exc:
            raise ConnectorError("mysql-connector-python not installed") from exc
        except Exception as exc:
            raise ConnectorError(f"MySQL connection failed: {exc}") from exc

    def test_connection(self) -> bool:
        held = getattr(self, "_held_conn", None)
        own = held is None
        conn = held if held is not None else self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            return result[0] == 1
        finally:
            if own:
                conn.close()

    def execute_query(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        held = getattr(self, "_held_conn", None)
        own = held is None
        conn = held if held is not None else self._connect()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(query, params or ())
                rows = cursor.fetchall() if cursor.description else []
                return list(rows)
            finally:
                cursor.close()
        finally:
            if own:
                conn.close()

    def execute_script(self, script: str) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            for statement in script.split(";"):
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
            conn.commit()
            cursor.close()
        except Exception as exc:
            conn.rollback()
            raise ConnectorError(f"Script execution failed: {exc}") from exc
        finally:
            conn.close()

    def open_connection(self) -> Any:
        """Open one MySQL connection with autocommit off. The caller closes it."""
        connection = self._connect()
        try:
            connection.autocommit = False
        except Exception:
            connection.close()
            raise
        return connection

    def get_version(self) -> str:
        rows = self.execute_query("SELECT VERSION() AS version")
        return rows[0]["version"] if rows else "unknown"
