# Installation Guide

## Prerequisites

- Python 3.10 or higher
- pip package manager
- Database CLI tools (as needed):
  - PostgreSQL: `pg_dump`, `pg_restore`, `psql`, `pg_basebackup`
  - MySQL: `mysqldump`, `mysql`
  - MongoDB: `mongodump`, `mongorestore`
  - MSSQL: ODBC Driver 17 for SQL Server
  - Oracle: Data Pump (`expdp`)

## Quick Start

### 1. Clone and Setup

```bash
cd database-automation-pro
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment

```bash
copy .env.example .env    # Windows
# cp .env.example .env      # Linux/macOS
```

Edit `.env` or YAML files in `config/` for your database connections.

### 3. Set Environment Profile

```bash
# Windows PowerShell
$env:DAP_ENVIRONMENT = "dev"

# Linux/macOS
export DAP_ENVIRONMENT=dev
```

### 4. Run the API Server

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Access:
- API: http://localhost:8000
- Swagger Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### 5. Run the Desktop Application

```bash
python -m desktop.main
```

### 6. Run the Web Frontend

Serve the `frontend/` directory with any static file server:

```bash
# Python built-in
python -m http.server 3000 --directory frontend
```

Open http://localhost:3000 and login with `admin` / `admin123`.

## Configuration

### YAML Profiles

Configuration files in `config/`:
- `default.yaml` — Base settings
- `dev.yaml` — Development overrides
- `test.yaml` — Test environment
- `prod.yaml` — Production overrides

### Environment Variables

All settings can be overridden with `DAP_` prefixed env vars:

```
DAP_ENVIRONMENT=prod
DAP_JWT__SECRET_KEY=your-production-secret
DAP_CONNECTIONS__DEFAULT__HOST=db.example.com
DAP_CONNECTIONS__DEFAULT__PASSWORD=secret
DAP_BACKUP__RETENTION_DAYS=90
```

Nested keys use `__` as delimiter.

### Secrets Management

Option 1: Environment variables (recommended for production)
Option 2: Secrets file via `DAP_SECRETS_FILE=./secrets.env`

```
DAP_JWT__SECRET_KEY=production-key
DAP_CONNECTIONS__DEFAULT__PASSWORD=db-password
```

## Running Tests

```bash
pytest tests/ -v
```

## Production Deployment

1. Set `DAP_ENVIRONMENT=prod`
2. Change JWT secret key
3. Configure proper database credentials
4. Run with production ASGI server:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

5. Use a reverse proxy (nginx) for HTTPS
6. Ensure backup output directory has sufficient disk space
7. Configure log rotation for `logs/` directory

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `pg_dump not found` | Install PostgreSQL client tools and add to PATH |
| `psycopg2 not installed` | Run `pip install psycopg2-binary` |
| Connection refused | Verify database host/port in config |
| 401 Unauthorized | Check JWT token expiry, re-login |
| bcrypt errors | Ensure `bcrypt>=4.0.0,<5.0.0` is installed |

## Directory Structure

```
database-automation-pro/
├── api/           # FastAPI REST API
├── backend/       # Business logic & connectors
├── config/        # YAML configuration profiles
├── desktop/       # CustomTkinter desktop app
├── docs/          # Documentation
├── frontend/      # Web UI
├── logs/          # Application & audit logs
├── tests/         # Unit tests
├── requirements.txt
└── README.md
```
