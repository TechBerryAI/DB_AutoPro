# Desktop UI inventory

Verified against the working tree on **29 Sep 2026** after Phase 3–4 landings and subsequent polish. Scope: `desktop/` only.

## Corrections to this brief

These claims in the brief **do not match the code today**. Later phases must follow the code.

| Brief claim | Actual |
|---|---|
| Nav = Dashboard, Servers, Databases, Backup, Restore, Logs, Reports, Settings only | **Grouped nav** already exists: Overview / Infrastructure / Operations / Observability / System. Wired: Migration, Patching, FDW, Monitoring, Execution Log, Audit Log, Reports, Settings. |
| Dashboard is permanent; other pages are `page_overlay`s destroyed on close | **False then and now.** There is no `page_overlay` navigation stack. Pages live in `view_container` (`grid_forget` / `grid`). Connect/disconnect **no longer destroy** views (push `set_connection` + refresh). Theme switch still destroys and rebuilds for baked colours. |
| `overlay_manager.py` / `overlay_dialogs.py` / `auth_panel.py` exist | **Do not exist.** Overlay is `desktop/overlay.py` (Modal, confirm, alert, detail_panel, Drawer, Stepper, Toast) + `desktop/ui_helpers.py` re-exports. Login is `desktop/auth_dialog.py` via `desktop/main.py`. |
| Dead: `auth_dialog.py` | **Live.** Keep it. |
| Dead: `sidebar.py`, `sidebar_databases.py`, `nav_icons.py` | Were deleted in Phase 4 cleanup (not imported). Do not revive. |
| `CONNECTION_REQUIRED_KEYS` = databases, backup, restore, reports | Now also **migration, patching, fdw**. Monitoring + Execution Log + Audit Log stay reachable offline. |
| System metrics block the Tk thread every 5s while overlays sit on Dashboard | Partially fixed: Dashboard poll pauses when not mapped; `get_system_metrics` / DB metrics run on worker threads. App-level **session footer poll** (15s) still runs when connected even off Dashboard. |
| Test baseline 46 passed, 5 failed, 1 skipped | Latest full run: **46 passed, 6 failed, 0 skipped**. Failures: 5× `test_restore_gaps.py` + 1× `test_monitoring_service.py::test_backup_audit_stats…` (live audit log). Do not edit those tests. |

## Already landed (do not redo blindly)

| Work | Status |
|---|---|
| Theme tokens + design_system factories | Phase 3 — present |
| `desktop/overlay.py` confirm / detail_panel / Drawer / Stepper / Toast | Phase 3 — present; Drawer/Stepper/Toast barely used in pages |
| Grouped sidebar + Migration/Patch/FDW/Monitoring/Audit | Phase 4 — present |
| Connect keep-alive (no destroy-all on connect) | Phase 4 — present |
| Reports/date_picker theme tokens | Done earlier |
| Footer idle vs connected; short Version string; session poll | Done (polish) |
| Temporary AppHeader | **Removed** by request (duplicated Riya/Operator) |

## How to launch and build

```powershell
cd c:\Users\DELL\database-automation-pro
.\venv\Scripts\Activate.ps1
python run_app.py
# or
python -m desktop.main
```

Build: `deployment\build.bat` · Spec: `deployment/DataAutomationPro.spec` · minsize 1024×680, starts maximized.

## Navigation (current)

`NAV_GROUPS` in `desktop/app.py`:

- Overview: Dashboard  
- Infrastructure: Servers (+ New / Saved subnav), Databases  
- Operations: Backup, Restore, Migration, Patching (submenu: MySQL Patch Management), FDW  
- Observability: Monitoring, Execution Log, Audit Log, Reports  
- System: Settings  

Sidebar still has **brand card + profile card (Riya)** + Theme + **filled Sign Out** at bottom — this fights the new brief (identity only in header; Theme/Sign Out in user menu; nav without scroll).

## Pages and threads (summary)

| Page | Primary calls | Thread notes |
|---|---|---|
| Login (`auth_dialog.py`) | `employee_store` | Main (local JSON + bcrypt) |
| Dashboard | `MonitoringService`, `fetch_database_performance`, catalogs | Workers for sys + DB poll; paused when hidden |
| Servers | `connection_store`, test connect, SSH | Connect test on daemon thread |
| Databases | explorer / `db_catalog` | Loads on daemon threads |
| Backup / Restore | matching services | Daemon + stage progress callbacks |
| Migration / Patch / FDW | matching services | Daemon (basic forms) |
| Monitoring / Audit | `MonitoringService` | Mostly main for button handlers |
| Execution Log | `ExecutionLogStore` | Main |
| Reports | `ReportService` | Daemon |
| Settings | theme callback | Main |

## Overlay / design system

- Tokens: `theme.py` (light/dark). Components: `design_system.py`. Confirm path: `overlay.confirm` / `design_system.confirm`.
- Hard-coded leftovers still dense in: `backup_view.py`, `restore_view.py`, `connection_panel.py`, parts of `databases_view.py`, Migration/Patch/FDW forms (pre-design-system layout).

## Data gaps (never invent)

- No environment / per-server health-version on saved profiles  
- No desktop scheduler UI (API-only)  
- Host gauges = **this workstation** (`psutil`), not DB host  
- Progress = stage messages, not true percent  
- MSSQL / Mongo explorer = stub  
- Passwords in `saved_servers.json` / remember-password prefs = plaintext technical debt  

## Risks

- Sidebar scroll on maximized windows with full group list + profile + Theme + Sign Out  
- Theme switch still destroys all views  
- Drawer/wizard not rolled onto Backup/Restore yet  
- Roles display-only  

## Test baseline

`pytest tests/` → **46 passed, 6 failed** (restore gaps + monitoring audit). No UI automated tests.
