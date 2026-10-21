"""PostgreSQL database connector."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from backend.connectors.base import BaseConnector, ConnectionInfo
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class PostgreSQLConnector(BaseConnector):
    """PostgreSQL connector using psycopg2."""

    db_type = DatabaseType.POSTGRESQL

    @contextmanager
    def reused_connection(self) -> Iterator[None]:
        """Run many reads on one connection. Each query otherwise opens a new one."""
        conn, factory = self._connect()
        try:
            conn.autocommit = True
        except Exception:
            pass
        self._held_conn = conn
        self._held_factory = factory
        try:
            yield
        finally:
            self._held_conn = None
            self._held_factory = None
            try:
                conn.close()
            except Exception:
                pass

    def _connect(self) -> Any:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            timeout = (self.config.extra_options or {}).get("connect_timeout", 5)
            requested = (self.config.ssl_mode or "").strip().lower()
            ssl_modes: list[str] = []
            if requested:
                ssl_modes.append(requested)
                if requested == "prefer":
                    ssl_modes.append("require")
            else:
                ssl_modes.extend(("prefer", "require", "allow", "disable"))

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
        held = getattr(self, "_held_conn", None)
        if held is not None:
            with held.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                value = next(iter(row.values())) if isinstance(row, dict) else row[0]
                return value == 1
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
        held = getattr(self, "_held_conn", None)
        if held is not None:
            try:
                with held.cursor(cursor_factory=self._held_factory) as cur:
                    cur.execute(query, params)
                    if cur.description:
                        return [dict(row) for row in cur.fetchall()]
                    return []
            except Exception:
                try:
                    held.rollback()
                except Exception:
                    pass
                raise
        conn, cursor_factory = self._connect()
        try:
            with conn.cursor(cursor_factory=cursor_factory) as cur:
                cur.execute(query, params)
                if cur.description:
                    return [dict(row) for row in cur.fetchall()]
                return []
        finally:
            conn.close()

    def open_connection(self) -> Any:
        """Open one PostgreSQL connection. The caller closes it."""
        conn, _factory = self._connect()
        return conn

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
