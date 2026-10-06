# Database Automation Pro

**Enterprise database administration automation platform** for Windows (and API usage anywhere). One place to connect, back up, restore, monitor, patch, migrate, and report across multiple database engines — without jumping between vendor tools.

---

## End goal

Give DBAs and ops teams a **single desktop console + optional REST API** that:

1. Connects securely to **PostgreSQL, MySQL, MongoDB, Microsoft SQL Server, and Oracle** (local or remote via SSH).
2. Runs **backup and restore** with engine-native tools (`pg_dump` / `pg_restore`, `mysqldump`, `mongodump` / `mongorestore`, MSSQL backup, Oracle Data Pump).
3. Surfaces **live health and performance** (connections, latency, host CPU/RAM/disk, audit-based alerts).
4. Supports **patch scripts, FDW setup (PostgreSQL), schema/data migration**, and **scheduled jobs**.
5. Produces **reports and execution logs** operators can export (CSV / PDF).
6. Can be packaged as a **Windows desktop installer** for demos and field use.

The long-term product direction is a production-ready **DBA operations platform**: fewer manual SSH sessions, fewer one-off scripts, and an auditable trail of who did what.

---

## Who this is for

| Role | How they use it |
|------|-----------------|
| **Admin** | Full access: connections, backup/restore, settings, users (API RBAC) |
| **Operator** | Day-to-day backup, restore, monitoring, reports |
| **Viewer** | Read-oriented monitoring and reports (API roles; desktop roles are mainly display today) |

Default demo login (API / auth): `admin` / `admin123` (change in production).

---

## What you can do in the app

| Area | What it does |
|------|----------------|
| **Servers** | Save connection profiles, test connect, optional Linux SSH tunnel |
| **Databases** | Browse server databases / explorer-style inventory |
| **Backup** | Logical (and where supported physical) backups with scopes and paths |
| **Restore** | Restore from local or remote backup artifacts |
| **Dashboard** | KPIs, host gauges, DB performance chart, security/activity panels |
| **Logs** | In-app execution log (filter, search, export) |
| **Reports** | Filterable operations report + CSV/PDF export |
| **Settings** | Theme (dark/light) and app preferences |

API-only (also used by services under the hood): **patches**, **FDW**, **migrations**, **scheduler**, **monitoring health**.

---

## Supported databases

| Engine | Python driver | Typical CLI / tools |
|--------|---------------|---------------------|
| PostgreSQL | `psycopg2-binary` | `pg_dump`, `pg_restore`, `psql`, `pg_basebackup` |
| MySQL | `mysql-connector-python` | `mysqldump`, `mysql` |
| MongoDB | `pymongo` | `mongodump`, `mongorestore` |
| Microsoft SQL Server | `pyodbc` | ODBC Driver + T-SQL `BACKUP` / `RESTORE` |
| Oracle | `oracledb` | Data Pump (`expdp` / `impdp`) where wired |

Remote Linux hosts use **Paramiko** (SSH) for tunnels and remote backup directories.

---

## Tech stack — libraries and what they are for

### Application & API

| Library | Role |
|---------|------|
| **Python 3.10+** | Runtime |
| **FastAPI** | REST API (`api/`), OpenAPI/Swagger at `/docs` |
| **Uvicorn** | ASGI server for the API |
| **python-multipart** | File uploads in API forms |
| **CustomTkinter** | Modern desktop UI (`desktop/`) |
| **Pillow** | Image handling in the desktop UI |
| **resvg_py** | SVG → raster for Lucide icons in the sidebar |
| **fpdf2** | PDF export (reports / logs) |

### Auth & security

| Library | Role |
|---------|------|
| **python-jose** | JWT create/verify for the API |
| **passlib** + **bcrypt** | Password hashing |
| **Paramiko** | SSH client / tunnels for remote DB hosts |

### Configuration & data models

| Library | Role |
|---------|------|
| **Pydantic** / **pydantic-settings** | Request/response models, settings validation |
| **PyYAML** | Profile configs under `config/` |
| **python-dotenv** | Load `.env` overrides |

### Scheduling & host metrics

| Library | Role |
|---------|------|
| **APScheduler** | Cron-style scheduled backups (API/scheduler service) |
| **psutil** | Local host CPU, memory, disk, network for the dashboard |

### Testing

| Library | Role |
|---------|------|
| **pytest** / **pytest-asyncio** | Unit and integration tests |
| **httpx** | API test client |

### Packaging (deployment)

| Tool | Role |
|------|------|
| **PyInstaller** | Bundle the desktop app (`deployment/`) |
| **Inno Setup** | Windows installer scripts for demos |

Install everything with:

```bash
pip install -r requirements.txt
```

---

## Data models (schemas)

Shared contracts live in `backend/models/schemas.py` (Pydantic). Important types:

### Enums (engines and operations)

| Model / enum | Meaning |
|--------------|---------|
| `DatabaseType` | `postgresql`, `mysql`, `mongodb`, `mssql`, `oracle` |
| `UserRole` | `admin`, `operator`, `viewer` |
| `BackupCategory` | `logical` / `physical` |
| `BackupMode` | `full` / `incremental` / `differential` |
| `BackupMethod` | Engine-specific backup methods |
| `PgBackupScope` / `PgDumpFormat` | PostgreSQL dump scopes and formats |
| `MySQLBackupScope` / `MySQLDumpFormat` | MySQL dump options |
| `MongoRestoreScope` | Mongo restore granularity |
| `RestoreSourceType` / `RestoreTargetType` | Where restore reads from / writes to |
| `OperationStatus` | Running / success / failed lifecycle |

### Request / response models

| Model | Used for |
|-------|----------|
| `ConnectionRequest` | Connect / profile payloads |
| `BackupRequest` | Start a backup |
| `RestoreRequest` | Start a restore |
| `PatchRequest` | Run SQL patch files |
| `FDWRequest` | PostgreSQL foreign data wrapper setup |
| `MigrationRequest` | Schema/data migration jobs |
| `OperationResult` | Status of a long-running op |
| `PatchHistoryEntry` | Patch history rows |
| `HealthCheckResult` | Monitoring health checks |
| `DashboardStats` | Aggregated dashboard numbers |
| `UserLogin` / `UserCreate` / `UserResponse` / `TokenResponse` | Auth API |

Desktop-specific session objects (connection result, employee session, saved servers JSON) sit under `desktop/` and `config/saved_servers.json`.

---

## Architecture (how the pieces fit)

```
┌─────────────────────┐     ┌─────────────────────┐
│  Desktop (CustomTk) │     │  FastAPI REST API   │
│  python -m desktop  │     │  uvicorn api.main   │
│  or run_app.py      │     │  JWT + RBAC         │
└──────────┬──────────┘     └──────────┬──────────┘
           │                           │
           └─────────────┬─────────────┘
                         ▼
              backend/services/
     Backup · Restore · Patch · FDW · Migration
     Scheduler · Monitoring · Reports · SSH
                         │
                         ▼
              backend/connectors/
        PostgreSQL · MySQL · MongoDB · MSSQL · Oracle
                         │
                         ▼
              Real databases + CLI tools
```

- **Desktop** calls backend services **directly** (no requirement to run the API for day-to-day UI work).
- **API** exposes the same capabilities over HTTP for integration, automation, and Swagger exploration.
- **Connectors** abstract engine differences; services own workflows (backup chains, catalogs, audit).

---

## Project layout

| Path | Purpose |
|------|---------|
| `desktop/` | CustomTkinter app: views, theme, connection panel, reports, logs |
| `backend/connectors/` | Per-engine DB drivers |
| `backend/services/` | Business logic (backup, restore, monitoring, …) |
| `backend/models/` | Pydantic schemas / enums |
| `backend/core/` | Config, logging, exceptions |
| `api/` | FastAPI app, auth, routes |
| `config/` | YAML profiles, saved servers, UI preferences |
| `frontend/` | Optional web dashboard assets |
| `tests/` | pytest suite |
| `docs/` | Architecture, API, installation |
| `deployment/` | Spec / build scripts for Windows packaging |
| `logs/` | `app.log`, `audit.log` |
| `run_app.py` | Desktop launcher (also used by PyInstaller) |
| `run.py` | CLI entry for modes (e.g. desktop) |

---

## Quick start

### 1. Environment

```bash
cd database-automation-pro
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Config (optional)

```bash
copy .env.example .env    # Windows
# cp .env.example .env    # Linux/macOS
```

Profiles: `config/default.yaml`, `dev.yaml`, `test.yaml`, `prod.yaml`.  
Overrides use the `DAP_` prefix (see `docs/installation.md`).

### 3. Run the desktop app

```bash
python run_app.py
# or
python -m desktop.main
```

### 4. Run the API (optional)

```bash
uvicorn api.main:app --reload
```

- Swagger: http://localhost:8000/docs  
- Default login: `admin` / `admin123`

### 5. Tests

```bash
pytest tests/ -v
```

---

## API surface (summary)

| Area | Examples |
|------|----------|
| Auth | `POST /api/v1/auth/login` |
| Backups | `POST/GET /api/v1/backups/` |
| Restore | `POST /api/v1/restore/` |
| Patches | `POST /api/v1/patches/execute` |
| FDW | `POST /api/v1/fdw/setup` |
| Migration | `POST /api/v1/migrations/` |
| Monitoring | `GET /api/v1/monitoring/health` |

Details: [docs/api.md](docs/api.md).

---

## Design system (desktop)

- **Theme tokens** — `desktop/theme.py` (dark / light palettes, spacing, radius, type scale).
- **Shared widgets** — `desktop/design_system.py`, `desktop/overlay.py` (dialogs, toasts).
- Preferences persist in `config/ui_preferences.json` (theme, remembered login).

---

## Prerequisites for real backups

Install engine CLI tools on the machine that runs the job (or on the remote Linux host when using SSH):

- PostgreSQL client tools  
- MySQL client tools  
- MongoDB Database Tools  
- MSSQL: ODBC Driver 17+ for SQL Server  
- Oracle: Instant Client / Data Pump as required  

Without these, connect and monitoring may work, but dump/restore jobs will fail.

---

## Further documentation

- [Architecture](docs/architecture.md)  
- [API reference](docs/api.md)  
- [Installation](docs/installation.md)  
- [Deployment / Windows build](deployment/README.md)  

---

## License

MIT License
