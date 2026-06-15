"""Structured logging setup with audit and application log streams."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import get_settings


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data  # type: ignore[attr-defined]
        return json.dumps(log_data)


class AuditLogger:
    """Dedicated audit log writer for compliance tracking."""

    def __init__(self) -> None:
        settings = get_settings()
        self._logger = logging.getLogger("dap.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        log_path = Path(settings.logging.audit_log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._logger.handlers:
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(JSONFormatter())
            self._logger.addHandler(handler)

    def log(
        self,
        action: str,
        user: str,
        resource: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record an audit event."""
        record = logging.LogRecord(
            name="dap.audit",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f"{action} on {resource} by {user}: {status}",
            args=(),
            exc_info=None,
        )
        record.extra_data = {  # type: ignore[attr-defined]
            "action": action,
            "user": user,
            "resource": resource,
            "status": status,
            "details": details or {},
        }
        self._logger.handle(record)


_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return singleton audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def setup_logging(level: str | None = None) -> None:
    """Configure application-wide logging."""
    settings = get_settings()
    log_level = level or settings.logging.level

    root = logging.getLogger("dap")
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if root.handlers:
        return

    console = logging.StreamHandler(sys.stdout)
    if settings.logging.format == "json":
        console.setFormatter(JSONFormatter())
    else:
        console.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(console)

    app_log_path = Path(settings.logging.app_log_path)
    app_log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(app_log_path, encoding="utf-8")
    file_handler.setFormatter(JSONFormatter())
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger under the dap namespace."""
    return logging.getLogger(f"dap.{name}")
