"""Live database performance metrics for the desktop dashboard."""

from __future__ import annotations

import time
from typing import Any

from backend.connectors.base import BaseConnector, get_connector_from_info
from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult, apply_connection


def _empty_metrics(*, connected: bool = False) -> dict[str, Any]:
    return {
        "connected": connected,
        "latency_ms": None,
        "latency": "Not connected",
        "connections": "Offline",
        "slow_queries": "—",
        "replication_lag": "N/A",
        "version": "",
    }


def _scalar(rows: list[dict[str, Any]], key: str | None = None) -> Any:
    if not rows:
        return None
    row = rows[0]
    if key is not None:
        return row.get(key)
    return next(iter(row.values()), None)


def _safe_query(connector: BaseConnector, query: str) -> list[dict[str, Any]]:
    try:
        return connector.execute_query(query)
    except Exception:
        return []


def _measure_latency_ms(connector: BaseConnector) -> float | None:
    try:
        start = time.perf_counter()
        if not connector.test_connection():
            return None
        return round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return None


def _format_latency(ms: float | None) -> str:
    if ms is None:
        return "Unavailable"
    if ms < 1:
        return "< 1 ms"
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    return f"{ms:.0f} ms"


def _postgres_metrics(connector: BaseConnector) -> dict[str, str]:
    connections = _scalar(
        _safe_query(
            connector,
            "SELECT count(*)::int AS cnt FROM pg_stat_activity "
            "WHERE datname = current_database()",
        ),
        "cnt",
    )
    slow = _scalar(
        _safe_query(
            connector,
            "SELECT count(*)::int AS cnt FROM pg_stat_activity "
            "WHERE state = 'active' "
            "AND query NOT ILIKE '%pg_stat_activity%' "
            "AND now() - query_start > interval '1 second'",
        ),
        "cnt",
    )
    lag_row = _safe_query(
        connector,
        "SELECT pg_is_in_recovery() AS replica, "
        "CASE WHEN pg_is_in_recovery() THEN "
        "  EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())) "
        "ELSE NULL END AS lag_seconds",
    )
    replication = "Primary"
    if lag_row:
        if lag_row[0].get("replica"):
            lag = lag_row[0].get("lag_seconds")
            replication = f"{float(lag or 0):.0f} s lag" if lag is not None else "Replica"
    return {
        "connections": str(int(connections or 0)),
        "slow_queries": str(int(slow or 0)),
        "replication_lag": replication,
    }


def _mysql_metrics(connector: BaseConnector) -> dict[str, str]:
    status: dict[str, str] = {}
    for row in _safe_query(
        connector,
        "SHOW GLOBAL STATUS WHERE Variable_name IN "
        "('Threads_connected', 'Slow_queries')",
    ):
        name = row.get("Variable_name")
        if name:
            status[str(name)] = str(row.get("Value", "0"))

    replication = "Primary"
    slave_rows = _safe_query(connector, "SHOW REPLICA STATUS")
    if not slave_rows:
        slave_rows = _safe_query(connector, "SHOW SLAVE STATUS")
    if slave_rows:
        lag = slave_rows[0].get("Seconds_Behind_Source")
        if lag is None:
            lag = slave_rows[0].get("Seconds_Behind_Master")
        if lag is not None:
            replication = f"{int(lag)} s lag"

    return {
        "connections": status.get("Threads_connected", "0"),
        "slow_queries": status.get("Slow_queries", "0"),
        "replication_lag": replication,
    }


def _mongodb_metrics(connector: BaseConnector) -> dict[str, str]:
    try:
        client = connector._get_client()  # noqa: SLF001 — internal client access for admin stats
    except Exception:
        return {"connections": "0", "slow_queries": "0", "replication_lag": "N/A"}

    try:
        status = client.admin.command("serverStatus")
        connections = status.get("connections", {}).get("current", 0)
        slow_ops = 0
        try:
            ops = client.admin.command({"currentOp": 1, "active": True})
            slow_ops = sum(
                1 for op in ops.get("inprog", []) if int(op.get("secs_running") or 0) >= 1
            )
        except Exception:
            slow_ops = 0

        replication = "Standalone"
        try:
            rs = client.admin.command("replSetGetStatus")
            if rs.get("members"):
                max_lag = 0.0
                for member in rs["members"]:
                    if member.get("stateStr") == "SECONDARY":
                        max_lag = max(max_lag, float(member.get("lag", 0) or 0))
                replication = f"{max_lag:.0f} s lag" if max_lag else "Replica set OK"
        except Exception:
            pass

        return {
            "connections": str(int(connections or 0)),
            "slow_queries": str(int(slow_ops)),
            "replication_lag": replication,
        }
    finally:
        client.close()


def _mssql_metrics(connector: BaseConnector) -> dict[str, str]:
    connections = _scalar(
        _safe_query(
            connector,
            "SELECT COUNT(*) AS cnt FROM sys.dm_exec_sessions WHERE is_user_process = 1",
        ),
        "cnt",
    )
    slow = _scalar(
        _safe_query(
            connector,
            "SELECT cnt = COUNT(*) FROM sys.dm_exec_requests "
            "WHERE total_elapsed_time > 1000",
        ),
        "cnt",
    )
    return {
        "connections": str(int(connections or 0)),
        "slow_queries": str(int(slow or 0)),
        "replication_lag": "N/A",
    }


def _oracle_metrics(connector: BaseConnector) -> dict[str, str]:
    connections = _scalar(
        _safe_query(
            connector,
            "SELECT COUNT(*) AS cnt FROM v$session WHERE type = 'USER'",
        ),
        "cnt",
    )
    slow = _scalar(
        _safe_query(
            connector,
            "SELECT COUNT(*) AS cnt FROM v$session "
            "WHERE status = 'ACTIVE' AND type = 'USER'",
        ),
        "cnt",
    )
    return {
        "connections": str(int(connections or 0)),
        "slow_queries": str(int(slow or 0)),
        "replication_lag": "N/A",
    }


_COLLECTORS = {
    DatabaseType.POSTGRESQL: _postgres_metrics,
    DatabaseType.MYSQL: _mysql_metrics,
    DatabaseType.MONGODB: _mongodb_metrics,
    DatabaseType.MSSQL: _mssql_metrics,
    DatabaseType.ORACLE: _oracle_metrics,
}


def fetch_database_performance(connection: ConnectionResult | None) -> dict[str, Any]:
    """Collect live DB metrics. Never raises; always returns display-ready values."""
    if connection is None:
        return _empty_metrics(connected=False)

    try:
        apply_connection(connection)
        connector = get_connector_from_info(connection.db_type, connection.to_connection_info())
    except Exception:
        return _empty_metrics(connected=False)

    latency_ms = _measure_latency_ms(connector)
    version = ""
    try:
        version = connector.get_version() or ""
    except Exception:
        pass

    collector = _COLLECTORS.get(connection.db_type)
    details = {"connections": "0", "slow_queries": "0", "replication_lag": "N/A"}
    if collector:
        try:
            details = collector(connector)
        except Exception:
            pass

    return {
        "connected": latency_ms is not None,
        "latency_ms": latency_ms,
        "latency": _format_latency(latency_ms),
        "connections": details.get("connections", "0"),
        "slow_queries": details.get("slow_queries", "0"),
        "replication_lag": details.get("replication_lag", "N/A"),
        "version": version,
    }
