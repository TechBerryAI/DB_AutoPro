"""Copy mapped PostgreSQL tables into MongoDB collections."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from backend.services.mysql_patch.redaction import redact_text
from backend.services.pg_migration.precheck import SCOPE_DATA, SCOPE_SCHEMA, STRATEGY_REPLACE, STRATEGY_SKIP
from backend.services.pg_mongo_migration.mapping import row_document
from backend.services.pg_mongo_migration.schema import CollectionSpec


@dataclass
class Outcome:
    name: str
    action: str
    detail: str
    source_rows: int | None = None
    target_rows: int | None = None


@dataclass
class MongoMigrationRun:
    status: str
    summary: str
    outcomes: list[Outcome] = field(default_factory=list)
    migration_id: str = ""


def run_pg_mongo_migration(
    specs: list[CollectionSpec],
    conflicts: list[str],
    *,
    scope: str,
    strategy: str,
    batch_size: int,
    fetch_batches,
    target,
    count_source,
    cancel: threading.Event,
    migration_id: str,
    on_progress=None,
) -> MongoMigrationRun:
    outcomes: list[Outcome] = []
    committed: list[str] = []
    total = len(specs)
    done = 0
    for spec in specs:
        if cancel.is_set():
            return _finish("Cancelled", "Cancellation was requested before this collection. Written MongoDB collections were not rolled back.", outcomes, migration_id)
        if spec.name in conflicts and strategy == STRATEGY_SKIP and scope != SCOPE_DATA:
            outcomes.append(Outcome(spec.name, "skipped", "The existing MongoDB collection was left unchanged."))
            done += 1
            _progress(on_progress, done, total, spec.name)
            continue
        try:
            if spec.name in conflicts and strategy == STRATEGY_REPLACE:
                target.drop_collection(spec.name)
                outcomes.append(Outcome(spec.name, "dropped", "The existing MongoDB collection was dropped because replacement was confirmed."))
            if scope != SCOPE_DATA:
                target.ensure_collection(spec.name)
                committed.append(spec.name)
            inserted = 0
            if scope != SCOPE_SCHEMA:
                for batch in fetch_batches(spec.name, spec.columns, batch_size, cancel):
                    if cancel.is_set():
                        outcomes.append(Outcome(spec.name, "rolled_back", "Cancellation stopped this collection. Earlier collections were not rolled back."))
                        return _finish("Cancelled", "Cancellation stopped the open MongoDB insert. Earlier collections were not rolled back.", outcomes, migration_id)
                    documents = [row_document(spec.columns, row, spec.primary_key) for row in batch]
                    if documents:
                        target.insert_many(spec.name, documents)
                        inserted += len(documents)
                    _progress(on_progress, done, total, f"{spec.name}: {inserted:,} documents copied")
                if spec.name not in committed:
                    committed.append(spec.name)
            outcomes.append(Outcome(spec.name, "data_copied" if scope != SCOPE_SCHEMA else "schema_created", f"Documents written: {inserted}.", source_rows=inserted))
        except Exception as exc:
            outcomes.append(Outcome(spec.name, "failed", redact_text(str(exc)) or "The collection failed."))
            if committed or any(item.action == "dropped" for item in outcomes):
                return _finish("Partially completed", "Earlier MongoDB changes stayed. This attempt was not rolled back. " + (redact_text(str(exc)) or ""), outcomes, migration_id)
            return _finish("Failed", redact_text(str(exc)) or "Migration failed.", outcomes, migration_id)
        done += 1
        _progress(on_progress, done, total, spec.name)

    mismatched = []
    for spec in specs:
        if scope == SCOPE_SCHEMA or spec.name not in [item.name for item in outcomes if item.action == "data_copied"]:
            continue
        source_rows = int(count_source(spec.name))
        target_rows = int(target.count(spec.name))
        for item in outcomes:
            if item.name == spec.name and item.action == "data_copied":
                item.source_rows = source_rows
                item.target_rows = target_rows
        if target_rows != source_rows:
            mismatched.append(spec.name)
    if mismatched:
        return _finish("Verification failed", "Document counts do not match for: " + ", ".join(mismatched) + ".", outcomes, migration_id)
    status = "Completed with warnings" if any(item.action in {"skipped", "dropped"} for item in outcomes) else "Completed and verified"
    summary = "MongoDB document counts match the PostgreSQL source." if status == "Completed and verified" else "The MongoDB migration finished with collections skipped or replaced."
    if scope == SCOPE_SCHEMA and status == "Completed and verified":
        summary = "MongoDB collections were created. No documents were copied."
    return _finish(status, summary, outcomes, migration_id)


def _progress(on_progress, done, total, name) -> None:
    if on_progress is not None:
        on_progress(done, total, name)


def _finish(status: str, summary: str, outcomes: list[Outcome], migration_id: str) -> MongoMigrationRun:
    return MongoMigrationRun(status, summary, outcomes, migration_id)


class MongoTargetSession:
    """MongoDB writes for one outbound PostgreSQL migration."""

    def __init__(self, client, database: str) -> None:
        self._client = client
        self._database = client[database]

    def drop_collection(self, name: str) -> None:
        self._database.drop_collection(name)

    def ensure_collection(self, name: str) -> None:
        if name not in self._database.list_collection_names():
            self._database.create_collection(name)

    def insert_many(self, name: str, documents: list[dict]) -> None:
        if documents:
            self._database[name].insert_many(documents, ordered=True)

    def count(self, name: str) -> int:
        return int(self._database[name].count_documents({}))

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
