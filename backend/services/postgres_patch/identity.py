"""Identity of the PostgreSQL database a SQL patch targets.

The patch key is the SQL SHA-256 checksum, the cluster system identifier from
pg_control_system(), and the database OID. The database name is stored for
display and for an exact live comparison. It is not the only identity.

Limitations:
- system_identifier changes when the cluster is reinitialized. A restored copy
  with a new system identifier is a different cluster.
- The database OID changes if that database is dropped and created again.
- The configured endpoint is the connection label the operator selected. It is
  not a separate proof of the cluster. A tunnel address can differ from the
  server's own address, so a live system identifier mismatch still blocks.
- History rows written before these fields existed have no cluster identity.
  They still load, and they are not treated as a completed patch on a live cluster.
- A running row from that older history matches the checksum and the exact
  database name only. It still requires reconciliation before another attempt.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.mysql_patch.redaction import redact_text


class DatabaseIdentityError(Exception):
    """The live database identity could not be confirmed."""


@dataclass(frozen=True)
class DatabaseIdentity:
    system_identifier: str
    database_oid: str
    database_name: str

    def summary(self) -> str:
        return (
            f"Cluster {self.system_identifier}, database OID {self.database_oid}, "
            f"database {self.database_name}."
        )


def patch_identity_key(checksum_sha256: str, system_identifier: str, database_oid: str) -> tuple[str, str, str]:
    """Stable duplicate key. The database name is intentionally absent."""
    return (
        checksum_sha256.strip().lower(),
        system_identifier.strip(),
        database_oid.strip(),
    )


def read_database_identity(query) -> DatabaseIdentity:
    """Read the live cluster and database identity. Raises when any part is missing."""
    try:
        rows = query(_IDENTITY_SQL)
    except Exception as exc:
        raise DatabaseIdentityError(
            redact_text(f"Live database identity could not be read ({exc})")
            or "Live database identity could not be read."
        ) from exc
    row = {}
    if rows:
        row = {str(key).lower(): value for key, value in (rows[0] or {}).items()}
    identity = DatabaseIdentity(
        system_identifier=str(row.get("system_identifier") or "").strip(),
        database_oid=str(row.get("database_oid") or "").strip(),
        database_name=str(row.get("database_name") or "").strip(),
    )
    if not identity.system_identifier or not identity.database_oid or not identity.database_name:
        raise DatabaseIdentityError(
            "Live database identity is incomplete. The patch was not executed."
        )
    return identity


_IDENTITY_SQL = """
SELECT current_database() AS database_name,
       d.oid::text AS database_oid,
       s.system_identifier::text AS system_identifier
FROM pg_database AS d
CROSS JOIN pg_control_system() AS s
WHERE d.datname = current_database()
"""
