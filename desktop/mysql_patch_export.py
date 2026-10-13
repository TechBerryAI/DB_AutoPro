"""CSV and PDF export for one MySQL patch job. Secrets are not written."""

from __future__ import annotations

import csv
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from backend.services.mysql_patch.models import MysqlPatchAuditRecord


def _rows(record: MysqlPatchAuditRecord) -> list[tuple[str, str]]:
    return [
        ("Job ID", record.job_id),
        ("Server", record.server_id),
        ("Initiated by", record.initiated_by),
        ("Started", record.started_at.isoformat()),
        ("Finished", "" if record.finished_at is None else record.finished_at.isoformat()),
        ("Current version", record.current_version),
        ("Target version", record.target_version),
        ("Plan ID", record.plan_id),
        ("Plan digest", record.plan_digest),
        ("Approved by", record.approved_by or ""),
        ("Stage", record.stage),
        ("Execution outcome", record.execution_outcome),
        ("Verification outcome", record.verification_outcome or ""),
        ("Final status", record.final_status),
        ("Packages installed", "yes" if record.packages_installed else "no"),
        ("Dry run", "yes" if record.dry_run else "no"),
        ("Error", record.error_message or ""),
    ]


def export_patch_csv(record: MysqlPatchAuditRecord, path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Field", "Value"])
        writer.writerows(_rows(record))
    return path


def export_patch_pdf(record: MysqlPatchAuditRecord, path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "MySQL patch report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    for label, value in _rows(record):
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, f"{label}: {value}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.output(str(path))
    return path
