"""Desktop application entry point."""

from desktop.app import DatabaseAutomationApp


def main() -> None:
    app = DatabaseAutomationApp()
    app.mainloop()


if __name__ == "__main__":
    main()
