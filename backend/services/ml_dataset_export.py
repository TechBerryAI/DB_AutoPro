"""Export a unified CSV dataset for ML from logs, audit, health checks, and metrics."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import get_settings
from backend.services.monitoring_service import MonitoringService

CSV_COLUMNS = [
    "event_type",
    "timestamp",
    "level",
    "status",
    "service",
    "action",
    "user",
    "resource",
    "message",
    "latency_ms",
    "db_type",
    "method",
    "category",
    "remote",
    "size_bytes",
    "error",
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "platform",
    "environment",
    "total_backups",
    "storage_used_mb",
    "patch_id",
    "sql_file",
    "details_json",
]


def _blank_row() -> dict[str, Any]:
    return {col: "" for col in CSV_COLUMNS}


def _row(**kwargs: Any) -> dict[str, Any]:
    row = _blank_row()
    for key, value in kwargs.items():
        if key in row and value is not None and value != "":
            row[key] = value
    return row


def _parse_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _audit_rows(audit_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in _parse_json_lines(audit_path):
        data = rec.get("data") or {}
        details = data.get("details") or {}
        rows.append(
            _row(
                event_type="audit",
                timestamp=rec.get("timestamp", ""),
                level=rec.get("level", ""),
                status=data.get("status", ""),
                service="audit",
                action=data.get("action", ""),
                user=data.get("user", ""),
                resource=data.get("resource", ""),
                message=rec.get("message", ""),
                db_type=details.get("db_type", ""),
                method=details.get("method", ""),
                category=details.get("category", ""),
                remote=details.get("remote", ""),
                size_bytes=details.get("size_bytes", ""),
                error=details.get("error", ""),
                details_json=json.dumps(details, ensure_ascii=False) if details else "",
            )
        )
    return rows


def _app_log_rows(app_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in _parse_json_lines(app_path):
        level = rec.get("level", "")
        message = rec.get("message", "")
        status = ""
        if level == "ERROR":
            status = "failed"
        elif level in ("INFO", "WARNING") and "completed" in message.lower():
            status = "success"
        rows.append(
            _row(
                event_type="app_log",
                timestamp=rec.get("timestamp", ""),
                level=level,
                status=status,
                service=rec.get("logger", ""),
                message=message,
                error=message if level == "ERROR" else "",
            )
        )
    return rows


def _health_check_rows(monitoring: MonitoringService) -> list[dict[str, Any]]:
    exported_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    try:
        checks = monitoring.health_check()
    except Exception as exc:
        return [
            _row(
                event_type="health_check",
                timestamp=exported_at,
                level="ERROR",
                status="unhealthy",
                service="health_check",
                message=f"Health check export failed: {exc}",
                error=str(exc),
            )
        ]
    for check in checks:
        details = check.details or {}
        rows.append(
            _row(
                event_type="health_check",
                timestamp=exported_at,
                level="INFO",
                status=check.status,
                service=check.service,
                message=f"Health check: {check.service} = {check.status}",
                latency_ms=check.latency_ms if check.latency_ms is not None else "",
                environment=details.get("environment", ""),
                details_json=json.dumps(details, ensure_ascii=False),
            )
        )
    return rows


def _system_metrics_row(monitoring: MonitoringService) -> dict[str, Any]:
    metrics = monitoring.get_system_metrics()
    memory = metrics.get("memory") or {}
    disk = metrics.get("disk") or {}
    return _row(
        event_type="system_metrics",
        timestamp=metrics.get("timestamp", ""),
        level="INFO",
        status="healthy",
        service="system",
        message="System resource snapshot",
        cpu_percent=metrics.get("cpu_percent", ""),
        memory_percent=memory.get("percent", ""),
        disk_percent=disk.get("percent", ""),
        platform=metrics.get("platform", ""),
        details_json=json.dumps(metrics, ensure_ascii=False),
    )


def _dashboard_stats_row(monitoring: MonitoringService, settings) -> dict[str, Any]:
    stats = monitoring.get_dashboard_stats()
    last_backup = stats.last_backup.isoformat() if stats.last_backup else ""
    return _row(
        event_type="dashboard_stats",
        timestamp=datetime.now(timezone.utc).isoformat(),
        level="INFO",
        status="healthy",
        service="dashboard",
        message="Dashboard KPI snapshot",
        environment=settings.environment,
        total_backups=stats.total_backups,
        storage_used_mb=stats.storage_used_mb,
        details_json=json.dumps(
            {
                "successful_backups": stats.successful_backups,
                "failed_backups": stats.failed_backups,
                "pending_operations": stats.pending_operations,
                "last_backup": last_backup,
                "database_connections": stats.database_connections,
                "patch_count": stats.patch_count,
            },
            ensure_ascii=False,
        ),
    )


def _backup_inventory_rows(monitoring: MonitoringService) -> list[dict[str, Any]]:
    report = monitoring.get_backup_report()
    rows: list[dict[str, Any]] = []
    for backup in report.get("backups", []):
        rows.append(
            _row(
                event_type="backup_inventory",
                timestamp=backup.get("created_at", ""),
                level="INFO",
                status="success",
                service="backup_storage",
                resource=backup.get("path", ""),
                message=f"Backup inventory: {backup.get('name', '')}",
                size_bytes=backup.get("size_bytes", ""),
                remote=backup.get("remote", ""),
                details_json=json.dumps(backup, ensure_ascii=False),
            )
        )
    return rows


def _patch_history_rows(monitoring: MonitoringService) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in monitoring.patch_service.get_history():
        executed_at = entry.executed_at
        if executed_at.tzinfo is None:
            executed_at = executed_at.replace(tzinfo=timezone.utc)
        rows.append(
            _row(
                event_type="patch_history",
                timestamp=executed_at.isoformat(),
                level="INFO",
                status=entry.status.value if hasattr(entry.status, "value") else str(entry.status),
                service="patch",
                action="patch_execute",
                user=entry.executed_by,
                resource=entry.sql_file,
                message=entry.description or entry.sql_file,
                patch_id=entry.patch_id,
                sql_file=entry.sql_file,
                details_json=json.dumps(
                    {
                        "rollback_available": entry.rollback_available,
                        "description": entry.description,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return rows


def build_ml_dataset_rows() -> list[dict[str, Any]]:
    """Collect all exportable project events into flat rows."""
    settings = get_settings()
    monitoring = MonitoringService()
    audit_path = Path(settings.logging.audit_log_path)
    app_path = Path(settings.logging.app_log_path)

    rows: list[dict[str, Any]] = []
    rows.extend(_audit_rows(audit_path))
    rows.extend(_app_log_rows(app_path))
    rows.extend(_health_check_rows(monitoring))
    rows.append(_system_metrics_row(monitoring))
    rows.append(_dashboard_stats_row(monitoring, settings))
    rows.extend(_backup_inventory_rows(monitoring))
    rows.extend(_patch_history_rows(monitoring))

    rows.sort(key=lambda r: str(r.get("timestamp", "")))
    return rows


def export_ml_dataset(output_path: Path | None = None) -> tuple[Path, list[dict[str, Any]]]:
    """Write unified ML dataset CSV. Returns (path, rows)."""
    settings = get_settings()
    out = output_path or (Path(settings.logging.audit_log_path).parent / "ml_dataset.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = build_ml_dataset_rows()
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return out, rows
