"""Execute SQL or MongoDB shell-style commands safely from the classic workspace."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from backend.connectors.base import get_connector_from_info
from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult, apply_connection

_SQL_WRITE = re.compile(
    r"^\s*(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|replace|call|exec)\b",
    re.IGNORECASE,
)


@dataclass
class QueryOutcome:
    ok: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    message: str = ""
    elapsed_ms: float = 0.0
    affected: int | None = None
    language: str = "sql"


def is_probably_write_sql(text: str) -> bool:
    first = (text or "").strip().split(";", 1)[0]
    return bool(_SQL_WRITE.match(first))


def run_query(connection: ConnectionResult, text: str) -> QueryOutcome:
    """Run editor text against the active connection using the correct language."""
    script = (text or "").strip()
    if not script:
        return QueryOutcome(ok=False, message="Nothing to execute.", language="none")

    apply_connection(connection)
    if connection.db_type == DatabaseType.MONGODB:
        return _run_mongo(connection, script)
    return _run_sql(connection, script)


def _run_sql(connection: ConnectionResult, script: str) -> QueryOutcome:
    connector = get_connector_from_info(connection.db_type, connection.to_connection_info())
    start = time.perf_counter()
    try:
        # Prefer returning rows for SELECT / WITH / SHOW / DESCRIBE / EXPLAIN.
        head = script.lstrip().split(None, 1)[0].lower() if script.lstrip() else ""
        if head in {"select", "with", "show", "describe", "desc", "explain", "pragma"}:
            rows = connector.execute_query(script)
            elapsed = (time.perf_counter() - start) * 1000
            columns = list(rows[0].keys()) if rows else []
            return QueryOutcome(
                ok=True,
                rows=rows,
                columns=columns,
                message=f"Query returned {len(rows)} row(s).",
                elapsed_ms=round(elapsed, 1),
                language="sql",
            )
        connector.execute_script(script)
        elapsed = (time.perf_counter() - start) * 1000
        return QueryOutcome(
            ok=True,
            message="Script executed successfully.",
            elapsed_ms=round(elapsed, 1),
            language="sql",
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return QueryOutcome(
            ok=False,
            message=str(exc),
            elapsed_ms=round(elapsed, 1),
            language="sql",
        )


def _run_mongo(connection: ConnectionResult, script: str) -> QueryOutcome:
    """Limited MongoDB shell-style command runner (never treats input as SQL)."""
    if re.search(r"\b(select|insert|update|delete|drop|alter|create)\b", script, re.I) and "db." not in script:
        return QueryOutcome(
            ok=False,
            message="MongoDB does not accept SQL. Use shell-style commands (e.g. show dbs, db.coll.find()).",
            language="mongo",
        )

    connector = get_connector_from_info(connection.db_type, connection.to_connection_info())
    client = connector._get_client()  # noqa: SLF001
    start = time.perf_counter()
    try:
        text = script.strip().rstrip(";")
        lower = text.lower()

        if lower in {"show dbs", "show databases"}:
            rows = [{"name": n} for n in client.list_database_names()]
            return _mongo_ok(rows, start, "Listed databases.")

        db_name = connection.database or "admin"
        db_match = re.match(r"^use\s+([A-Za-z0-9_\-]+)$", text, re.I)
        if db_match:
            return QueryOutcome(
                ok=True,
                message=f"Database context noted: {db_match.group(1)} (session database is managed by the connection profile).",
                elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
                language="mongo",
            )

        if lower in {"show collections", "show tables"}:
            db = client[db_name]
            rows = [{"name": n} for n in sorted(db.list_collection_names())]
            return _mongo_ok(rows, start, f"Collections in '{db_name}'.")

        if lower in {"db", "db.getname()"}:
            return _mongo_ok([{"database": db_name}], start, "Current database.")

        # db.runCommand({...})
        cmd_match = re.match(r"^db\.runcommand\((.+)\)$", text, re.I | re.S)
        if cmd_match:
            payload = _parse_js_object(cmd_match.group(1))
            result = client[db_name].command(payload)
            rows = [_flatten_mongo(result)]
            return _mongo_ok(rows, start, "Command completed.")

        # db.stats()
        if re.match(r"^db\.stats\(\)$", text, re.I):
            result = client[db_name].command("dbStats")
            return _mongo_ok([_flatten_mongo(result)], start, "dbStats.")

        # db.<coll>.stats()
        stats_match = re.match(r"^db\.([A-Za-z0-9_\-]+)\.stats\(\)$", text, re.I)
        if stats_match:
            coll = stats_match.group(1)
            result = client[db_name].command("collStats", coll)
            return _mongo_ok([_flatten_mongo(result)], start, f"collStats for {coll}.")

        # db.<coll>.countDocuments({}) / estimatedDocumentCount()
        count_match = re.match(
            r"^db\.([A-Za-z0-9_\-]+)\.(countDocuments|estimatedDocumentCount)\((.*)\)$",
            text,
            re.I | re.S,
        )
        if count_match:
            coll = count_match.group(1)
            method = count_match.group(2)
            if method.lower() == "estimateddocumentcount":
                n = client[db_name][coll].estimated_document_count()
            else:
                filt = _parse_js_object(count_match.group(3) or "{}")
                n = client[db_name][coll].count_documents(filt)
            return _mongo_ok([{"collection": coll, "count": n}], start, "Count completed.")

        # db.<coll>.find(...).limit(n)  or  db.<coll>.find(...)
        find_match = re.match(
            r"^db\.([A-Za-z0-9_\-]+)\.find\((.*)\)(?:\.limit\((\d+)\))?$",
            text,
            re.I | re.S,
        )
        if find_match:
            coll = find_match.group(1)
            filt_src = (find_match.group(2) or "").strip() or "{}"
            limit = int(find_match.group(3) or 50)
            filt = _parse_js_object(filt_src)
            cursor = client[db_name][coll].find(filt).limit(min(limit, 500))
            rows = [_flatten_mongo(doc) for doc in cursor]
            return _mongo_ok(rows, start, f"find() returned {len(rows)} document(s).")

        # db.<coll>.aggregate([...])
        agg_match = re.match(r"^db\.([A-Za-z0-9_\-]+)\.aggregate\((.+)\)$", text, re.I | re.S)
        if agg_match:
            coll = agg_match.group(1)
            pipeline = _parse_js_object(agg_match.group(2))
            if not isinstance(pipeline, list):
                raise ValueError("aggregate() expects a pipeline array")
            rows = [_flatten_mongo(doc) for doc in client[db_name][coll].aggregate(pipeline)]
            return _mongo_ok(rows[:500], start, f"aggregate() returned {len(rows)} document(s).")

        return QueryOutcome(
            ok=False,
            message=(
                "Unsupported MongoDB command. Supported examples:\n"
                "  show dbs\n"
                "  show collections\n"
                "  db.stats()\n"
                "  db.<collection>.find({}).limit(20)\n"
                "  db.<collection>.stats()\n"
                "  db.runCommand({ping: 1})"
            ),
            language="mongo",
        )
    except Exception as exc:
        return QueryOutcome(
            ok=False,
            message=str(exc),
            elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
            language="mongo",
        )
    finally:
        client.close()


def _mongo_ok(rows: list[dict[str, Any]], start: float, message: str) -> QueryOutcome:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return QueryOutcome(
        ok=True,
        rows=rows,
        columns=columns,
        message=message,
        elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
        language="mongo",
    )


def _parse_js_object(src: str) -> Any:
    text = (src or "").strip()
    if not text:
        return {}
    # Allow bare JS-ish literals by normalizing to JSON.
    normalized = text
    normalized = re.sub(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', normalized)
    normalized = normalized.replace("'", '"')
    normalized = re.sub(r"\bNone\b", "null", normalized)
    normalized = re.sub(r"\bTrue\b", "true", normalized)
    normalized = re.sub(r"\bFalse\b", "false", normalized)
    return json.loads(normalized)


def _flatten_mongo(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {"value": str(doc)}
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["_id"] = str(value)
        elif isinstance(value, (dict, list)):
            out[key] = json.dumps(value, default=str)[:200]
        else:
            out[key] = value
    return out
