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
        "connections_num": 0,
        "slow_num": 0,
        "replication_sec": 0.0,
        "max_connections": None,
        "uptime": "—",
        "conn_distribution": {"active": 0, "idle": 0, "blocked": 0, "waiting": 0},
        "slow_query_rows": [],
        "load_pct": 0.0,
        "throughput": 0.0,
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
    collector_ok = False
    if collector:
        try:
            details = collector(connector)
            collector_ok = True
        except Exception:
            pass

    connected = collector_ok or latency_ms is not None
    if latency_ms is None and connected:
        try:
            start = time.perf_counter()
            if connector.test_connection():
                latency_ms = round((time.perf_counter() - start) * 1000, 1)
        except Exception:
            pass

    return {
        "connected": connected,
        "latency_ms": latency_ms,
        "latency": _format_latency(latency_ms),
        "connections": details.get("connections", "0"),
        "slow_queries": details.get("slow_queries", "0"),
        "replication_lag": details.get("replication_lag", "N/A"),
        "version": version,
        **_extended_metrics(connector, connection.db_type, details),
    }


def _parse_replication_seconds(text: str) -> float:
    text_l = text.lower()
    if "lag" in text_l:
        parts = text.split()
        for p in parts:
            try:
                return float(p)
            except ValueError:
                continue
    return 0.0


def _postgres_extended(connector: BaseConnector) -> dict[str, Any]:
    max_conn = _scalar(
        _safe_query(
            connector,
            "SELECT setting::int AS val FROM pg_settings WHERE name = 'max_connections'",
        ),
        "val",
    )
    uptime = _scalar(
        _safe_query(
            connector,
            "SELECT to_char(now() - pg_postmaster_start_time(), 'DD\"d \"HH24\"h \"MI\"m\"') AS val",
        ),
        "val",
    )
    states: dict[str, int] = {"active": 0, "idle": 0, "blocked": 0, "waiting": 0}
    for row in _safe_query(
        connector,
        "SELECT state, count(*)::int AS cnt FROM pg_stat_activity "
        "WHERE datname = current_database() GROUP BY state",
    ):
        state = str(row.get("state", "")).lower()
        cnt = int(row.get("cnt") or 0)
        if state == "active":
            states["active"] += cnt
        elif state in ("idle", "idle in transaction"):
            states["idle"] += cnt
        elif "wait" in state:
            states["waiting"] += cnt
        else:
            states["blocked"] += cnt

    slow_rows: list[dict[str, Any]] = []
    for row in _safe_query(
        connector,
        "SELECT LEFT(query, 72) AS query, "
        "GREATEST(1, EXTRACT(EPOCH FROM (now() - query_start)) * 1000)::int AS elapsed_ms, "
        "1 AS cnt FROM pg_stat_activity "
        "WHERE state = 'active' AND query NOT ILIKE '%pg_stat_activity%' "
        "AND now() - query_start > interval '1 second' "
        "ORDER BY query_start LIMIT 5",
    ):
        if row.get("query"):
            slow_rows.append({
                "query": str(row["query"]).strip(),
                "avg_ms": int(row.get("elapsed_ms") or 0),
                "count": int(row.get("cnt") or 1),
            })

    slow_count = int(
        _scalar(
            _safe_query(
                connector,
                "SELECT count(*)::int AS cnt FROM pg_stat_activity "
                "WHERE state = 'active' "
                "AND query NOT ILIKE '%pg_stat_activity%' "
                "AND now() - query_start > interval '1 second'",
            ),
            "cnt",
        )
        or 0
    )

    tps = _scalar(
        _safe_query(
            connector,
            "SELECT sum(xact_commit + xact_rollback)::float AS val FROM pg_stat_database "
            "WHERE datname = current_database()",
        ),
        "val",
    )

    connections = states["active"] + states["idle"] + states["blocked"] + states["waiting"]
    load_pct = (connections / max_conn * 100) if max_conn else 0.0

    return {
        "connections_num": connections,
        "slow_num": slow_count,
        "replication_sec": 0.0,
        "max_connections": int(max_conn or 0),
        "uptime": str(uptime or "—"),
        "conn_distribution": states,
        "slow_query_rows": slow_rows,
        "load_pct": round(min(100.0, load_pct), 1),
        "throughput": float(tps or 0),
        "throughput_label": "Total transactions",
    }


def _mysql_extended(connector: BaseConnector, details: dict[str, str]) -> dict[str, Any]:
    max_conn = 0
    uptime = "—"
    throughput = 0.0
    for row in _safe_query(connector, "SHOW VARIABLES LIKE 'max_connections'"):
        try:
            max_conn = int(row.get("Value") or row.get("value") or 0)
        except (TypeError, ValueError):
            pass

    for row in _safe_query(connector, "SHOW GLOBAL STATUS LIKE 'Uptime'"):
        try:
            seconds = int(row.get("Value") or row.get("value") or 0)
            days, rem = divmod(seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            uptime = f"{days}d {hours}h {minutes}m"
        except (TypeError, ValueError):
            pass

    for row in _safe_query(
        connector,
        "SHOW GLOBAL STATUS WHERE Variable_name IN ('Questions', 'Queries')",
    ):
        name = str(row.get("Variable_name", ""))
        if name in ("Questions", "Queries"):
            try:
                throughput = float(row.get("Value") or 0)
            except (TypeError, ValueError):
                pass

    connections = int(details.get("connections", "0") or 0)
    slow_num = int(details.get("slow_queries", "0") or 0)
    load_pct = (connections / max_conn * 100) if max_conn else 0.0

    idle = int(
        _scalar(
            _safe_query(
                connector,
                "SELECT COUNT(*) AS cnt FROM information_schema.PROCESSLIST "
                "WHERE COMMAND = 'Sleep'",
            ),
            "cnt",
        )
        or 0
    )
    active = int(
        _scalar(
            _safe_query(
                connector,
                "SELECT COUNT(*) AS cnt FROM information_schema.PROCESSLIST "
                "WHERE COMMAND != 'Sleep'",
            ),
            "cnt",
        )
        or max(0, connections - idle)
    )

    slow_rows: list[dict[str, Any]] = []
    for row in _safe_query(
        connector,
        "SELECT LEFT(INFO, 72) AS query, TIME * 1000 AS elapsed_ms, 1 AS cnt "
        "FROM information_schema.PROCESSLIST "
        "WHERE COMMAND != 'Sleep' AND INFO IS NOT NULL AND INFO != '' AND TIME >= 1 "
        "ORDER BY TIME DESC LIMIT 5",
    ):
        if row.get("query"):
            slow_rows.append({
                "query": str(row["query"]).strip(),
                "avg_ms": int(float(row.get("elapsed_ms") or 0)),
                "count": 1,
            })

    return {
        "connections_num": connections,
        "slow_num": slow_num,
        "replication_sec": _parse_replication_seconds(details.get("replication_lag", "")),
        "max_connections": max_conn,
        "uptime": uptime,
        "conn_distribution": {
            "active": active,
            "idle": idle,
            "blocked": 0,
            "waiting": 0,
        },
        "slow_query_rows": slow_rows,
        "load_pct": round(min(100.0, load_pct), 1),
        "throughput": throughput,
        "throughput_label": "Total queries",
    }


def _generic_extended(details: dict[str, str]) -> dict[str, Any]:
    connections = int(details.get("connections", "0") or 0)
    slow_num = int(details.get("slow_queries", "0") or 0)
    return {
        "connections_num": connections,
        "slow_num": slow_num,
        "replication_sec": _parse_replication_seconds(details.get("replication_lag", "")),
        "max_connections": None,
        "uptime": "—",
        "conn_distribution": {"active": connections, "idle": 0, "blocked": 0, "waiting": 0},
        "slow_query_rows": [],
        "load_pct": 0.0,
        "throughput": 0.0,
        "throughput_label": "Activity",
    }


def _extended_metrics(
    connector: BaseConnector,
    db_type: DatabaseType,
    details: dict[str, str],
) -> dict[str, Any]:
    try:
        if db_type == DatabaseType.POSTGRESQL:
            ext = _postgres_extended(connector)
            ext["replication_sec"] = _parse_replication_seconds(details.get("replication_lag", ""))
            return ext
        if db_type == DatabaseType.MYSQL:
            return _mysql_extended(connector, details)
    except Exception:
        pass
    ext = _generic_extended(details)
    if db_type == DatabaseType.MONGODB:
        ext["conn_distribution"] = {
            "active": ext["connections_num"],
            "idle": 0,
            "blocked": 0,
            "waiting": 0,
        }
    return ext
