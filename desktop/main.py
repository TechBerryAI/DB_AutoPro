"""Desktop application entry point."""

from desktop.app import DatabaseAutomationApp
from desktop.auth_dialog import AuthDialog
from desktop.ssh_tunnel import close_ssh_tunnel


def main() -> None:
    while True:
        employee = AuthDialog.show()
        if employee is None:
            return

        app = None
        try:
            app = DatabaseAutomationApp(connection=None, employee=employee)
            app.mainloop()
        finally:
            close_ssh_tunnel()

        if app is None or not app.disconnect_requested:
            return


if __name__ == "__main__":
    main()
