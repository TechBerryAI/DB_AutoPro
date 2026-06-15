"""API routes package."""

from api.routes import auth, backup, fdw, migration, monitoring, patch, restore

__all__ = ["auth", "backup", "restore", "patch", "fdw", "migration", "monitoring"]
