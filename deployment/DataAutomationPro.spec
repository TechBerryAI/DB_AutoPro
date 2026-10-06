# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Data Automation Pro Windows desktop build.

Build with:  pyinstaller deployment/DataAutomationPro.spec --noconfirm
(or just run deployment/build.bat, which also produces the installer).

Layout note
-----------
``contents_directory='.'`` keeps the classic flat one-dir layout, so the frozen
app's ``sys._MEIPASS`` is the application folder itself. That makes every
``Path(__file__).parents[...]`` lookup in the existing source resolve exactly as
it does when running from a checkout:

    <app>/config      <- YAML profiles + runtime JSON state
    <app>/logs        <- audit.log, app.log, patch_history.json
    <app>/backups     <- default backup output directory
    <app>/desktop/assets/lucide

No application module had to be modified to support packaging.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# --- paths (all relative to this spec, never to a developer's machine) --------
SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent
ICON_PATH = SPEC_DIR / "assets" / "DataAutomationPro.ico"       # TEMPORARY placeholder
WORK_DIR = SPEC_DIR / "build"

APP_NAME = "Data Automation Pro"
ENTRY_SCRIPT = str(PROJECT_ROOT / "run_app.py")


def _read_versions() -> tuple[str, str]:
    """Read APP_VERSION / FILE_VERSION from deployment/version.txt."""
    values = {}
    version_file = SPEC_DIR / "version.txt"
    if version_file.exists():
        for line in version_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return (
        os.environ.get("DAP_APP_VERSION") or values.get("APP_VERSION", "0.0.0-dev"),
        os.environ.get("DAP_FILE_VERSION") or values.get("FILE_VERSION", "0.0.0.0"),
    )


APP_VERSION, FILE_VERSION = _read_versions()
VERSION_TUPLE = tuple(int(part) for part in FILE_VERSION.split("."))


def _write_version_resource() -> str:
    """Emit the Windows VERSIONINFO resource consumed by EXE(version=...)."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = WORK_DIR / "file_version_info.txt"
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION_TUPLE}, prodvers={VERSION_TUPLE},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'TechBerry AI'),
        StringStruct('FileDescription', '{APP_NAME}'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('InternalName', 'DataAutomationPro'),
        StringStruct('OriginalFilename', 'Data Automation Pro.exe'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return str(target)


# --- data files --------------------------------------------------------------
# Only what the desktop app actually reads at runtime.
#
# Deliberately NOT bundled: config/saved_servers.json, config/ui_preferences.json
# and config/employees.json. The first two hold plaintext credentials and must
# never ship inside a distributable installer; employees.json is re-seeded
# automatically by desktop/employee_store.py on first launch.
datas = [(str(path), "config") for path in sorted((PROJECT_ROOT / "config").glob("*.yaml"))]
datas += [
    (str(PROJECT_ROOT / "desktop" / "assets" / "lucide"), "desktop/assets/lucide"),
]
datas += collect_data_files("customtkinter")

binaries = []
for package in ("resvg_py", "bcrypt"):
    try:
        binaries += collect_dynamic_libs(package)
    except Exception:  # package layout varies; the normal analysis still finds it
        pass

# --- imports reached only from inside functions / by driver name -------------
hiddenimports = [
    "psycopg2",
    "pymongo",
    "pyodbc",
    "oracledb",
    "mysql.connector",
    "mysql.connector.locales.eng.client_error",
    "mysql.connector.plugins.mysql_native_password",
    "mysql.connector.plugins.caching_sha2_password",
    "paramiko",
    "bcrypt",
    "fpdf",
    "resvg_py",
    "PIL._tkinter_finder",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
]

# View modules that exist in desktop/views but are not referenced by the running
# UI yet, so the import graph does not reach them. Bundled anyway, so the
# packaged app stays complete if they are wired up later.
hiddenimports += [
    "desktop.views.migration_view",
    "desktop.views.monitoring_view",
    "desktop.views.patch_view",
    "desktop.overlay",
    "desktop.ui_helpers",
    "desktop.shell_header",
]

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "matplotlib", "numpy.testing"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Data Automation Pro",
    contents_directory=".",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
    version=_write_version_resource(),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
