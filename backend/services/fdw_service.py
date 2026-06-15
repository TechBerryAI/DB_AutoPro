"""PostgreSQL Foreign Data Wrapper (FDW) management."""

from __future__ import annotations

from backend.connectors.base import get_connector
from backend.core.exceptions import FDWError, ValidationError
from backend.core.logging import get_audit_logger, get_logger
from backend.models.schemas import DatabaseType, FDWRequest

logger = get_logger("services.fdw")
audit = get_audit_logger()


class FDWService:
    """Manages PostgreSQL FDW setup and validation."""

    def create_extension(self, profile: str = "default", extension: str = "postgres_fdw") -> dict:
        connector = get_connector(DatabaseType.POSTGRESQL, profile)
        connector.execute_script(f"CREATE EXTENSION IF NOT EXISTS {extension};")
        audit.log("fdw_extension", "system", extension, "success")
        return {"extension": extension, "status": "created"}

    def create_foreign_server(self, request: FDWRequest) -> dict:
        sql = f"""
        CREATE SERVER IF NOT EXISTS {request.server_name}
        FOREIGN DATA WRAPPER {request.extension}
        OPTIONS (host '{request.foreign_host}', port '{request.foreign_port}', dbname '{request.foreign_db}');
        """
        connector = get_connector(DatabaseType.POSTGRESQL, request.profile)
        connector.execute_script(sql)
        audit.log("fdw_server", "system", request.server_name, "success")
        return {"server_name": request.server_name, "status": "created"}

    def create_user_mapping(self, request: FDWRequest) -> dict:
        sql = f"""
        CREATE USER MAPPING IF NOT EXISTS FOR {request.local_user}
        SERVER {request.server_name}
        OPTIONS (user '{request.foreign_user}', password '{request.foreign_password}');
        """
        connector = get_connector(DatabaseType.POSTGRESQL, request.profile)
        connector.execute_script(sql)
        audit.log("fdw_mapping", "system", request.server_name, "success")
        return {"server_name": request.server_name, "local_user": request.local_user, "status": "created"}

    def import_foreign_schema(self, request: FDWRequest) -> dict:
        if not request.schema_name:
            raise FDWError("schema_name is required for import")
        sql = f"""
        IMPORT FOREIGN SCHEMA {request.schema_name}
        FROM SERVER {request.server_name}
        INTO public;
        """
        connector = get_connector(DatabaseType.POSTGRESQL, request.profile)
        connector.execute_script(sql)
        audit.log("fdw_import", "system", request.schema_name, "success")
        return {"schema": request.schema_name, "status": "imported"}

    def setup_complete_fdw(self, request: FDWRequest) -> dict:
        """Full FDW setup: extension, server, mapping, optional schema import."""
        results = []
        results.append(self.create_extension(request.profile, request.extension))
        results.append(self.create_foreign_server(request))
        results.append(self.create_user_mapping(request))
        if request.schema_name:
            results.append(self.import_foreign_schema(request))
        validation = self.validate_fdw(request)
        return {"steps": results, "validation": validation}

    def validate_fdw(self, request: FDWRequest) -> dict:
        """Validate FDW configuration."""
        connector = get_connector(DatabaseType.POSTGRESQL, request.profile)
        checks = {}

        try:
            ext_rows = connector.execute_query(
                "SELECT extname FROM pg_extension WHERE extname = %s",
                (request.extension,),
            )
            checks["extension"] = len(ext_rows) > 0
        except Exception as exc:
            checks["extension"] = False
            checks["extension_error"] = str(exc)

        try:
            server_rows = connector.execute_query(
                "SELECT srvname FROM pg_foreign_server WHERE srvname = %s",
                (request.server_name,),
            )
            checks["server"] = len(server_rows) > 0
        except Exception as exc:
            checks["server"] = False
            checks["server_error"] = str(exc)

        all_passed = all(v is True for k, v in checks.items() if not k.endswith("_error"))
        if not all_passed:
            raise ValidationError("FDW validation failed", checks)

        return {"valid": True, "checks": checks}
