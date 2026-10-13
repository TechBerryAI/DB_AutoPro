"""Windows desktop launcher for Data Automation.

Thin wrapper around the existing entry point (equivalent to ``python run.py
desktop``). It exists so PyInstaller has a single GUI-only script to package and
so that a packaged build has somewhere to report start-up failures. It adds no
application behaviour of its own.
"""

from __future__ import annotations

import os
import pkgutil
import sys
import traceback
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent


def _crash_log_path() -> Path:
    return APP_ROOT / "var" / "logs" / "launcher_error.log"


def _report_startup_failure(exc: BaseException) -> None:
    """Persist a traceback and show it, so a packaged build never dies silently."""
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_path: Path | None = _crash_log_path()
    try:
        assert log_path is not None
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(details + "\n")
    except OSError:
        log_path = None

    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        location = f"\n\nDetails written to:\n{log_path}" if log_path else ""
        messagebox.showerror(
            "Data Automation - startup failed",
            f"{type(exc).__name__}: {exc}{location}",
        )
        root.destroy()
    except Exception:
        print(details, file=sys.stderr)


def _selftest() -> int:
    """Import every UI/service module and report the result to logs/selftest.log.

    Used by the build script to prove a packaged build really has all of its
    hidden imports, rather than only proving that the login window appears.
    Triggered with DAP_SELFTEST=1; it never runs during normal use.
    """
    import importlib

    packages = [
        "desktop.views",
        "desktop.explorer",
        "backend.services",
        "backend.connectors",
        "backend.services.mysql_patch",
        "backend.services.mysql_patch.adapters",
    ]
    modules: list[str] = [
        "desktop.app",
        "desktop.main",
        "desktop.theme",
        "desktop.lucide_icons",
    ]
    for package_name in packages:
        package = importlib.import_module(package_name)
        for info in pkgutil.iter_modules(package.__path__):
            modules.append(f"{package_name}.{info.name}")

    failures: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    # Resources the UI loads from disk at runtime.
    from desktop.lucide_icons import _ICONS_DIR, _load_svg

    if not any(_ICONS_DIR.glob("*.svg")):
        failures.append(f"missing lucide SVG assets in {_ICONS_DIR}")
    try:
        _load_svg("mysql_patch")
    except OSError as exc:
        failures.append(f"mysql patch icon: {exc}")
    if not (APP_ROOT / "config" / "default.yaml").exists():
        failures.append("missing config/default.yaml")

    status = "FAIL" if failures else "PASS"
    summary = f"{status}: {len(modules) - len(failures)}/{len(modules)} modules imported"
    report = APP_ROOT / "var" / "logs" / "selftest.log"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join([summary, *failures]), encoding="utf-8")
    return 1 if failures else 0


def main() -> None:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))

    if os.environ.get("DAP_SELFTEST") == "1":
        sys.exit(_selftest())

    from desktop.main import main as run_desktop

    run_desktop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - last-resort launcher guard
        _report_startup_failure(exc)
        sys.exit(1)
