"""Run a PostgreSQL script in one transaction when every statement can be rolled back.

Statement boundaries follow quotes, dollar quotes, and comments. That is enough to
execute a script. It is not a SQL parser and it is not an access-control check.
"""

from __future__ import annotations

import re
from typing import Callable

from backend.core.exceptions import ConnectorError

ProgressFunc = Callable[[int, int], None]

_EXPLICIT_TX = re.compile(r"^(commit|rollback|begin|start\s+transaction|end)\b", re.IGNORECASE)
_NON_TX = re.compile(
    r"^(vacuum|cluster|checkpoint|alter\s+system|create\s+database|drop\s+database|"
    r"create\s+tablespace|drop\s+tablespace)\b",
    re.IGNORECASE,
)
_CONCURRENT = re.compile(
    r"^(create|drop|reindex)\b.*\bconcurrently\b",
    re.IGNORECASE,
)


class PostgresScriptError(ConnectorError):
    """A script stopped before a normal commit. apply_result says what remains."""

    def __init__(
        self,
        message: str,
        *,
        apply_result: str,
        statement_index: int = 0,
        statement_count: int = 0,
        completed_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.apply_result = apply_result
        self.statement_index = statement_index
        self.statement_count = statement_count
        self.completed_count = completed_count


def split_sql(script: str) -> list[str]:
    """Split a script on semicolons that are outside quotes, dollar quotes, and comments."""
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(script)
    while index < length:
        char = script[index]
        nxt = script[index + 1] if index + 1 < length else ""
        if char == "-" and nxt == "-":
            end = script.find("\n", index)
            if end < 0:
                break
            index = end + 1
            continue
        if char == "/" and nxt == "*":
            index = _skip_block_comment(script, index + 2)
            continue
        if char == "'":
            token, index = _quoted(script, index, "'")
            buffer.append(token)
            continue
        if char == '"':
            token, index = _quoted(script, index, '"')
            buffer.append(token)
            continue
        if char == "$":
            token, index = _dollar(script, index)
            buffer.append(token)
            continue
        if char == ";":
            text = "".join(buffer).strip()
            if text:
                statements.append(text)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def statement_effect(sql: str) -> str:
    """Classify rollback support from the leading words. This is not a full parse."""
    compact = re.sub(r"\s+", " ", sql).strip()
    if _EXPLICIT_TX.match(compact):
        return "explicit_transaction"
    if _NON_TX.match(compact) or _CONCURRENT.match(compact):
        return "non_transactional"
    return "transactional"


def execute_statements(
    connection,
    script: str,
    *,
    allow_non_transactional: bool = False,
    on_progress: ProgressFunc | None = None,
) -> None:
    """Execute every statement. Transactional scripts commit once, or roll back entirely."""
    statements = split_sql(script)
    if not statements:
        raise PostgresScriptError("The SQL file has no statements.", apply_result="not_committed")
    effects = [statement_effect(item) for item in statements]
    if "explicit_transaction" in effects:
        raise PostgresScriptError(
            "The script contains BEGIN, COMMIT, or ROLLBACK. "
            "This page cannot track a transaction the script controls, so it was not executed.",
            apply_result="not_committed",
            statement_count=len(statements),
        )
    if "non_transactional" in effects and not allow_non_transactional:
        raise PostgresScriptError(
            "The script contains a statement PostgreSQL cannot roll back, such as VACUUM "
            "or CREATE INDEX CONCURRENTLY. Confirm a non-transactional run before execution.",
            apply_result="not_committed",
            statement_count=len(statements),
        )
    if "non_transactional" in effects:
        _run_each(connection, statements, on_progress, transactional=False)
        return
    _run_each(connection, statements, on_progress, transactional=True)


def _run_each(connection, statements: list[str], on_progress: ProgressFunc | None, *, transactional: bool) -> None:
    total = len(statements)
    completed = 0
    try:
        connection.autocommit = not transactional
    except Exception:
        pass
    for index, statement in enumerate(statements, start=1):
        if on_progress is not None:
            on_progress(index, total)
        try:
            with connection.cursor() as cursor:
                cursor.execute(statement)
            if not transactional:
                _commit(connection)
            completed += 1
        except PostgresScriptError:
            raise
        except Exception as exc:
            if transactional:
                apply_result = "rolled_back" if _rollback(connection) else "rollback_unconfirmed"
                completed = 0
            else:
                apply_result = "partial" if completed else "not_committed"
            detail = _safe_db_error(exc)
            if apply_result == "partial":
                message = (
                    f"Statement {index} of {total} failed after {completed} statement(s) had already been committed. "
                    f"Those statements were not rolled back. {detail}"
                )
            elif apply_result == "rolled_back":
                message = f"Statement {index} of {total} failed. The transaction was rolled back. {detail}"
            elif apply_result == "rollback_unconfirmed":
                message = (
                    f"Statement {index} of {total} failed and rollback could not be confirmed. {detail}"
                )
            else:
                message = f"Statement {index} of {total} failed. No statement was committed. {detail}"
            raise PostgresScriptError(
                message,
                apply_result=apply_result,
                statement_index=index,
                statement_count=total,
                completed_count=completed,
            ) from exc
    if transactional:
        if not _commit(connection):
            raise PostgresScriptError(
                "The script ran but the commit could not be confirmed.",
                apply_result="rollback_unconfirmed",
                statement_count=total,
                completed_count=0,
            )


def _commit(connection) -> bool:
    try:
        connection.commit()
        return True
    except Exception:
        return False


def _rollback(connection) -> bool:
    try:
        connection.rollback()
        return True
    except Exception:
        return False


def _safe_db_error(exc: Exception) -> str:
    text = str(exc).split("\n", 1)[0]
    text = re.sub(r"(?i)\b(password|passwd|pwd)\b(\s*[:=]\s*)\S+", r"\1\2[REDACTED]", text)
    text = re.sub(r"'(?:[^']|'')*'", "'[value]'", text)
    return text[:300]


def _skip_block_comment(script: str, index: int) -> int:
    depth = 1
    while index < len(script):
        if script.startswith("/*", index):
            depth += 1
            index += 2
            continue
        if script.startswith("*/", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    return index


def _quoted(script: str, index: int, quote: str) -> tuple[str, int]:
    start = index
    index += 1
    while index < len(script):
        if script[index] == quote:
            if index + 1 < len(script) and script[index + 1] == quote:
                index += 2
                continue
            return script[start : index + 1], index + 1
        index += 1
    return script[start:], index


def _dollar(script: str, index: int) -> tuple[str, int]:
    match = re.match(r"\$[A-Za-z0-9_]*\$", script[index:])
    if match is None:
        return script[index], index + 1
    tag = match.group(0)
    end = script.find(tag, index + len(tag))
    if end < 0:
        return script[index:], len(script)
    return script[index : end + len(tag)], end + len(tag)
