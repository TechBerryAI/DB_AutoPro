"""Microsoft SQL Server database connector."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class MSSQLConnector(BaseConnector):
    """MSSQL connector using pyodbc."""

    db_type = DatabaseType.MSSQL

    def _connect(self) -> Any:
        try:
            import pyodbc

            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={self.config.host},{self.config.port};"
                f"DATABASE={self.config.database};"
                f"UID={self.config.username};"
                f"PWD={self.config.password}"
            )
            return pyodbc.connect(conn_str, timeout=10)
        except ImportError as exc:
            raise ConnectorError("pyodbc not installed") from exc
        except Exception as exc:
            raise ConnectorError(f"MSSQL connection failed: {exc}") from exc

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
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            if not cursor.description:
                return []
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def execute_script(self, script: str) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(script)
            conn.commit()
            cursor.close()
        except Exception as exc:
            conn.rollback()
            raise ConnectorError(f"Script execution failed: {exc}") from exc
        finally:
            conn.close()

    def get_version(self) -> str:
        rows = self.execute_query("SELECT @@VERSION AS version")
        return rows[0]["version"] if rows else "unknown"
