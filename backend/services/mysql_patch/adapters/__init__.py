"""Database-engine adapters for package patch discovery."""

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import UbuntuMysqlAptAdapter

__all__ = ["UbuntuMysqlAptAdapter"]
