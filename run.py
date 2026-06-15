"""Application entry points."""

import sys


def run_api() -> None:
    import uvicorn
    from backend.core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )


def run_desktop() -> None:
    from desktop.main import main
    main()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "desktop":
        run_desktop()
    else:
        run_api()
