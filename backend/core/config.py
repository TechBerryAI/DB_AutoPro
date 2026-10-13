"""Application configuration with YAML profiles and environment variable support."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


from backend.core.paths import BACKUPS_DIR, CONFIG_DIR, LOGS_DIR, PROJECT_ROOT


class DatabaseConnectionConfig(BaseSettings):
    """Single database connection profile."""

    host: str = "localhost"
    port: int = 5432
    database: str = "postgres"
    username: str = "postgres"
    password: SecretStr = SecretStr("")
    ssl_mode: str | None = None
    extra_options: dict[str, Any] = Field(default_factory=dict)


class BackupConfig(BaseSettings):
    """Backup-related settings."""

    output_dir: Path = Field(default=BACKUPS_DIR)
    compression: bool = True
    compression_format: str = "gzip"  # gzip, zip, none
    retention_days: int = 30
    schedule_cron: str | None = None


class JWTConfig(BaseSettings):
    """JWT authentication settings."""

    secret_key: SecretStr = SecretStr("change-me-in-production")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7


class LoggingConfig(BaseSettings):
    """Structured logging configuration."""

    level: str = "INFO"
    format: str = "json"
    audit_log_path: Path = Field(default=LOGS_DIR / "audit.log")
    app_log_path: Path = Field(default=LOGS_DIR / "app.log")


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_prefix="DAP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Database Automation Pro"
    environment: str = "dev"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_type: str = "postgresql"
    connections: dict[str, DatabaseConnectionConfig] = Field(default_factory=dict)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    jwt: JWTConfig = Field(default_factory=JWTConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    allowed_roles: list[str] = Field(
        default_factory=lambda: ["admin", "operator", "viewer"]
    )

    @classmethod
    def from_yaml(cls, profile: str = "dev") -> Settings:
        """Load settings from YAML profile with env var overrides."""
        profile_path = CONFIG_DIR / f"{profile}.yaml"
        default_path = CONFIG_DIR / "default.yaml"

        data: dict[str, Any] = {}
        if default_path.exists():
            with default_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        if profile_path.exists():
            with profile_path.open(encoding="utf-8") as f:
                profile_data = yaml.safe_load(f) or {}
                data = _deep_merge(data, profile_data)

        env_profile = os.getenv("DAP_ENVIRONMENT", profile)
        if env_profile != profile and (CONFIG_DIR / f"{env_profile}.yaml").exists():
            with (CONFIG_DIR / f"{env_profile}.yaml").open(encoding="utf-8") as f:
                profile_data = yaml.safe_load(f) or {}
                data = _deep_merge(data, profile_data)

        _resolve_runtime_paths(data)
        return cls(**data)


def _resolve_runtime_paths(data: dict[str, Any]) -> None:
    """Turn relative backup and log paths into paths under the project root."""
    backup = data.get("backup")
    if isinstance(backup, dict) and backup.get("output_dir"):
        backup["output_dir"] = str(_under_root(backup["output_dir"]))
    logging_cfg = data.get("logging")
    if isinstance(logging_cfg, dict):
        for key in ("audit_log_path", "app_log_path"):
            if logging_cfg.get(key):
                logging_cfg[key] = str(_under_root(logging_cfg[key]))


def _under_root(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dict into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@lru_cache
def get_settings(profile: str | None = None) -> Settings:
    """Cached settings singleton."""
    env = profile or os.getenv("DAP_ENVIRONMENT", "dev")
    return Settings.from_yaml(env)
