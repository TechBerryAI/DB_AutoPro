"""MySQL database connector."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class MySQLConnector(BaseConnector):
    """MySQL connector using mysql-connector-python."""

    db_type = DatabaseType.MYSQL

    def _connect(self) -> Any:
        try:
            import mysql.connector

            return mysql.connector.connect(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.username,
                password=self.config.password,
            )
        except ImportError as exc:
            raise ConnectorError("mysql-connector-python not installed") from exc
        except Exception as exc:
            raise ConnectorError(f"MySQL connection failed: {exc}") from exc

    def test_connection(self) -> bool:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            return result[0] == 1
        finally:
            conn.close()

    def execute_query(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, params or ())
            rows = cursor.fetchall() if cursor.description else []
            cursor.close()
            return list(rows)
        finally:
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

    def get_version(self) -> str:
        rows = self.execute_query("SELECT VERSION() AS version")
        return rows[0]["version"] if rows else "unknown"
