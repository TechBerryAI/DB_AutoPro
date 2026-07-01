"""Export execution logs to CSV and PDF."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from fpdf import FPDF


def default_log_export_stem() -> str:
    return f"execution_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def export_logs_csv(entries: list[tuple[str, str, str]], path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Time", "Level", "Message"])
        for timestamp, level, message in entries:
            writer.writerow([timestamp, level, message])

    return path


def export_logs_pdf(entries: list[tuple[str, str, str]], path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Database Automation Pro", ln=True)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Execution Log Export", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        6,
        f"Exported on {datetime.now().strftime('%A, %d %B %Y · %H:%M:%S')}",
        ln=True,
    )
    pdf.cell(0, 6, f"Entries: {len(entries)}", ln=True)
    pdf.ln(4)

    col_w = (28, 22, 140)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(240, 244, 248)
    pdf.cell(col_w[0], 7, "Time", border=1, fill=True)
    pdf.cell(col_w[1], 7, "Level", border=1, fill=True)
    pdf.cell(col_w[2], 7, "Message", border=1, ln=True, fill=True)

    pdf.set_font("Helvetica", "", 8)
    fill = False
    for timestamp, level, message in entries:
        pdf.set_fill_color(248, 250, 252 if fill else 255)
        pdf.cell(col_w[0], 6, timestamp[:12], border=1, fill=True)
        pdf.cell(col_w[1], 6, level[:10], border=1, fill=True)
        msg = message.replace("\n", " ")[:120]
        pdf.cell(col_w[2], 6, msg, border=1, ln=True, fill=True)
        fill = not fill

    pdf.output(str(path))
    return path
