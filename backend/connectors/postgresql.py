"""PostgreSQL database connector."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import BaseConnector, ConnectionInfo
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class PostgreSQLConnector(BaseConnector):
    """PostgreSQL connector using psycopg2."""

    db_type = DatabaseType.POSTGRESQL

    def _connect(self) -> Any:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            timeout = (self.config.extra_options or {}).get("connect_timeout", 5)
            ssl_modes: list[str] = []
            if self.config.ssl_mode:
                ssl_modes.append(self.config.ssl_mode)
            for mode in ("prefer", "require", "allow", "disable"):
                if mode not in ssl_modes:
                    ssl_modes.append(mode)

            tried: set[str] = set()
            last_error: Exception | None = None

            for sslmode in ssl_modes:
                if sslmode in tried:
                    continue
                tried.add(sslmode)
                try:
                    conn = psycopg2.connect(
                        host=self.config.host,
                        port=self.config.port,
                        dbname=self.config.database,
                        user=self.config.username,
                        password=self.config.password,
                        sslmode=sslmode,
                        connect_timeout=timeout,
                    )
                    return conn, RealDictCursor
                except Exception as exc:
                    last_error = exc

            raise ConnectorError(f"PostgreSQL connection failed: {last_error}") from last_error
        except ImportError as exc:
            raise ConnectorError("psycopg2 not installed", {"error": str(exc)}) from exc
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"PostgreSQL connection failed: {exc}") from exc

    def test_connection(self) -> bool:
        conn, _ = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1
        finally:
            conn.close()

    def execute_query(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        conn, cursor_factory = self._connect()
        try:
            with conn.cursor(cursor_factory=cursor_factory) as cur:
                cur.execute(query, params)
                if cur.description:
                    return [dict(row) for row in cur.fetchall()]
                return []
        finally:
            conn.close()

    def execute_script(self, script: str) -> None:
        conn, _ = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(script)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise ConnectorError(f"Script execution failed: {exc}") from exc
        finally:
            conn.close()

    def get_version(self) -> str:
        rows = self.execute_query("SELECT version() AS version")
        return rows[0]["version"] if rows else "unknown"
