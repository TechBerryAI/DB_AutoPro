"""Generate DB_AutoPro_Technical_Report.pdf (ReportLab). Run: python docs/build_technical_report.py"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Preformatted, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

OUT = Path(__file__).resolve().parents[1] / "DB_AutoPro_Technical_Report.pdf"
NAVY = colors.HexColor("#14325c")
BLUE = colors.HexColor("#2563eb")
LIGHT = colors.HexColor("#eef3fb")
GRID = colors.HexColor("#c5d0e0")

ss = getSampleStyleSheet()
body = ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica", fontSize=10, leading=14.5,
                      alignment=TA_JUSTIFY, spaceAfter=6)
h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=18, textColor=NAVY,
                    spaceBefore=4, spaceAfter=10)
h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, textColor=BLUE,
                    spaceBefore=10, spaceAfter=5)
cell = ParagraphStyle("cell", parent=body, fontSize=8.8, leading=11.5, alignment=0, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=14, bulletIndent=3, spaceAfter=2, alignment=0)
code = ParagraphStyle("code", fontName="Courier", fontSize=8, leading=10, backColor=LIGHT,
                      borderColor=GRID, borderWidth=0.5, borderPadding=5, spaceAfter=8, spaceBefore=2)


class Doc(BaseDocTemplate):
    def __init__(self, path, **kw):
        super().__init__(path, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm,
                         topMargin=22 * mm, bottomMargin=20 * mm, title="Database Automation Pro - Technical Report",
                         author="DB AutoPro Team", **kw)
        fr = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([PageTemplate(id="cover", frames=[fr], onPage=lambda c, d: None),
                               PageTemplate(id="main", frames=[fr], onPage=self.decorate)])

    def decorate(self, c, d):
        c.saveState()
        c.setStrokeColor(GRID)
        c.line(22 * mm, A4[1] - 16 * mm, A4[0] - 22 * mm, A4[1] - 16 * mm)
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.grey)
        c.drawString(22 * mm, A4[1] - 13 * mm, "Database Automation Pro - Technical Report")
        c.drawRightString(A4[0] - 22 * mm, A4[1] - 13 * mm, "Confidential - Internal")
        c.line(22 * mm, 15 * mm, A4[0] - 22 * mm, 15 * mm)
        c.drawCentredString(A4[0] / 2, 10 * mm, f"Page {d.page}")
        c.restoreState()

    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and f.style.name in ("h1", "h2"):
            lvl = 0 if f.style.name == "h1" else 1
            t = f.getPlainText()
            key = f"h{id(f)}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (lvl, t, self.page, key))


def P(t, s=body): return Paragraph(t, s)
def B(items): return [Paragraph(i, bullet, bulletText="•") for i in items]


def table(rows, widths, header=True):
    data = []
    for r, row in enumerate(rows):
        data.append([Paragraph(str(c), cellb if (header and r == 0) else cell) for c in row])
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if header else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
        for i in range(2, len(rows), 2):
            st.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(TableStyle(st))
    return [t, Spacer(1, 8)]


def diagram():
    """Layered architecture drawn as a table of boxes."""
    def box(txt, bg):
        return Paragraph(f"<para alignment='center'><b>{txt}</b></para>",
                         ParagraphStyle("d", parent=cell, textColor=colors.white if bg != LIGHT else NAVY))
    rows = [
        ["Presentation", "Desktop app (CustomTkinter)  |  Web dashboard (frontend/)  |  Swagger /docs"],
        ["API layer", "FastAPI  -  JWT authentication  -  RBAC  -  /api/v1 routers"],
        ["Services", "Backup | Restore | Patch | FDW | Migration | Scheduler | Monitoring | Reports | SSH"],
        ["Connectors", "PostgreSQL | MySQL | MongoDB | SQL Server | Oracle  (BaseConnector + factory)"],
        ["External", "Database engines, native CLI tools (pg_dump, mysqldump, mongodump ...), remote Linux hosts"],
    ]
    shades = [BLUE, colors.HexColor("#1d4ed8"), NAVY, colors.HexColor("#0f2646"), colors.HexColor("#475569")]
    data = [[box(r[0], s), Paragraph(r[1], ParagraphStyle("x", parent=cell, alignment=TA_CENTER))]
            for r, s in zip(rows, shades)]
    t = Table(data, colWidths=[32 * mm, 138 * mm], rowHeights=[13 * mm] * len(rows))
    st = [("GRID", (0, 0), (-1, -1), 0.6, colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("BACKGROUND", (1, 0), (1, -1), LIGHT)]
    for i, s in enumerate(shades):
        st.append(("BACKGROUND", (0, i), (0, i), s))
    t.setStyle(TableStyle(st))
    return [t, P("<i>Figure 1 - Layered architecture. The desktop client calls services directly; the REST API exposes the same services over HTTP.</i>", cell), Spacer(1, 8)]


S = []

# ---------------- Cover ----------------
S += [Spacer(1, 55 * mm),
      Paragraph("<font size=30 color='#14325c'><b>Database Automation Pro</b></font>", ParagraphStyle("c", alignment=TA_CENTER, leading=36)),
      Spacer(1, 6),
      Paragraph("<font size=15 color='#2563eb'>Technical Report</font>", ParagraphStyle("c2", alignment=TA_CENTER, leading=20)),
      Spacer(1, 10),
      Paragraph("Enterprise database administration automation platform for PostgreSQL, MySQL, MongoDB, Microsoft SQL Server and Oracle",
                ParagraphStyle("c3", parent=body, alignment=TA_CENTER, textColor=colors.grey)),
      Spacer(1, 45 * mm)]
S += table([["Project", "Database Automation Pro (DB AutoPro / Data Automation Pro)"],
            ["Package version", "1.0.0 (pyproject.toml); installer demo build 0.0.1-demo"],
            ["Repository", "TechBerryAI / DB-AutoPro - branch &lsquo;Ganesh&rsquo; (main branch: production)"],
            ["Report date", "30 October 2026"],
            ["Prepared by", "DB AutoPro Team"],
            ["License", "MIT"]], [40, 130], header=False)
S += [Spacer(1, 4), P("<i>This report was produced by analysing the repository source tree (api/, backend/, desktop/, config/, deployment/, "
                      "tests/, docs/). Limitations described reflect what the code does today.</i>", cell)]
from reportlab.platypus import NextPageTemplate
S += [NextPageTemplate("main"), PageBreak()]

# ---------------- TOC ----------------
S.append(Paragraph("Table of Contents", ParagraphStyle("tochead", parent=h1)))
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle("t0", fontName="Helvetica-Bold", fontSize=10.5, leading=16, leftIndent=0),
                   ParagraphStyle("t1", fontName="Helvetica", fontSize=9.5, leading=13, leftIndent=14)]
S += [toc, PageBreak()]

# ---------------- 1 ----------------
S.append(P("1. Executive Summary", h1))
S.append(P("Database Automation Pro is a Python platform that gives database administrators one console, and an optional REST API, "
           "for the routine work of running heterogeneous database estates: connecting to servers (locally or through SSH), taking and "
           "restoring backups with each engine's native tooling, watching health and performance, applying reviewed SQL and package patches, "
           "migrating data between engines, scheduling jobs, and exporting auditable reports."))
S.append(P("The product has two front ends over a shared service layer: a Windows desktop application built with CustomTkinter, and a FastAPI "
           "service with JWT authentication and role-based access control. Five engines are supported - PostgreSQL, MySQL, MongoDB, "
           "Microsoft SQL Server and Oracle - behind a common connector interface."))
S += table([["Metric", "Value"],
            ["Python source files (api / backend / desktop)", "14 / 93 / 67"],
            ["Approx. lines of Python (incl. tests)", "~52,800"],
            ["Automated test modules", "40 (pytest)"],
            ["REST endpoints", "26 under /api/v1 plus / and /health"],
            ["Supported engines", "PostgreSQL, MySQL, MongoDB, SQL Server, Oracle"],
            ["Migration pairs implemented", "9 (PostgreSQL to/from other engines, plus PG to PG)"],
            ["Target platform", "Windows 10/11 desktop; API on any OS with Python 3.10+"]], [90, 80])

# ---------------- 2 ----------------
S.append(P("2. Project Objectives and Scope", h1))
S.append(P("Objectives", h2))
S += B(["Provide a single console to connect to PostgreSQL, MySQL, MongoDB, SQL Server and Oracle, locally or over SSH.",
        "Run backup and restore using engine-native tools (pg_dump/pg_restore, mysqldump, mongodump/mongorestore, T-SQL BACKUP/RESTORE, Data Pump).",
        "Surface live health and performance: connections, latency, host CPU/RAM/disk, and audit-based alerts.",
        "Support SQL patch execution, MySQL package patch checking, PostgreSQL FDW setup, schema/data migration and scheduled jobs.",
        "Produce reports and execution logs exportable to CSV and PDF, with an audit trail of who did what.",
        "Package the desktop client as a Windows installer for demos and field use."])
S.append(P("Intended users", h2))
S += table([["Role", "Responsibilities"],
            ["Admin", "Full access: connections, backup/restore, patch rollback, scheduler, user management"],
            ["Operator", "Day-to-day backup, restore, patching, migration, monitoring and reports"],
            ["Viewer", "Read-only monitoring, backup/restore status and reports"]], [30, 140])
S.append(P("Out of scope (current version)", h2))
S += B(["MySQL package <i>installation</i> - the patch workflow runs prechecks and an <font face='Courier'>apt-get -s</font> dry-run only.",
        "Packaging the FastAPI service and web dashboard in the Windows installer (desktop only).",
        "Bundling database client tools (pg_dump, mysqldump, mongodump ...); they must be on PATH.",
        "Migration of views, functions, triggers and other programmable objects (reported, not copied)."])

# ---------------- 3 ----------------
S.append(P("3. System Requirements", h1))
S.append(P("3.1 Functional requirements", h2))
S += table([["ID", "Requirement", "Implemented by"],
            ["FR-01", "Save, test and manage connection profiles; optional SSH tunnel to Linux hosts", "desktop/connection*.py, ssh_tunnel.py, connectors/"],
            ["FR-02", "Browse databases / server inventory", "desktop/explorer/, views/databases_view.py"],
            ["FR-03", "Logical and physical backups; full, incremental, differential modes; compression; retention", "backup_service.py, backup_mode_handlers.py, backup_chain.py"],
            ["FR-04", "Restore from local or remote artifacts with validation", "restore_service.py, mongo_restore.py, restore_catalog.py"],
            ["FR-05", "Live dashboard: KPIs, host gauges, DB performance, security/activity panels", "views/dashboard.py, db_performance.py, monitoring_service.py"],
            ["FR-06", "Execute reviewed SQL patch scripts with history and rollback", "patch_service.py, postgres_patch/"],
            ["FR-07", "MySQL package patch check, plan, approval, dry-run (Ubuntu APT / CentOS DNF adapters)", "mysql_patch/"],
            ["FR-08", "PostgreSQL FDW setup and validation", "fdw_service.py, api/routes/fdw.py"],
            ["FR-09", "Schema/data migration between supported engine pairs with precheck, progress, cancel, reconcile", "pg_migration/, pg_mysql_migration/, to_pg_migration/ ..."],
            ["FR-10", "Cron-style scheduled backups and retention cleanup", "scheduler_service.py (APScheduler)"],
            ["FR-11", "Operations report and execution log with filter, search, CSV/PDF export", "report_service.py, desktop/report_export.py, log_export.py"],
            ["FR-12", "JWT login and role-based authorisation on REST API", "api/auth/, api/routes/"],
            ["FR-13", "Light/dark theme and persisted UI preferences", "desktop/theme.py, settings_view.py"]], [14, 90, 66])
S.append(P("3.2 Non-functional requirements", h2))
S += table([["Area", "Requirement / design response"],
            ["Security", "JWT with expiry; bcrypt hashing; RBAC; pinned SSH host keys; Windows DPAPI for stored secrets; log/redaction of secrets"],
            ["Reliability", "Migration operations persisted in SQLite with crash reconciliation; backup validation; disposable-restore proof"],
            ["Auditability", "Structured JSON logging, separate audit.log, patch and migration history"],
            ["Safety", "Dry-run first, explicit approvals with expiry (30 min), parameterised inserts, locks against concurrent jobs"],
            ["Portability", "Config via YAML profiles and DAP_ environment overrides; runtime state isolated under var/"],
            ["Extensibility", "New engine = new BaseConnector subclass registered in factory; explicit migration capability matrix"],
            ["Testability", "40 pytest modules using fakes for SSH/CLI; live integration tests opt-in via environment variables"]], [32, 138])
S.append(P("3.3 Hardware and software requirements", h2))
S += table([["Item", "Minimum", "Recommended"],
            ["Operating system", "Windows 10 x64 (desktop); Linux/macOS (API only)", "Windows 11 x64"],
            ["Python", "3.10", "3.12 (used by CI)"],
            ["CPU / RAM", "2 cores / 4 GB", "4 cores / 8 GB"],
            ["Disk", "500 MB for app + free space for backups (sized to databases)", "SSD; separate volume for backups"],
            ["Display", "1280x720", "1920x1080"],
            ["Network", "Reach to DB ports (5432, 3306, 27017, 1433, 1521) and SSH 22", "VPN / private network"]], [32, 78, 60])
S.append(P("3.4 External tools (must be installed on the machine running the job, or on the remote Linux host)", h2))
S += table([["Engine", "Tools required"],
            ["PostgreSQL", "pg_dump, pg_restore, psql, pg_basebackup (PostgreSQL client tools)"],
            ["MySQL", "mysqldump, mysql (MySQL client tools)"],
            ["MongoDB", "mongodump, mongorestore (MongoDB Database Tools; located by mongo_tools.py)"],
            ["SQL Server", "ODBC Driver 17+ for SQL Server (pyodbc); T-SQL BACKUP / RESTORE"],
            ["Oracle", "Instant Client / Data Pump (expdp, impdp) where wired"],
            ["Build only", "PyInstaller; Inno Setup 6 (winget install JRSoftware.InnoSetup)"]], [32, 138])

# ---------------- 4 ----------------
S.append(P("4. Technology Stack and Dependencies", h1))
S.append(P("All runtime dependencies are pinned as minimums in <font face='Courier'>requirements.txt</font>. Install with "
           "<font face='Courier'>pip install -r requirements.txt</font>."))
S += table([["Category", "Library (min. version)", "Purpose"],
            ["API", "fastapi 0.109, uvicorn[standard] 0.27, python-multipart 0.0.6", "REST API, ASGI server, uploads"],
            ["Auth", "python-jose[cryptography] 3.3, passlib[bcrypt] 1.7.4, bcrypt >=4.0 &lt;5.0", "JWT, password hashing"],
            ["Config", "pydantic 2.5, pydantic-settings 2.1, pyyaml 6.0.1, python-dotenv 1.0", "Models, settings, YAML, .env"],
            ["DB drivers", "psycopg2-binary 2.9.9, mysql-connector-python 8.2, pymongo 4.6.1, pyodbc 5.0.1, oracledb 2.0", "Engine connectivity"],
            ["Scheduling / metrics", "apscheduler 3.10.4, psutil 5.9.7", "Cron jobs, host CPU/RAM/disk"],
            ["Desktop UI", "customtkinter 5.2.1, Pillow 10.0, resvg_py 0.3, fpdf2 2.7.9", "GUI, images, SVG icons, PDF export"],
            ["SSH", "paramiko 3.4", "Tunnels and remote commands"],
            ["Testing", "pytest 7.4.4, pytest-asyncio 0.23.3, httpx 0.26", "Unit / API tests"],
            ["Utilities", "email-validator 2.1", "Schema validation"]], [28, 82, 60])
S.append(P("Packaging tools: PyInstaller (spec: deployment/DataAutomationPro.spec) and Inno Setup (deployment/installer/DataAutomationPro.iss). "
           "Lint configuration (ruff, line length 100, py310 target) is in pyproject.toml."))

# ---------------- 5 ----------------
S.append(P("5. System Architecture", h1))
S += diagram()
S.append(P("The architecture is layered. The <b>desktop</b> application invokes backend services directly, so the REST API is not "
           "required for day-to-day UI work. The <b>API</b> exposes the same capabilities for automation and integration. "
           "<b>Services</b> own workflows (backup chains, catalogs, audit, patches, migrations); <b>connectors</b> hide engine differences."))
S.append(P("5.1 Design principles", h2))
S += table([["Principle", "Realisation"],
            ["Single responsibility", "One service per domain (backup, restore, patch, FDW, migration ...)"],
            ["Open / closed", "New engines added through new connector classes and registry entries"],
            ["Liskov substitution", "All connectors implement BaseConnector (test_connection, execute_query, execute_script, get_version, health_check)"],
            ["Dependency inversion", "Services obtain connectors via get_connector() / get_connector_from_info()"]], [40, 130])
S.append(P("5.2 Request flow (backup via API)", h2))
S.append(Preformatted(
    "User -> POST /api/v1/backups/\n"
    "  API: validate JWT, check RBAC permission 'backup:create'\n"
    "  BackupService.create_backup(): validate mode -> resolve output path\n"
    "     -> engine handler (pg_dump | mysqldump | mongodump | T-SQL | Data Pump), local or via SSH\n"
    "     -> compress (gzip) -> record chain/catalog -> audit log\n"
    "  <- OperationResult (status, path, size, timings)", code))
S.append(P("5.3 Application lifecycle", h2))
S.append(P("On API start-up (<font face='Courier'>api/main.py</font>) the lifespan handler configures logging, calls "
           "<font face='Courier'>MigrationOperationStore.reconcile_interrupted()</font> to mark migrations left running by a crashed process, "
           "and starts the APScheduler background scheduler; it is stopped on shutdown. CORS is enabled for all origins (development default - "
           "restrict in production)."))

# ---------------- 6 ----------------
S.append(P("6. Project Structure", h1))
S += table([["Path", "Contents"],
            ["api/", "FastAPI app (main.py), auth (jwt_handler, rbac), dependencies, routes: auth, backup, restore, patch, fdw, migration, monitoring"],
            ["backend/core/", "config (YAML + env settings), logging, exceptions, security (secrets, masking), paths"],
            ["backend/models/", "Pydantic schemas and enums (schemas.py)"],
            ["backend/connectors/", "base, postgresql, mysql, mongodb, mssql, oracle"],
            ["backend/services/", "backup/restore/patch/fdw/migration/scheduler/monitoring/report/ssh services; sub-packages mysql_patch, postgres_patch, pg_migration, pg_mysql_migration, pg_sql_migration, pg_mongo_migration, to_pg_migration, migration_framework"],
            ["desktop/", "CustomTkinter app: app.py, views/, explorer/, theme, design system, dialogs, connection store, DPAPI, SSH tunnel and host-trust UI"],
            ["config/", "default.yaml, dev.yaml, test.yaml, prod.yaml, ui_preferences.json"],
            ["frontend/", "Optional lightweight web dashboard (index.html, css, js)"],
            ["deployment/", "PyInstaller spec, Inno Setup script, build.bat, version.txt, assets"],
            ["docs/", "architecture, api, installation, mysql_patch_management, ui_inventory, ui_plan"],
            ["scripts/", "ML dataset export, connection-panel builder"],
            ["tests/", "40 pytest modules"],
            ["var/", "Runtime state: data/, logs/, backups/ (git-ignored contents)"],
            ["run.py / run_app.py", "Entry points: API or desktop; GUI-only launcher used by PyInstaller"],
            [".github/workflows/ci.yml", "CI: Windows, Python 3.12, pytest on push/PR"]], [42, 128])

# ---------------- 7 ----------------
S.append(P("7. Module Descriptions", h1))
S.append(P("7.1 Connectors", h2))
S.append(P("<font face='Courier'>BaseConnector</font> (ABC) takes a <font face='Courier'>ConnectionInfo</font> and defines "
           "<font face='Courier'>test_connection</font>, <font face='Courier'>execute_query</font>, <font face='Courier'>execute_script</font>, "
           "<font face='Courier'>get_version</font> and <font face='Courier'>health_check</font>. "
           "<font face='Courier'>resolve_connection(profile)</font> reads connection settings from configuration; "
           "<font face='Courier'>get_connector(db_type, profile)</font> is the factory."))
S.append(P("7.2 Backup service", h2))
S += B(["Engines: PostgreSQL (pg_dump / pg_dumpall / pg_basebackup), MySQL (mysqldump), MongoDB (local, via SSH, or remote endpoint), SQL Server (T-SQL BACKUP), Oracle (Data Pump).",
        "Categories logical/physical; modes full, incremental, differential - handled by backup_mode_handlers.py with chain metadata in backup_chain.py.",
        "Scopes and formats (PgBackupScope, PgDumpFormat, MySQLBackupScope, MySQLDumpFormat) allow database, schema or table level dumps.",
        "gzip compression, retention policy (apply_retention_policy), local and remote backup listing, and backup validation."])
S.append(P("7.3 Restore service", h2))
S.append(P("Restores from local or remote sources to local or remote targets (RestoreSourceType / RestoreTargetType), with validation. "
           "MongoDB restores are planned by <font face='Courier'>mongo_restore.py</font> (scope: MongoRestoreScope) and tools are located by "
           "<font face='Courier'>mongo_tools.py</font>. <font face='Courier'>restore_catalog.py</font> describes options to the UI."))
S.append(P("7.4 Patch management", h2))
S += B(["<b>SQL script patching</b> (patch_service.py, postgres_patch/): executes reviewed SQL, keeps history in var/logs/patch_history.json, supports rollback (admin only). PostgreSQL workflow includes read-only administrator pre-checks (admin_check.py), identity checks and a script runner.",
        "<b>MySQL package patching</b> (mysql_patch/): discovery, precheck (1 GiB free disk on /, /var and APT cache; SSH host-key trust), preview of package changes, plan with digest, approval (30-minute expiry), dry-run executor and verification. Adapters: Ubuntu APT and CentOS DNF. Jobs are audited to var/data/mysql_patch_jobs.json with secrets redacted.",
        "The workflow refuses: non-Ubuntu/non-APT hosts, third-party repositories, series changes (e.g. 8.0 to 8.4), downgrades, removals, and concurrent jobs. This version never installs packages."])
S.append(P("7.5 FDW service", h2))
S.append(P("Creates the postgres_fdw extension, foreign servers, user mappings and foreign tables, and validates configuration "
           "(POST /fdw/setup, /fdw/extension, /fdw/validate)."))
S.append(P("7.6 Monitoring and reporting", h2))
S.append(P("monitoring_service.py aggregates health checks, dashboard statistics, system metrics (psutil), backup reports and audit-log entries. "
           "report_service.py builds the enterprise report model; the desktop exports CSV/PDF (fpdf2). ml_dataset_export.py and "
           "scripts/export_ml_dataset.py create a unified CSV dataset from logs, audit, health and metrics for ML use."))
S.append(P("7.7 Scheduler", h2))
S.append(P("SchedulerService wraps APScheduler's BackgroundScheduler with CronTrigger jobs for scheduled backups (default cron 0 2 * * *) and retention cleanup."))
S.append(P("7.8 SSH subsystem", h2))
S.append(P("Paramiko-based client (ssh_client.py), tunnel helpers (desktop/ssh_tunnel.py) and pinned host keys (ssh_host_keys.py): the first "
           "connection stores the presented key; a later different key is rejected and never silently replaced. "
           "desktop/remote_directory_browser.py lets users pick remote backup directories."))

# ---------------- 8 ----------------
S.append(P("8. Migration Framework", h1))
S.append(P("Migration is organised around an explicit <b>compatibility matrix</b> (migration_framework/registry.py). A source/target pair is "
           "offered in the UI only when a connector, inspection path, writer, type mapping and verification path exist. Each pair is described by "
           "a PairCapability record (schema, data, type conversion, constraints, indexes, views, programmable objects, verification, limitations)."))
S += table([["Source", "Target", "Engine module"],
            ["PostgreSQL", "PostgreSQL", "pg_migration"],
            ["PostgreSQL", "MySQL", "pg_mysql_migration"],
            ["PostgreSQL", "SQL Server", "pg_sql_migration"],
            ["PostgreSQL", "Oracle", "pg_sql_migration"],
            ["PostgreSQL", "MongoDB", "pg_mongo_migration"],
            ["MySQL", "PostgreSQL", "to_pg_migration"],
            ["SQL Server", "PostgreSQL", "to_pg_migration"],
            ["Oracle", "PostgreSQL", "to_pg_migration"],
            ["MongoDB", "PostgreSQL", "to_pg_migration"]], [40, 40, 90])
S.append(P("Each engine package follows the same pipeline: <b>precheck</b> (no SQL executed) -> <b>schema</b> inspection/DDL -> <b>mapping</b> "
           "(type conversion) -> <b>engine</b> (streamed, parameterised inserts so rows are not all held in memory) -> <b>workflow</b> "
           "(orchestration, progress, cancel) -> verification. The PG-to-MySQL path persists operations in a SQLite store "
           "(operation_store.py) using BEGIN IMMEDIATE locks and process-id tracking; interrupted rows are reconciled at start-up. "
           "Passwords and connection strings are never stored. backup_proof.py checks that a backup file exists and can be restored into a disposable database - "
           "a readable file or a successful dump command is not treated as proof of restorability."))
S.append(P("Known limitations (from the matrix): PostgreSQL-to-PostgreSQL copies public tables only; views, functions, triggers, policies, extensions, "
           "custom types and non-public schemas are reported but not copied; row-count verification is not a restore test."))

# ---------------- 9 ----------------
S.append(P("9. REST API", h1))
S.append(P("Base URL <font face='Courier'>http://localhost:8000/api/v1</font>; Swagger at /docs, ReDoc at /redoc, OpenAPI at /openapi.json. "
           "Errors return <font face='Courier'>{\"detail\": \"...\"}</font> with 400, 401, 403, 404 or 500."))
S += table([["Method", "Endpoint", "Permission", "Description"],
            ["POST", "/auth/login", "-", "Obtain access and refresh tokens"],
            ["GET", "/auth/me", "token", "Current user"],
            ["POST", "/backups/", "backup:create", "Start a backup"],
            ["GET", "/backups/", "backup:read", "List backups"],
            ["GET", "/backups/{id}", "backup:read", "Operation status"],
            ["POST", "/backups/validate", "backup:read", "Validate a backup file"],
            ["POST", "/backups/retention/apply", "backup:create", "Apply retention policy"],
            ["POST", "/restore/", "restore:create", "Restore from backup"],
            ["POST", "/patches/execute", "patch:create", "Execute SQL patch"],
            ["GET", "/patches/history", "patch:read", "Patch history"],
            ["POST", "/patches/rollback/{id}", "patch:rollback", "Rollback patch (admin)"],
            ["GET", "/patches/{id}/report", "patch:read", "Execution report"],
            ["POST", "/fdw/setup | /extension | /validate", "fdw:manage", "FDW management"],
            ["POST", "/migrations/preview", "migration:create", "Preview a migration"],
            ["POST", "/migrations/", "migration:create", "Run migration"],
            ["POST", "/migrations/{id}/cancel | /reconcile", "migration:create", "Cancel / reconcile"],
            ["GET", "/migrations/{id}/progress", "migration:read", "Progress"],
            ["GET", "/monitoring/health | /dashboard | /metrics", "monitoring:read", "Health, stats, metrics"],
            ["GET", "/monitoring/backups/report | /audit-logs", "monitoring:read", "Backup report, audit log"],
            ["GET", "/monitoring/scheduler/jobs", "scheduler:manage", "Scheduled jobs"]], [16, 66, 32, 56])
S.append(P("9.1 Role-permission matrix", h2))
S += table([["Permission", "Admin", "Operator", "Viewer"],
            ["backup:create / restore:create / patch:create / fdw:manage / migration:create", "Yes", "Yes", "No"],
            ["backup:read / restore:read / patch:read / migration:read / monitoring:read", "Yes", "Yes", "Yes"],
            ["patch:rollback / scheduler:manage / users:manage", "Yes", "No", "No"]], [100, 20, 25, 25])

# ---------------- 10 ----------------
S.append(P("10. Data Model", h1))
S.append(P("Contracts are Pydantic models in <font face='Courier'>backend/models/schemas.py</font>."))
S += table([["Model / enum", "Purpose"],
            ["DatabaseType", "postgresql, mysql, mongodb, mssql, oracle"],
            ["UserRole", "admin, operator, viewer"],
            ["BackupCategory / BackupMode / BackupMethod / BackupSourceType", "logical-physical; full-incremental-differential; engine-specific method"],
            ["PgBackupScope, PgDumpFormat, MySQLBackupScope, MySQLDumpFormat", "Dump scope and format options"],
            ["MongoRestoreScope, RestoreSourceType, RestoreTargetType", "Restore granularity and endpoints"],
            ["OperationStatus", "Running / success / failed lifecycle"],
            ["ConnectionRequest, BackupRequest, RestoreRequest, PatchRequest, FDWRequest, MigrationRequest", "Request payloads"],
            ["OperationResult, PatchHistoryEntry, HealthCheckResult, DashboardStats", "Responses and history"],
            ["UserLogin, UserCreate, UserResponse, TokenResponse", "Authentication API"]], [80, 90])
S.append(P("Persistent runtime data (under var/): saved_servers.json, employees.json, ui_preferences.json, ssh_known_hosts, "
           "migration_operations.sqlite, mysql_patch_jobs.json (data/); app.log, audit.log, patch_history.json (logs/); backup output and chain metadata (backups/)."))

# ---------------- 11 ----------------
S.append(P("11. Desktop Application", h1))
S.append(P("CustomTkinter application (<font face='Courier'>desktop/app.py</font>, launched by <font face='Courier'>python -m desktop.main</font> or "
           "<font face='Courier'>run_app.py</font>) with a Lucide-icon sidebar (SVG rasterised by resvg_py). Views:"))
S += table([["View", "Function"],
            ["Dashboard", "KPIs, host gauges (psutil), DB performance chart, infrastructure health, security and activity panels"],
            ["Servers / Databases", "Connection profiles, test connect, SSH tunnel; database and object explorer (PostgreSQL, MySQL, Oracle providers)"],
            ["Backup / Restore", "Scope, mode and path selection; local or remote artefacts; progress and results"],
            ["Patching", "SQL script patch; MySQL and PostgreSQL patch management flows"],
            ["Migration", "Source/target selection from the capability matrix, precheck, run, progress"],
            ["Monitoring", "Health and performance"],
            ["Reports / Logs", "Filterable operations report, execution log with search; CSV and PDF export"],
            ["Settings", "Theme (dark/light) and preferences"]], [36, 134])
S.append(P("Design tokens (palettes, spacing, radius, type scale) live in theme.py; shared widgets in design_system.py and overlay.py "
           "(dialogs, toasts). Employee login is handled by employee_store.py and auth_dialog.py."))

# ---------------- 12 ----------------
S.append(P("12. Security Design", h1))
S += table([["Control", "Detail"],
            ["Authentication", "JWT (HS256) access tokens (60 min dev / 30 min prod) and refresh tokens (7 days); passwords hashed with bcrypt via passlib"],
            ["Authorisation", "RBAC with hierarchy admin > operator > viewer; per-endpoint permission strings"],
            ["Secrets", "DAP_ environment variables or DAP_SECRETS_FILE; SecretStr; mask_secret() for logs; redaction module for patch audit"],
            ["Credential storage", "Windows DPAPI (current-user scope, application entropy) via desktop/windows_dpapi.py"],
            ["SSH", "Pinned host keys, mismatch rejected; passwordless sudo only (no sudo prompt); no automatic retry or reboot"],
            ["Data safety", "Parameterised SQL in migrations; dry-run/approval gates; operation locks; backup proof by disposable restore"],
            ["Audit", "Structured JSON logs; audit.log; patch and migration histories"]], [36, 134])
S.append(P("Hardening required before production", h2))
S += B(["Replace the default demo accounts (admin/admin123, operator/operator123, viewer/viewer123) and move the user store from in-memory to a database or directory service (LDAP/OAuth).",
        "Set a strong DAP_JWT__SECRET_KEY; the default.yaml value is a development placeholder.",
        "Restrict CORS origins (currently '*') and terminate TLS at a reverse proxy.",
        "Encrypt saved connection passwords and stop persisting the login password in ui_preferences.json (noted in deployment/README.md).",
        "Move writable state out of the install directory (e.g. %LOCALAPPDATA%) for per-machine installs; sign the executable and installer."])

# ---------------- 13 ----------------
S.append(P("13. Configuration", h1))
S.append(P("Settings are loaded from <font face='Courier'>config/default.yaml</font>, overlaid by the profile selected with "
           "<font face='Courier'>DAP_ENVIRONMENT</font> (dev, test, prod) and then by <font face='Courier'>DAP_*</font> environment variables "
           "(nested keys use a double underscore) and an optional <font face='Courier'>.env</font>."))
S += table([["Setting", "Default", "Production override"],
            ["environment / debug", "dev / true", "prod / false"],
            ["api_host / api_port", "0.0.0.0 / 8000", "-"],
            ["connections.default", "localhost:5432, db postgres, ssl prefer", "set via env"],
            ["backup.output_dir / compression", "./var/backups / gzip", "-"],
            ["backup.retention_days", "30", "90"],
            ["backup.schedule_cron", "0 2 * * *", "0 1 * * *"],
            ["jwt.access_token_expire_minutes", "60", "30"],
            ["jwt.refresh_token_expire_days", "7", "-"],
            ["logging.level / format", "INFO / json", "WARNING"],
            ["logging paths", "./var/logs/audit.log, app.log", "-"]], [58, 62, 50])
S.append(P("Example environment overrides:"))
S.append(Preformatted("DAP_ENVIRONMENT=prod\nDAP_JWT__SECRET_KEY=<strong-random-value>\nDAP_CONNECTIONS__DEFAULT__HOST=db.example.com\n"
                      "DAP_CONNECTIONS__DEFAULT__PASSWORD=<secret>\nDAP_BACKUP__RETENTION_DAYS=90", code))

# ---------------- 14 ----------------
S.append(P("14. Installation and Build Guide", h1))
S.append(P("14.1 Development setup", h2))
S.append(Preformatted("python -m venv venv\nvenv\\Scripts\\activate          # Linux/macOS: source venv/bin/activate\n"
                      "pip install -r requirements.txt\ncopy .env.example .env        # Linux/macOS: cp .env.example .env", code))
S.append(P("14.2 Running", h2))
S.append(Preformatted("python run_app.py                 # desktop (or: python -m desktop.main)\n"
                      "uvicorn api.main:app --reload     # API; Swagger at http://localhost:8000/docs\n"
                      "python -m http.server 3000 --directory frontend   # optional web dashboard\n"
                      "pytest tests/ -v                  # test suite", code))
S.append(P("14.3 Production API deployment", h2))
S += B(["Set DAP_ENVIRONMENT=prod and a real JWT secret; configure credentials via environment or secrets file.",
        "Run <font face='Courier'>uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4</font> behind nginx (HTTPS).",
        "Provision backup disk space and log rotation for var/logs."])
S.append(P("14.4 Windows installer", h2))
S.append(P("Run <font face='Courier'>deployment\\build.bat</font> (options: exe, installer). Outputs: "
           "<font face='Courier'>dist\\Data Automation Pro\\Data Automation Pro.exe</font> and <font face='Courier'>dist\\Data Automation Pro Setup Demo.exe</font>. "
           "The PyInstaller spec uses a flat one-directory layout (contents_directory='.') so existing __file__-relative paths work unchanged. "
           "Installation is per-user into %LOCALAPPDATA%\\Programs (no UAC); a fixed AppId enables in-place upgrades; user data in var\\data, var\\logs and "
           "var\\backups is excluded from the payload and preserved on uninstall. Versions are set only in deployment/version.txt (APP_VERSION, FILE_VERSION)."))
S.append(P("14.5 Troubleshooting", h2))
S += table([["Symptom", "Resolution"],
            ["pg_dump not found", "Install PostgreSQL client tools and add to PATH (same for mysqldump, mongodump)"],
            ["psycopg2 not installed", "pip install psycopg2-binary"],
            ["Connection refused", "Verify host/port, firewall and SSH tunnel settings"],
            ["401 Unauthorized", "Token expired - log in again"],
            ["bcrypt errors", "Ensure bcrypt>=4.0.0,<5.0.0"],
            ["SSH host key rejected", "Verify fingerprint on the server; changed keys are never auto-replaced"]], [50, 120])

# ---------------- 15 ----------------
S.append(P("15. Testing and Quality Assurance", h1))
S.append(P("The pytest suite (pytest + pytest-asyncio, httpx for the API) contains 40 modules, run with <font face='Courier'>pytest tests/ -v</font>. Coverage areas:"))
S += table([["Area", "Test modules"],
            ["API and config", "test_api, test_config, test_connectors"],
            ["Backup / restore", "test_backup_service, test_backup_modes, test_backup_preview, test_mysql_backup_options, test_restore_gaps, test_mongo_linux_restore, test_mongo_remote_endpoint, test_remote_directory_listing"],
            ["Migration", "test_migration_framework, test_pg_migration, test_pg_mysql_migration, test_pg_mysql_api, test_pg_sql_migration, test_pg_mongo_migration, test_to_pg_migration"],
            ["MySQL patching", "test_mysql_patch_discovery / precheck / preview / plan / executor / verification / page / remote_backup / centos"],
            ["PostgreSQL patching", "test_postgres_patch_admin, _script, _workflow"],
            ["SSH and credentials", "test_ssh_host_keys, test_ssh_connection_resolution, test_saved_server_credentials, test_employee_store"],
            ["Dashboard / monitoring", "test_monitoring_service, test_dashboard_connection_scope"],
            ["Live / integration (opt-in)", "test_pg_migration_integration_live, test_pg_mysql_migration_integration_live, test_postgres_patch_integration_live, test_mysql_patch_integration"]], [40, 130])
S.append(P("Tests use fake SSH and CLI output and do not connect to servers or install packages; live tests run only when environment variables such as "
           "DB_AUTOPRO_MYSQL_PATCH_IT=1 are set. <b>Continuous integration</b> (.github/workflows/ci.yml) runs on pushes and pull requests to production, "
           "Development, Ganesh and Riya: Windows runner, Python 3.12, install requirements, then <font face='Courier'>python -m pytest tests/ -q --tb=short</font>."))

# ---------------- 16 ----------------
S.append(P("16. Limitations, Risks and Roadmap", h1))
S.append(P("Current limitations", h2))
S += B(["Operation tracking for backup/restore is in memory for the API (lost on restart); only migrations are durably stored.",
        "Desktop roles are largely display-only; RBAC is enforced on the API.",
        "MySQL patching is dry-run only; Oracle backup via Data Pump is partially wired.",
        "Migration copies tables and data; views, procedures, triggers and non-public schemas are not migrated.",
        "Windows installer is unsigned, uses a placeholder icon, and does not package the API or bundle DB client tools.",
        "Writable state sits beside the binaries; passwords for saved servers need stronger encryption."])
S.append(P("Suggested roadmap", h2))
S += table([["Phase", "Item"],
            ["Short term", "Persist operation state (SQLite/PostgreSQL); production user store with password policy; restrict CORS; code signing"],
            ["Medium term", "Controlled package installation for MySQL/PostgreSQL patching with rollback guidance; migration of views and routines; additional engines (MariaDB, SQLite, Redis are already recognised in the registry)"],
            ["Long term", "Centralised multi-user server mode, alerting integrations, ML-driven anomaly detection using the exported dataset"]], [30, 140])

# ---------------- 17 ----------------
S.append(P("17. Conclusion", h1))
S.append(P("Database Automation Pro delivers a coherent, layered platform that consolidates backup, restore, monitoring, patching, migration and reporting "
           "across five database engines. Its emphasis on dry-runs, explicit approvals, pinned SSH trust, durable migration records and an extensive test suite "
           "gives it a safety-first character suited to DBA operations. Closing the hardening items listed in sections 12 and 16 will bring it to production readiness."))
S.append(P("Appendix A - Quick reference commands", h1))
S.append(Preformatted("pip install -r requirements.txt\npython run_app.py\nuvicorn api.main:app --host 0.0.0.0 --port 8000\n"
                      "pytest tests/ -q\ndeployment\\build.bat            # exe + installer\ndeployment\\build.bat exe", code))

doc = Doc(str(OUT))
doc.multiBuild(S)
print("wrote", OUT)
