"""Tests for restore planning gaps (Mongo gzip, catalog methods)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.models.schemas import (
    BackupCategory,
    BackupMethod,
    DatabaseType,
    MongoRestoreScope,
    RestoreRequest,
)
from backend.services.mongo_restore import plan_mongorestore
from backend.services.restore_catalog import restore_method_label, restore_methods_for


def test_mongo_folder_gzip_dump_adds_gzip_flag(tmp_path: Path) -> None:
    dump = tmp_path / "db_backup"
    coll = dump / "mydb"
    coll.mkdir(parents=True)
    (coll / "users.bson.gz").write_bytes(b"fake")
    (coll / "users.metadata.json.gz").write_text("{}", encoding="utf-8")

    plan = plan_mongorestore(
        RestoreRequest(
            backup_path=str(dump),
            mongo_restore_scope=MongoRestoreScope.ALL_DATABASES,
        ),
        dump,
    )
    assert "--gzip" in plan.extra_flags
    assert plan.source_path == str(dump)


def test_mongo_specific_db_honors_gzip_checkbox(tmp_path: Path) -> None:
    dump = tmp_path / "dump"
    dump.mkdir()
    plan = plan_mongorestore(
        RestoreRequest(
            backup_path=str(dump),
            database="sales",
            mongo_restore_scope=MongoRestoreScope.SPECIFIC_DATABASE,
            gzip_archive=True,
        ),
        dump,
    )
    assert "--gzip" in plan.extra_flags


def test_mongo_single_collection_accepts_bson_gz(tmp_path: Path) -> None:
    bson_gz = tmp_path / "users.bson.gz"
    bson_gz.write_bytes(b"fake")
    plan = plan_mongorestore(
        RestoreRequest(
            backup_path=str(bson_gz),
            database="sales",
            collection="users",
            mongo_restore_scope=MongoRestoreScope.SINGLE_COLLECTION,
        ),
        bson_gz,
    )
    assert "--gzip" in plan.extra_flags
    assert plan.collection == "users"


def test_restore_catalog_exposes_mssql_and_oracle() -> None:
    assert restore_methods_for(DatabaseType.MSSQL, BackupCategory.PHYSICAL) == [
        BackupMethod.MSSQL_BACKUP
    ]
    assert restore_methods_for(DatabaseType.ORACLE, BackupCategory.LOGICAL) == [
        BackupMethod.ORACLE_IMPDP
    ]
    assert "RESTORE DATABASE" in restore_method_label(BackupMethod.MSSQL_BACKUP)
    assert "impdp" in restore_method_label(BackupMethod.ORACLE_IMPDP)


def test_restore_catalog_exposes_physical_pg_mysql() -> None:
    assert BackupMethod.PG_BASEBACKUP in restore_methods_for(
        DatabaseType.POSTGRESQL, BackupCategory.PHYSICAL
    )
    assert BackupMethod.MYSQL_PHYSICAL in restore_methods_for(
        DatabaseType.MYSQL, BackupCategory.PHYSICAL
    )
