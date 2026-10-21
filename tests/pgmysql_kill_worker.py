"""Child process for the opt-in kill test. It is not collected by pytest.

The parent supplies disposable DSNs and a sqlite path. This process creates one
table, then waits so the parent can terminate it before the operation is finished.
"""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    store_path = os.environ.get("DB_AUTOPRO_PGMYSQL_KILL_STORE", "")
    source_dsn = os.environ.get("DB_AUTOPRO_PG_MYSQL_SOURCE_DSN", "")
    target_dsn = os.environ.get("DB_AUTOPRO_PG_MYSQL_TARGET_DSN", "")
    if not store_path or not source_dsn.endswith("/db_autopro_pgmysql_src"):
        print("disposable kill worker is not configured", file=sys.stderr)
        return 2
    if not target_dsn.endswith("/db_autopro_pgmysql_dst"):
        print("disposable kill worker target is not configured", file=sys.stderr)
        return 2

    from urllib.parse import urlparse

    from backend.connectors.base import ConnectionInfo
    from backend.connectors.mysql import MySQLConnector
    from backend.connectors.postgresql import PostgreSQLConnector
    from backend.models.schemas import DatabaseType, MigrationRequest
    from backend.services.migration_service import MigrationService
    from backend.services.pg_mysql_migration.engine import MysqlTargetSession
    from backend.services.pg_mysql_migration.operation_store import MigrationOperationStore

    original = MysqlTargetSession.execute

    def pause_after_create(self, sql, params=None):
        original(self, sql, params)
        if str(sql).startswith("CREATE TABLE"):
            time.sleep(60)

    MysqlTargetSession.execute = pause_after_create

    def parsed(dsn: str) -> dict:
        value = urlparse(dsn)
        return {
            "host": value.hostname or "127.0.0.1",
            "port": value.port or 5432,
            "user": value.username or "",
            "password": value.password or "",
            "database": value.path.lstrip("/"),
        }

    source = parsed(source_dsn)
    target = parsed(target_dsn)

    def factory(db_type, _profile):
        if db_type == DatabaseType.POSTGRESQL:
            return PostgreSQLConnector(
                ConnectionInfo(source["host"], source["port"], source["database"], source["user"], source["password"])
            )
        return MySQLConnector(
            ConnectionInfo(target["host"], target["port"], target["database"], target["user"], target["password"])
        )

    service = MigrationService(factory, MigrationOperationStore(store_path))
    request = MigrationRequest(
        source_type=DatabaseType.POSTGRESQL,
        target_type=DatabaseType.MYSQL,
        scope="schema_only",
        strategy="fail_on_conflict",
        source_database="db_autopro_pgmysql_src",
        target_database="db_autopro_pgmysql_dst",
        tables=["kill_hold"],
        confirm_plan=True,
        client_request_id="kill-hold",
    )
    service.migrate(request, user="disposable-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
