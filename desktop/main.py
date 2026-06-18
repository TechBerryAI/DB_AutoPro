"""Desktop application entry point."""

from desktop.app import DatabaseAutomationApp
from backend.core.config import get_settings
from desktop.connection import apply_connection
from desktop.connection_dialog import ConnectionDialog
from desktop.ssh_tunnel import close_ssh_tunnel, open_ssh_tunnel


def main() -> None:
    while True:
        connection = ConnectionDialog.show()
        if connection is None:
            return

        app = None
        try:
            apply_connection(connection)
            effective = open_ssh_tunnel(connection)
            if connection.ssh_enabled and effective.host != connection.host:
                settings = get_settings()
                profile = "default"
                if profile in settings.connections:
                    settings.connections[profile].host = effective.host
                    settings.connections[profile].port = effective.port
            app = DatabaseAutomationApp(connection=connection)
            app.mainloop()
        finally:
            close_ssh_tunnel()

        if app is None or not app.disconnect_requested:
            return


if __name__ == "__main__":
    main()
