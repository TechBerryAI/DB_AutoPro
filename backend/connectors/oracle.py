"""Oracle database connector."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class OracleConnector(BaseConnector):
    """Oracle connector using oracledb."""

    db_type = DatabaseType.ORACLE

    def _connect(self) -> Any:
        try:
            import oracledb

            dsn = oracledb.makedsn(
                self.config.host, self.config.port, service_name=self.config.database
            )
            return oracledb.connect(
                user=self.config.username,
                password=self.config.password,
                dsn=dsn,
            )
        except ImportError as exc:
            raise ConnectorError("oracledb not installed") from exc
        except Exception as exc:
            raise ConnectorError(f"Oracle connection failed: {exc}") from exc

    def test_connection(self) -> bool:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")
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
        rows = self.execute_query("SELECT BANNER AS version FROM v$version WHERE ROWNUM = 1")
        return rows[0]["version"] if rows else "unknown"
