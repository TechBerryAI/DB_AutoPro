"""Export unified ML dataset CSV from project logs and monitoring data."""

from __future__ import annotations

import sys
from pathlib import Path

from backend.services.ml_dataset_export import export_ml_dataset


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    path, rows = export_ml_dataset(output)
    counts: dict[str, int] = {}
    for row in rows:
        event_type = str(row.get("event_type", "unknown"))
        counts[event_type] = counts.get(event_type, 0) + 1
    print(f"Exported {len(rows)} rows to {path}")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
