"""Desktop application entry point."""

from desktop.app import DatabaseAutomationApp
from desktop.connection import apply_connection
from desktop.connection_dialog import ConnectionDialog


def main() -> None:
    while True:
        connection = ConnectionDialog.show()
        if connection is None:
            return

        apply_connection(connection)
        app = DatabaseAutomationApp(connection=connection)
        app.mainloop()

        if not app.disconnect_requested:
            return


if __name__ == "__main__":
    main()
