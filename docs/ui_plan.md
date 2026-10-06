# Desktop UI plan

**STOP — wait for approval before more application code.**

This plan supersedes the Sep 28 draft. It reflects the **current tree** (Phase 3 design system + Phase 4 grouped nav / connect keep-alive already landed) and the **updated brief** (header user menu, no duplicate identity, accent-bar nav, density).

## Preconditions

1. Work only under `desktop/` (+ `docs/`, PyInstaller spec if modules change). No `backend/` / `api/` / `frontend/` for styling.
2. Do not edit failing restore-gap / monitoring audit tests.
3. Commit only when asked.
4. Launchable tree: `python run_app.py` after `venv` activate.

## What is already done (skip unless revising)

- Design tokens + shared components (`theme.py`, `design_system.py`, `overlay.py`)
- Workspace container (views kept across nav; connect no longer `_reload_views()`-destroys)
- Grouped nav + Migration / Patching / FDW / Monitoring / Audit / Execution Log
- Connect gate includes ops pages
- Reports theming; footer session poll + short version; inventory row polish

## What the new brief still requires (gaps)

1. **Shell chrome rewrite**
   - Remove sidebar **brand+profile duplicate** (keep product name compact or move to header).
   - Move **Theme + Sign Out** into a **header user menu** (ghost Sign Out, not filled red).
   - **Header:** left = active connection (server · engine · SSH/direct · status); right = Refresh (context), last-updated, alerts (if real), user/role menu. No global search (no index).
   - Active nav = **3 px left accent + subtle tint**, not solid blue pill.
   - Nav rows ~34–36 px; groups tight so **maximized window needs no sidebar scroll**.
   - Status footer = **labelled segments at fixed positions** (not one pipe/dot soup). Keep real metrics only; idle state when disconnected.
2. **Dashboard (Phase 5)** — density, “This workstation” labels, fix contradictory sub-labels, no stub metrics as truth.
3. **Servers + Databases (Phase 6)** — servers as table; explorer honesty for stubs; drawers later.
4. **Drawer rollout (Phase 7)** — use existing `overlay.Drawer` (non-modal ≥1200 px, modal below).
5. **Backup / Restore wizards (Phase 8)** — one Stepper; typed confirm for destructive restore.
6. **Migration / Patch / FDW (Phases 9–10)** — restyle + confirm; failures visible.
7. **Monitoring / Logs / Reports / Settings (Phases 11–12)** — restyle; Audit table from `get_audit_logs`.
8. **Empty / loading / error / toasts (Phase 13)** — sweep.
9. **Regression (Phase 14)** — checklist for live engines.

## Dead code decision

| File | Decision |
|---|---|
| `sidebar.py`, `sidebar_databases.py`, `nav_icons.py` | **Stay deleted** — live sidebar is `app.py` Lucide nav |
| `auth_dialog.py` | **Keep** — real login |
| `connection_dialog.py` | **Keep** until `scripts/build_connection_panel.py` is removed; do not build UI on it |
| `overlay_manager.py` / `auth_panel.py` | **Do not create** — use `overlay.py` |

## Role gating

Proposal only (not in next coding phase): viewer / operator / admin gates in `navigate()` + Run buttons. **No implementation until you approve a dedicated phase.**

## Recommended next coding phase (after you approve)

### Phase 4b — Shell chrome — **DONE** (await Phase 5 approval)

Delivered: slim sidebar (no profile / Theme / Sign Out footer), 3px accent-bar nav,
`ShellHeader` (connection · engine · SSH/direct · status + Refresh + user menu with Theme
and ghost Sign Out), labelled footer segments, dashboard greeting identity removed.

### Phase 5 — Dashboard (next after approval)

## Colour / density (apply as pages are touched)

Use brief token table (Canvas/Surface/…); semantic colour only on status; Sign Out ghost; KPI density targets when Dashboard is rewritten; tabular figures where practical in CTk.

## Test plan

Each phase: `pytest tests/` (no new failures vs 46/6) + `python -c "import desktop.app"`.

Manual (you): login, theme, nav groups, connect/disconnect, header menu, footer metrics, gated pages prompt.

Live engines checklist deferred to Phase 14.
