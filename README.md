# Database Automation Pro

A production-ready Python platform for automating database administration tasks across **PostgreSQL**, **MySQL**, **MongoDB**, **MSSQL**, and **Oracle**.

## Features

- **Multi-Database Support** — Unified interface for 5 database engines
- **Backup & Restore** — pg_dump, mysqldump, mongodump, MSSQL/Oracle backups with compression and retention policies
- **Patch Management** — Execute SQL files with history tracking and rollback
- **FDW Management** — PostgreSQL foreign data wrapper setup and validation
- **Migration** — Schema and data migration with progress monitoring
- **Scheduled Backups** — Cron-based automation via APScheduler
- **Desktop App** — Modern CustomTkinter UI with dark/light themes
- **REST API** — FastAPI with JWT auth, RBAC, and Swagger docs
- **Monitoring** — Health checks, audit logs, system metrics dashboard

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   Desktop    │  │  Web Frontend │  │  Swagger UI  │
│ (CustomTk)   │  │   (HTML/JS)   │  │   (/docs)    │
└──────┬───────┘  └──────┬────────┘  └──────┬───────┘
       │                 │                   │
       ▼                 └─────────┬─────────┘
  Business Logic                  ▼
       │                   FastAPI + JWT/RBAC
       └─────────┬─────────┘
                 ▼
         Services Layer
    (Backup, Restore, Patch,
     FDW, Migration, Scheduler)
                 │
                 ▼
       Database Connectors
   (PG, MySQL, Mongo, MSSQL, Oracle)
```

See [docs/architecture.md](docs/architecture.md) for the full architecture diagram.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure (optional)
copy .env.example .env

# Start API server
uvicorn api.main:app --reload

# Start desktop app
python -m desktop.main
```

- API Docs: http://localhost:8000/docs
- Default login: `admin` / `admin123`

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `api/` | FastAPI REST API with authentication |
| `backend/` | Business logic, connectors, models |
| `config/` | YAML configuration profiles |
| `desktop/` | CustomTkinter desktop application |
| `frontend/` | Web dashboard UI |
| `tests/` | Unit and integration tests |
| `docs/` | Architecture, API, and installation docs |
| `logs/` | Application and audit logs |

## Configuration

Environment profiles in `config/` (dev, test, prod) with environment variable overrides:

```bash
DAP_ENVIRONMENT=prod
DAP_JWT__SECRET_KEY=your-secret
DAP_CONNECTIONS__DEFAULT__HOST=localhost
```

## API Endpoints

| Category | Endpoints |
|----------|-----------|
| Auth | `POST /api/v1/auth/login` |
| Backups | `POST/GET /api/v1/backups/` |
| Restore | `POST /api/v1/restore/` |
| Patches | `POST /api/v1/patches/execute` |
| FDW | `POST /api/v1/fdw/setup` |
| Migration | `POST /api/v1/migrations/` |
| Monitoring | `GET /api/v1/monitoring/health` |

Full API documentation: [docs/api.md](docs/api.md)

## Testing

```bash
pytest tests/ -v
```

## Documentation

- [Architecture](docs/architecture.md)
- [API Reference](docs/api.md)
- [Installation Guide](docs/installation.md)

## License

MIT License
