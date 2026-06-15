# API Documentation

Base URL: `http://localhost:8000/api/v1`

Interactive documentation: `http://localhost:8000/docs`

## Authentication

### POST /auth/login

Authenticate and receive JWT tokens.

**Request:**
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

### GET /auth/me

Get current user info. Requires Bearer token.

---

## Backups

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| POST | `/backups/` | backup:create | Create a new backup |
| GET | `/backups/` | backup:read | List all backups |
| GET | `/backups/{operation_id}` | backup:read | Get operation status |
| POST | `/backups/validate` | backup:read | Validate backup file |
| POST | `/backups/retention/apply` | backup:create | Apply retention policy |

**Create Backup Request:**
```json
{
  "profile": "default",
  "db_type": "postgresql",
  "method": "pg_dump",
  "database": "mydb",
  "compress": true
}
```

Supported methods:
- PostgreSQL: `pg_dump`, `pg_dumpall`, `pg_basebackup`
- MySQL: `mysqldump`
- MongoDB: `mongodump`
- MSSQL: `mssql_backup`

---

## Restore

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| POST | `/restore/` | restore:create | Restore from backup |

**Request:**
```json
{
  "profile": "default",
  "db_type": "postgresql",
  "backup_path": "./backups/backup.sql.gz",
  "validate": true
}
```

---

## Patches

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| POST | `/patches/execute` | patch:create | Execute SQL patch |
| GET | `/patches/history` | patch:read | Get patch history |
| POST | `/patches/rollback/{patch_id}` | patch:rollback | Rollback patch |
| GET | `/patches/{patch_id}/report` | patch:read | Get execution report |

---

## FDW (Foreign Data Wrappers)

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| POST | `/fdw/setup` | fdw:manage | Complete FDW setup |
| POST | `/fdw/extension` | fdw:manage | Create extension |
| POST | `/fdw/validate` | fdw:manage | Validate FDW config |

---

## Migrations

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| POST | `/migrations/` | migration:create | Run migration |
| GET | `/migrations/{id}/progress` | migration:read | Get progress |

---

## Monitoring

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| GET | `/monitoring/health` | monitoring:read | Health checks |
| GET | `/monitoring/dashboard` | monitoring:read | Dashboard stats |
| GET | `/monitoring/metrics` | monitoring:read | System metrics |
| GET | `/monitoring/backups/report` | monitoring:read | Backup report |
| GET | `/monitoring/audit-logs` | monitoring:read | Audit log entries |
| GET | `/monitoring/scheduler/jobs` | scheduler:manage | Scheduled jobs |

---

## Default Users

| Username | Password | Role |
|----------|----------|------|
| admin | admin123 | admin |
| operator | operator123 | operator |
| viewer | viewer123 | viewer |

## Error Responses

```json
{
  "detail": "Error message"
}
```

Status codes: 400 (validation), 401 (auth), 403 (forbidden), 404 (not found), 500 (server error)
