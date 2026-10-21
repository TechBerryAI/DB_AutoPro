"""Read-only checks a PostgreSQL database administrator runs before a SQL patch.

The checks use the connected session. They do not install packages, change
configuration, or treat a readable backup as a proven restore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from backend.services.mysql_patch.redaction import redact_text

QueryFunc = Callable[[str], list[dict]]


class CheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class AdminCheck:
    code: str
    status: CheckStatus
    summary: str


@dataclass
class PostgresAdminReport:
    checks: list[AdminCheck] = field(default_factory=list)
    is_administrator: bool = False
    server_version: str = ""
    database_name: str = ""
    ready_to_patch: bool = False

    @property
    def has_failure(self) -> bool:
        return any(item.status is CheckStatus.FAIL for item in self.checks)


def assess_administrator(query: QueryFunc, backup_count: int | None = None) -> PostgresAdminReport:
    """Inspect the live session. SQL patching stays closed unless the role is a superuser on the primary."""
    report = PostgresAdminReport()
    try:
        rows = query(_ROLE_SQL)
    except Exception as exc:
        report.checks.append(
            AdminCheck("administrator", CheckStatus.FAIL, _safe(f"Administration role could not be read ({exc})"))
        )
        return report

    row = _first(rows)
    role_name = str(row.get("login_role") or "").strip() or "unknown"
    report.server_version = str(row.get("server_version") or "").strip()
    report.database_name = str(row.get("database_name") or "").strip()
    report.is_administrator = bool(row.get("is_superuser"))
    if report.server_version:
        report.checks.append(
            AdminCheck("version", CheckStatus.PASS, f"PostgreSQL {report.server_version}")
        )
    else:
        report.checks.append(
            AdminCheck("version", CheckStatus.FAIL, "PostgreSQL version could not be read")
        )

    if not report.is_administrator:
        report.checks.append(
            AdminCheck(
                "administrator",
                CheckStatus.FAIL,
                f"Connected role {role_name} is not a database administrator",
            )
        )
        report.checks.append(
            AdminCheck(
                "administration_scope",
                CheckStatus.FAIL,
                "Remaining administration checks run only for a database administrator",
            )
        )
        return report

    report.checks.append(
        AdminCheck("administrator", CheckStatus.PASS, f"Role {role_name} is a database administrator")
    )
    if report.database_name:
        report.checks.append(
            AdminCheck("database", CheckStatus.PASS, f"Target database is {report.database_name}")
        )
        report.checks.append(
            AdminCheck(
                "privileges",
                CheckStatus.PASS,
                f"Superuser privilege is present on {report.database_name}",
            )
        )
    else:
        report.checks.append(
            AdminCheck("database", CheckStatus.FAIL, "Target database could not be read")
        )
    if bool(row.get("in_recovery")):
        report.checks.append(
            AdminCheck(
                "recovery",
                CheckStatus.FAIL,
                "This server is in recovery. SQL patches run on the primary",
            )
        )
    else:
        report.checks.append(AdminCheck("recovery", CheckStatus.PASS, "Server is the primary"))

    _count_check(
        report,
        query,
        _SESSIONS_SQL,
        "sessions",
        "other_sessions",
        empty_summary="No other sessions are connected to this database",
        busy_summary="{count} other session(s) are connected to this database",
        unreadable="Other sessions could not be read",
        blocking=False,
    )
    _count_check(
        report,
        query,
        _PREPARED_SQL,
        "prepared_transactions",
        "prepared",
        empty_summary="No prepared transactions are open",
        busy_summary="{count} prepared transaction(s) are open",
        unreadable="Prepared transactions could not be read",
        blocking=True,
        busy_status=CheckStatus.FAIL,
    )
    _count_check(
        report,
        query,
        _LOCKS_SQL,
        "blocking_locks",
        "blocking_locks",
        empty_summary="No session is waiting on a lock held by another session",
        busy_summary="{count} lock wait(s) are blocking another session",
        unreadable="Blocking locks could not be read",
        blocking=True,
        busy_status=CheckStatus.FAIL,
    )
    _count_check(
        report,
        query,
        _SLOTS_SQL,
        "replication_slots",
        "inactive_slots",
        empty_summary="No inactive replication slots",
        busy_summary="{count} inactive replication slot(s)",
        unreadable="Replication slots could not be read",
        blocking=False,
    )
    _count_check(
        report,
        query,
        _RESTART_SQL,
        "pending_restart",
        "pending_restart",
        empty_summary="No setting is waiting for a restart",
        busy_summary="{count} setting(s) are waiting for a restart",
        unreadable="Pending restart settings could not be read",
        blocking=False,
    )
    _replication_check(report, query)
    report.checks.append(
        AdminCheck(
            "disk",
            CheckStatus.NOT_APPLICABLE,
            "Free disk space is not available from this database session, so no free-space figure is reported.",
        )
    )
    report.checks.append(
        AdminCheck(
            "backup",
            *_backup_status(backup_count),
        )
    )
    report.ready_to_patch = report.is_administrator and not report.has_failure
    return report


def format_report(report: PostgresAdminReport) -> str:
    lines = [f"[{item.status.value}] {item.code}: {item.summary}" for item in report.checks]
    if report.ready_to_patch:
        lines.append("A database administrator can run a SQL patch on this primary.")
    else:
        lines.append("SQL patch execution stays closed.")
    return "\n".join(lines)


_ROLE_SQL = """
SELECT
    current_user AS login_role,
    r.rolsuper AS is_superuser,
    current_setting('server_version') AS server_version,
    current_database() AS database_name,
    pg_is_in_recovery() AS in_recovery
FROM pg_roles AS r
WHERE r.rolname = current_user
"""

_SESSIONS_SQL = """
SELECT count(*) AS other_sessions
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND datname = current_database()
"""

_PREPARED_SQL = "SELECT count(*) AS prepared FROM pg_prepared_xacts"

_LOCKS_SQL = """
SELECT count(*) AS blocking_locks
FROM pg_locks AS blocked
JOIN pg_locks AS blocking
  ON blocking.locktype = blocked.locktype
 AND blocking.database IS NOT DISTINCT FROM blocked.database
 AND blocking.relation IS NOT DISTINCT FROM blocked.relation
 AND blocking.page IS NOT DISTINCT FROM blocked.page
 AND blocking.tuple IS NOT DISTINCT FROM blocked.tuple
 AND blocking.virtualxid IS NOT DISTINCT FROM blocked.virtualxid
 AND blocking.transactionid IS NOT DISTINCT FROM blocked.transactionid
 AND blocking.classid IS NOT DISTINCT FROM blocked.classid
 AND blocking.objid IS NOT DISTINCT FROM blocked.objid
 AND blocking.objsubid IS NOT DISTINCT FROM blocked.objsubid
 AND blocking.pid <> blocked.pid
WHERE NOT blocked.granted
"""

_REPL_SQL = """
SELECT count(*) AS replicas,
       count(*) FILTER (WHERE state IS DISTINCT FROM 'streaming') AS not_streaming
FROM pg_stat_replication
"""

_SLOTS_SQL = """
SELECT count(*) AS inactive_slots
FROM pg_replication_slots
WHERE active IS FALSE
"""

_RESTART_SQL = "SELECT count(*) AS pending_restart FROM pg_settings WHERE pending_restart"


def verify_after_patch(query: QueryFunc, expected_database: str) -> tuple[str, str]:
    """Read-only session check after execution. It does not prove the patch changed the intended objects."""
    try:
        row = _first(query(_VERIFY_SQL))
    except Exception as exc:
        return "failed", _safe(f"Post-patch session check failed ({exc})")
    problems: list[str] = []
    if str(row.get("database_name") or "") != expected_database:
        problems.append("the connected database does not match the patch target")
    if bool(row.get("in_recovery")):
        problems.append("the server is in recovery")
    if int(row.get("ok") or 0) != 1:
        problems.append("the session did not accept a read")
    note = " These checks do not prove the SQL patch changed the intended objects."
    if problems:
        return "failed", "; ".join(problems) + "." + note
    return "passed", "The session still reads the target database on the primary." + note


_VERIFY_SQL = """
SELECT current_database() AS database_name,
       pg_is_in_recovery() AS in_recovery,
       1 AS ok
"""


def _backup_status(backup_count: int | None) -> tuple[CheckStatus, str]:
    if backup_count is None:
        return (
            CheckStatus.NOT_APPLICABLE,
            "Restore readiness is not proven. No approved restore verification is recorded for this connection.",
        )
    if backup_count < 0:
        return (
            CheckStatus.WARNING,
            "The local backup catalog could not be read. Restore readiness is not proven.",
        )
    if backup_count == 0:
        return (
            CheckStatus.WARNING,
            "No local backup file is recorded on this computer. Restore readiness is not proven.",
        )
    return (
        CheckStatus.WARNING,
        f"{backup_count} local backup file(s) are listed. Listing a file does not prove it can be restored.",
    )


def _replication_check(report: PostgresAdminReport, query: QueryFunc) -> None:
    try:
        row = _first(query(_REPL_SQL))
        replicas = int(row.get("replicas") or 0)
        not_streaming = int(row.get("not_streaming") or 0)
    except Exception as exc:
        report.checks.append(
            AdminCheck("replication", CheckStatus.WARNING, _safe(f"Replication status could not be read ({exc})"))
        )
        return
    if replicas == 0:
        report.checks.append(
            AdminCheck(
                "replication",
                CheckStatus.NOT_APPLICABLE,
                "No streaming replica is connected. Replication status does not apply.",
            )
        )
        return
    if not_streaming:
        report.checks.append(
            AdminCheck(
                "replication",
                CheckStatus.WARNING,
                f"{not_streaming} replica(s) are not streaming",
            )
        )
        return
    report.checks.append(
        AdminCheck("replication", CheckStatus.PASS, f"{replicas} replica(s) are streaming")
    )


def _count_check(
    report: PostgresAdminReport,
    query: QueryFunc,
    sql: str,
    code: str,
    column: str,
    *,
    empty_summary: str,
    busy_summary: str,
    unreadable: str,
    blocking: bool,
    busy_status: CheckStatus = CheckStatus.WARNING,
) -> None:
    try:
        row = _first(query(sql))
        count = int(row.get(column) or 0)
    except Exception as exc:
        status = CheckStatus.FAIL if blocking else CheckStatus.WARNING
        report.checks.append(AdminCheck(code, status, _safe(f"{unreadable} ({exc})")))
        return
    if count == 0:
        report.checks.append(AdminCheck(code, CheckStatus.PASS, empty_summary))
        return
    report.checks.append(
        AdminCheck(code, busy_status, busy_summary.format(count=count))
    )


def _first(rows: list[dict] | None) -> dict:
    if not rows:
        return {}
    row = rows[0] or {}
    return {str(key).lower(): value for key, value in row.items()}


def _safe(text: str) -> str:
    return redact_text(text) or "check failed"
