# Data Automation Pro — Windows build (DEMO)

Temporary packaging setup used to demonstrate how the final client deployment
will look. It does **not** change the application: every file under `backend/`,
`desktop/`, `api/`, `config/` and `frontend/` is untouched.

Current version: **0.0.1-demo** (see `version.txt`).

## Build

```bat
deployment\build.bat
```

| Command                    | Does                                            |
| -------------------------- | ----------------------------------------------- |
| `build.bat`                | Self-test, build the exe, build the installer   |
| `build.bat exe`            | Executable only                                 |
| `build.bat installer`      | Installer only (reuses `dist\Data Automation Pro\`) |

Outputs:

- `dist\Data Automation Pro\Data Automation Pro.exe` — the packaged application
- `dist\Data Automation Pro Setup Demo.exe` — the installer

Prerequisites: the project `venv`, `pyinstaller`, and Inno Setup 6
(`winget install --id JRSoftware.InnoSetup`).

## How the packaging works

`run_app.py` (project root) is a thin launcher equivalent to
`python run.py desktop`. It adds no application behaviour; it only exists so
PyInstaller has one GUI-only entry script, and so a packaged build can report a
start-up failure to `var\logs\launcher_error.log` instead of dying silently.

The spec sets `contents_directory='.'`, which keeps PyInstaller's flat one-dir
layout. That makes the frozen app's root the install folder itself, so every
`Path(__file__).parents[...]` lookup in the existing source resolves exactly as
it does from a checkout:

```
<install dir>/Data Automation Pro.exe
<install dir>/config/            default.yaml, dev.yaml, prod.yaml, test.yaml
<install dir>/var/logs/          audit.log, app.log, patch_history.json
<install dir>/var/backups/       default backup output directory
<install dir>/var/data/          saved servers, employees, UI preferences
<install dir>/desktop/assets/lucide/
```

This is why **no application module needed to be modified** for packaging.

### Files deliberately not shipped

`var\data\saved_servers.json` and `var\data\ui_preferences.json` hold **plaintext
credentials** and are never bundled. `var\data\employees.json` is not bundled
either — `desktop/employee_store.py` re-seeds a default `admin` / `admin123`
account on first launch. A fresh install therefore contains no secrets.

### Install model

Per-user install into `%LOCALAPPDATA%\Programs\Data Automation Pro`
(`PrivilegesRequired=lowest`). No UAC prompt, and the app can write its own
`config/`, `var\logs` and `var\backups` folders. Installing into `Program Files`
would break those writes, because the app writes next to its binaries.

Re-running the installer upgrades in place: the fixed `AppId` prevents a second
Add/Remove Programs entry, `var\data\*.json`, `var\logs\*` and `var\backups\*` are
excluded from the payload, and those folders are marked `uninsneveruninstall`
so user data survives both upgrade and uninstall.

## Releasing a new version

Edit `version.txt` only:

```
APP_VERSION=1.0.0
FILE_VERSION=1.0.0.0
```

then re-run `build.bat`. `APP_VERSION` flows into the exe's version resource,
the installer, and Add/Remove Programs. Do **not** change `AppId` in
`installer\DataAutomationPro.iss` — it is what makes upgrades upgrades.

## Known limitations of this demo build

1. **Placeholder icon.** `assets\DataAutomationPro.ico` is a generated blue "DA"
   tile, produced by `assets\make_placeholder_icon.py`. Replace it with real
   artwork.
2. **Unsigned.** SmartScreen will warn on first run. Needs an Authenticode
   certificate for production.
3. **Config lives beside the binaries**, not in a separate per-user data
   directory (see below).
4. **Bundled external tools are not included.** Backup/restore features that
   shell out to `pg_dump`, `mysqldump`, `mongodump`, `psql` etc. still expect
   those binaries on the target machine's `PATH`.
5. **x64 only**, tested on Windows 10.
6. Desktop application only — the FastAPI service (`api/`, `frontend/`) is not
   packaged.

## For the production release

- Replace the placeholder icon; add `LicenseFile` and a publisher URL.
- Sign `Data Automation Pro.exe` and the setup executable.
- **Move writable state out of the install directory.** Introduce a single
  helper that returns `%LOCALAPPDATA%\Data Automation Pro` when `sys.frozen` is set
  and the project root otherwise, then use it in the five places that currently
  derive paths from `__file__`:
  `backend/core/config.py` (`PROJECT_ROOT`, `CONFIG_DIR`, `LOGS_DIR`,
  `BackupConfig.output_dir`), `backend/services/patch_service.py`
  (`HISTORY_FILE`), `desktop/connection_store.py` (`STORE_PATH`),
  `desktop/employee_store.py` (`EMPLOYEES_PATH`) and `desktop/theme.py`
  (`THEME_PREF_PATH`). That also allows a per-machine `Program Files` install.
- Encrypt stored connection passwords (currently plaintext in
  `saved_servers.json`) and stop persisting the login password in
  `ui_preferences.json`.
- Set a real `jwt.secret_key` in `config\prod.yaml` and ship
  `DAP_ENVIRONMENT=prod`.
- Decide whether required DB client tools are bundled or listed as
  prerequisites.
