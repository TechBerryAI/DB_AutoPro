"""Export dashboard reports to CSV and PDF."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF


def _metric_rows(report: dict[str, Any]) -> list[tuple[str, str]]:
    period = report.get("period", "today")
    if period == "today":
        success_label = "Successful operations (today)"
        failed_label = "Failed operations (today)"
    elif period == "monthly":
        success_label = "Successful operations (this month)"
        failed_label = "Failed operations (this month)"
    else:
        success_label = "Successful operations (this year)"
        failed_label = "Failed operations (this year)"

    return [
        ("Period", str(report.get("period_label", ""))),
        ("Generated at", str(report.get("generated_at", ""))),
        ("Connection", str(report.get("connection_name", ""))),
        ("Linux servers (registered)", str(report.get("linux_servers", 0))),
        ("Databases (connected)", str(report.get("databases", 0))),
        ("Active jobs (live)", str(report.get("active_jobs", 0))),
        ("Pending jobs (queue)", str(report.get("pending_jobs", 0))),
        (success_label, str(report.get("success_operations", 0))),
        (failed_label, str(report.get("failed_operations", 0))),
        ("Backup success", str(report.get("backup_success", 0))),
        ("Backup failed", str(report.get("backup_failed", 0))),
        ("Restore success", str(report.get("restore_success", 0))),
        ("Restore failed", str(report.get("restore_failed", 0))),
        ("Total backup files", str(report.get("total_backups", 0))),
        ("Storage used (MB)", f"{float(report.get('storage_used_mb', 0)):.2f}"),
    ]


def default_export_stem(report: dict[str, Any]) -> str:
    period = report.get("period", "today")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"dashboard_report_{period}_{stamp}"


def export_dashboard_csv(report: dict[str, Any], path: Path) -> Path:
    """Write dashboard report metrics to a CSV file."""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Metric", "Value"])
        writer.writerows(_metric_rows(report))

    return path


def export_dashboard_pdf(report: dict[str, Any], path: Path) -> Path:
    """Write dashboard report metrics to a PDF file."""
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Database Automation Pro", ln=True)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Dashboard Report", ln=True)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, str(report.get("period_label", "")), ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary", ln=True)
    pdf.set_font("Helvetica", "", 10)

    col_w_label = 120
    col_w_value = 60
    pdf.set_fill_color(240, 244, 248)
    pdf.cell(col_w_label, 8, "Metric", border=1, fill=True)
    pdf.cell(col_w_value, 8, "Value", border=1, ln=True, fill=True)

    fill = False
    for label, value in _metric_rows(report):
        pdf.set_fill_color(248, 250, 252 if fill else 255)
        pdf.cell(col_w_label, 7, label[:70], border=1, fill=True)
        pdf.cell(col_w_value, 7, value[:30], border=1, ln=True, fill=True)
        fill = not fill

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(
        0,
        5,
        f"Exported on {datetime.now().strftime('%A, %d %B %Y · %H:%M:%S')}",
    )

    pdf.output(str(path))
    return path
