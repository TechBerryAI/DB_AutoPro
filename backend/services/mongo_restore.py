"""MongoDB mongorestore command planning for restore operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.exceptions import RestoreError
from backend.models.schemas import MongoRestoreScope, RestoreRequest

_SCOPE_LABELS: dict[MongoRestoreScope, str] = {
    MongoRestoreScope.ALL_DATABASES: "All databases",
    MongoRestoreScope.SPECIFIC_DATABASE: "Specific database",
    MongoRestoreScope.SINGLE_COLLECTION: "Single collection",
    MongoRestoreScope.ARCHIVE: "Compressed archive",
}

_SCOPE_HINTS: dict[MongoRestoreScope, str] = {
    MongoRestoreScope.ALL_DATABASES: (
        "Restores every database in the backup folder. "
        "Missing databases and collections are created automatically."
    ),
    MongoRestoreScope.SPECIFIC_DATABASE: (
        "Restores one database into the name you type below (created if it does not exist). "
        "Point Restore source at the backup root or the database subfolder."
    ),
    MongoRestoreScope.SINGLE_COLLECTION: (
        "Restores one collection into the database/collection names below "
        "(created if they do not exist). Restore source must be a .bson file."
    ),
    MongoRestoreScope.ARCHIVE: (
        "Restores from a mongodump .archive or .archive.gz file. "
        "Enable Gzip when the file was created with mongodump --gzip."
    ),
}


def mongo_restore_scope_label(scope: MongoRestoreScope) -> str:
    return _SCOPE_LABELS[scope]


def mongo_restore_scope_hint(scope: MongoRestoreScope) -> str:
    return _SCOPE_HINTS[scope]


def mongo_restore_scopes() -> list[MongoRestoreScope]:
    return list(MongoRestoreScope)


def _resolve_scope(request: RestoreRequest) -> MongoRestoreScope:
    if request.mongo_restore_scope:
        return request.mongo_restore_scope
    if request.collection:
        return MongoRestoreScope.SINGLE_COLLECTION
    path = Path(request.backup_path)
    if path.is_file() and _is_archive_file(path):
        return MongoRestoreScope.ARCHIVE
    if request.database:
        return MongoRestoreScope.SPECIFIC_DATABASE
    return MongoRestoreScope.ALL_DATABASES


def _is_archive_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".archive") or name.endswith(".archive.gz") or (
        path.suffix == ".gz" and "archive" in path.stem.lower()
    )


def _is_remote_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    return normalized.startswith("/") or normalized.startswith("mnt/")


def _path_available(path: Path) -> bool:
    return path.exists() or _is_remote_path(str(path))


@dataclass
class MongorestorePlan:
    """Flags and paths passed to mongorestore after connection options."""

    database: str | None
    collection: str | None
    extra_flags: list[str]
    source_path: str | None
    scope: MongoRestoreScope

    @property
    def uses_archive_flags(self) -> bool:
        return any(f.startswith("--archive=") for f in self.extra_flags)


def plan_mongorestore(request: RestoreRequest, backup_path: Path) -> MongorestorePlan:
    """Build mongorestore options from restore scope and user-selected path."""
    scope = _resolve_scope(request)
    db = request.database.strip() if request.database and request.database.strip() else None
    coll = request.collection.strip() if request.collection and request.collection.strip() else None
    extra: list[str] = []
    source: str | None = None

    if not _path_available(backup_path):
        raise RestoreError(f"Backup not found: {backup_path}")

    path_known = backup_path.exists()

    if scope == MongoRestoreScope.ALL_DATABASES:
        if path_known and not backup_path.is_dir():
            raise RestoreError("All-databases restore requires a backup folder path.")
        source = str(backup_path)

    elif scope == MongoRestoreScope.SPECIFIC_DATABASE:
        if not db:
            raise RestoreError("Database name is required for specific-database restore.")
        source = str(backup_path)
        if request.drop_existing:
            extra.append("--drop")

    elif scope == MongoRestoreScope.SINGLE_COLLECTION:
        if not db:
            raise RestoreError("Database name is required for single-collection restore.")
        if not coll:
            raise RestoreError("Collection name is required for single-collection restore.")
        if path_known and not backup_path.is_file():
            raise RestoreError("Single-collection restore requires a .bson file path.")
        if path_known and backup_path.suffix.lower() != ".bson":
            raise RestoreError("Single-collection restore source must be a .bson file.")
        source = str(backup_path)
        if request.drop_existing:
            extra.append("--drop")

    elif scope == MongoRestoreScope.ARCHIVE:
        if path_known and not backup_path.is_file():
            raise RestoreError("Archive restore requires an .archive or .archive.gz file.")
        if path_known and not _is_archive_file(backup_path):
            raise RestoreError("Archive restore source must be a .archive or .archive.gz file.")
        gzip = request.gzip_archive or backup_path.name.lower().endswith(".gz")
        extra.append(f"--archive={backup_path}")
        if gzip:
            extra.append("--gzip")
        if db and request.drop_existing:
            extra.append("--drop")

    else:
        raise RestoreError(f"Unsupported restore scope: {scope}")

    return MongorestorePlan(
        database=db if scope != MongoRestoreScope.ALL_DATABASES else None,
        collection=coll if scope == MongoRestoreScope.SINGLE_COLLECTION else None,
        extra_flags=extra,
        source_path=source,
        scope=scope,
    )


def format_mongorestore_preview(
    *,
    mongorestore_bin: str,
    host: str,
    port: int,
    username: str,
    auth_database: str,
    plan: MongorestorePlan,
    mask_password: bool = True,
) -> str:
    """Human-readable mongorestore command for the UI preview."""
    auth = (
        f"--username {username} --password {'****' if mask_password else '***'} "
        f"--authenticationDatabase {auth_database}"
    )
    parts = [mongorestore_bin, f"--host {host}", f"--port {port}", auth]
    parts.extend(plan.extra_flags)
    if plan.database:
        parts.append(f"--db {plan.database}")
    if plan.collection:
        parts.append(f"--collection {plan.collection}")
    if plan.source_path and not plan.uses_archive_flags:
        parts.append(plan.source_path)
    return " ".join(parts)
