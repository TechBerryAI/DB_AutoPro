"""Explicit source-target compatibility matrix.

A pair is listed as implemented only when this application already has a
connector, inspection path, writer, type mapping, and verification path.
Dropdowns must not offer other combinations as if they were operational.
"""

from __future__ import annotations

from dataclasses import dataclass, field

POSTGRESQL = "postgresql"
MYSQL = "mysql"

_KNOWN_TYPES = (
    POSTGRESQL,
    MYSQL,
    "mariadb",
    "mssql",
    "oracle",
    "sqlite",
    "mongodb",
    "redis",
)


@dataclass(frozen=True)
class PairCapability:
    source: str
    target: str
    schema: bool
    data: bool
    type_conversion: bool
    constraints: bool
    indexes: bool
    views: bool
    programmable_objects: bool
    verification: bool
    engine: str
    limitations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def implemented(self) -> bool:
        return bool(self.engine)

    def summary(self) -> str:
        lines = [
            f"{self.source} to {self.target}.",
            f"Schema: {'yes' if self.schema else 'no'}. Data: {'yes' if self.data else 'no'}.",
            f"Type conversion: {'yes' if self.type_conversion else 'no'}. "
            f"Constraints: {'yes' if self.constraints else 'no'}. Indexes: {'yes' if self.indexes else 'no'}.",
            f"Views: {'yes' if self.views else 'no'}. Programmable objects: {'yes' if self.programmable_objects else 'no'}.",
            f"Verification: {'yes' if self.verification else 'no'}.",
        ]
        if self.limitations:
            lines.append("Limitations: " + " ".join(self.limitations))
        return " ".join(lines)


_PAIRS: dict[tuple[str, str], PairCapability] = {
    (POSTGRESQL, POSTGRESQL): PairCapability(
        source=POSTGRESQL,
        target=POSTGRESQL,
        schema=True,
        data=True,
        type_conversion=False,
        constraints=True,
        indexes=True,
        views=False,
        programmable_objects=False,
        verification=True,
        engine="postgresql",
        limitations=(
            "Public tables only.",
            "Views, materialized views, functions, triggers, policies, extensions, custom types, and non-public schemas are reported and not copied.",
            "Row-count verification is not a restore test.",
        ),
    ),
    (POSTGRESQL, MYSQL): PairCapability(
        source=POSTGRESQL,
        target=MYSQL,
        schema=True,
        data=True,
        type_conversion=True,
        constraints=True,
        indexes=True,
        views=False,
        programmable_objects=False,
        verification=True,
        engine="postgresql_to_mysql",
        limitations=(
            "Public tables only.",
            "Some PostgreSQL types are converted with possible precision loss.",
            "Views, functions, triggers, sequences, enums, and non-public schemas are not copied.",
            "MySQL DDL commits immediately and is not rolled back.",
            "A PostgreSQL dump does not prove this MySQL target can be restored.",
        ),
    ),
}


def _norm(value: str) -> str:
    text = (value or "").strip().lower()
    if text == "mariadb":
        return MYSQL
    return text


def pair_capability(source_type: str, target_type: str) -> PairCapability | None:
    return _PAIRS.get((_norm(source_type), _norm(target_type)))


def implemented_pairs() -> tuple[PairCapability, ...]:
    return tuple(_PAIRS.values())


def source_types() -> tuple[str, ...]:
    return tuple(dict.fromkeys(pair.source for pair in _PAIRS.values()))


def target_types_for(source_type: str) -> tuple[str, ...]:
    source = _norm(source_type)
    return tuple(pair.target for pair in _PAIRS.values() if pair.source == source)


def unsupported_pair_reason(source_type: str, target_type: str) -> str | None:
    source = _norm(source_type)
    target = _norm(target_type)
    if (source, target) in _PAIRS:
        return None
    if source == target == "mongodb":
        return (
            "MongoDB-to-MongoDB document migration is not implemented. "
            "MongoDB is not treated as a relational source or target. No collections were copied."
        )
    if "redis" in {source, target}:
        return (
            f"{source}-to-{target} migration is not implemented. "
            "Redis keys are not mapped to tables or collections without an approved mapping. "
            "No data was copied."
        )
    if source in _KNOWN_TYPES and target in _KNOWN_TYPES:
        return (
            f"{source}-to-{target} migration is not implemented. "
            "Select a registered pair: postgresql to postgresql, or postgresql to mysql. "
            "No schema or data was copied."
        )
    return (
        f"{source}-to-{target} migration is not implemented. "
        "Select PostgreSQL as the source and PostgreSQL or MySQL as the target. "
        "No schema or data was copied."
    )


def servers_matching_type(servers: list, db_type: str) -> list:
    """Filter saved connections by stored db_type metadata, not by display name."""
    wanted = _norm(db_type)
    matched = []
    for server in servers:
        actual = _norm(str(getattr(server, "db_type", "") or ""))
        if actual == wanted:
            matched.append(server)
    return matched
