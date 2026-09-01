"""Oracle database connector."""

from __future__ import annotations

from typing import Any

from backend.connectors.base import BaseConnector
from backend.core.exceptions import ConnectorError
from backend.models.schemas import DatabaseType


class OracleConnector(BaseConnector):
    """Oracle connector using oracledb."""

    db_type = DatabaseType.ORACLE

    def _format_connect_error(self, detail: str, user: str, host: str, port: int) -> str:
        opts = self.config.extra_options or {}
        display_host = str(opts.get("db_host") or host).strip()
        display_port = int(opts.get("db_port") or port)
        endpoint = f"{display_host}:{display_port}"
        low = detail.lower()
        service = (self.config.database or "").strip()

        if "dpy-4011" in low or "forcibly closed" in low or "10054" in low:
            msg = (
                f"Oracle closed the connection to {endpoint} (service {service}). "
                "Use the schema user in DB username (e.g. BANKING, SYSTEM), "
                "not the Linux/SSH user."
            )
            if user.lower() in {"oracle", "grid", "root", "postgres", "mongodb", "user"}:
                msg += f" '{user}' is not a valid Oracle DB login on this server."
            return msg
        if "ora-01017" in low or "invalid username" in low:
            return (
                f"Oracle login failed for user '{user}' on {endpoint} "
                f"(service {service}). Check DB username/password."
            )
        if "ora-12514" in low or "listener does not currently know" in low:
            return (
                f"Oracle service '{service}' was not found on {endpoint}. "
                "Check Database / Service (e.g. orclpdb1)."
            )
        return f"Oracle connection failed ({endpoint}): {detail}"

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
            raise ConnectorError(
                self._format_connect_error(
                    str(exc),
                    self.config.username,
                    self.config.host,
                    self.config.port,
                )
            ) from exc

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
            # Oracle drivers often return uppercase column names even with aliases.
            columns = [str(col[0]).lower() for col in cursor.description]
            rows: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                item: dict[str, Any] = {}
                for idx, col in enumerate(columns):
                    value = row[idx]
                    if value is not None and hasattr(value, "read"):
                        value = value.read()
                    item[col] = value
                rows.append(item)
            return rows
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

    def open_session(self) -> Any:
        """Return a live connection the caller must close."""
        return self._connect()

    @staticmethod
    def _fetch_dicts(
        conn: Any,
        setup_statements: list[str],
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = conn.cursor()
        try:
            for statement in setup_statements:
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
            cursor.execute(query, params or ())
            if not cursor.description:
                return []
            columns = [str(col[0]).lower() for col in cursor.description]
            rows: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                item: dict[str, Any] = {}
                for idx, col in enumerate(columns):
                    value = row[idx]
                    if value is not None and hasattr(value, "read"):
                        value = value.read()
                    item[col] = value
                rows.append(item)
            return rows
        finally:
            cursor.close()

    def execute_with_setup(
        self,
        setup_statements: list[str],
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Run setup DDL/DCL on the same session before a query (e.g. ALTER SESSION SET CONTAINER)."""
        conn = self._connect()
        try:
            return self._fetch_dicts(conn, setup_statements, query, params)
        finally:
            conn.close()

    def get_version(self) -> str:
        rows = self.execute_query(
            "SELECT BANNER AS version FROM v$version WHERE ROWNUM = 1"
        )
        if not rows:
            return "unknown"
        row = rows[0]
        for key in ("version", "banner"):
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        first = next(iter(row.values()), None)
        return str(first).strip() if first is not None else "unknown"
