"""Configuration module tests."""

from backend.core.config import Settings, get_settings


def test_settings_loads_defaults():
    settings = Settings.from_yaml("dev")
    assert settings.app_name == "Database Automation Pro"
    assert settings.environment == "dev"


def test_settings_has_connections():
    settings = get_settings("dev")
    assert "default" in settings.connections


def test_backup_config():
    settings = get_settings("dev")
    assert settings.backup.compression is True
    assert settings.backup.retention_days == 7
