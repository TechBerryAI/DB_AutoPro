"""Database-engine adapters for package patch discovery."""

from backend.services.mysql_patch.adapters.mysql_apt_ubuntu import UbuntuMysqlAptAdapter
from backend.services.mysql_patch.adapters.mysql_dnf_centos import CentosMysqlDnfAdapter

__all__ = ["CentosMysqlDnfAdapter", "UbuntuMysqlAptAdapter"]
