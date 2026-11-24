import csv
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def export_csv(destination: Path, fieldnames: list[str], rows: list[dict]) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return destination


def export_inventory_pdf(destination: Path, title: str, rows: list[dict]) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(destination), pagesize=A4)
    width, height = A4
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(40, height - 40, title)
    y = height - 70
    pdf.setFont("Helvetica", 9)
    for row in rows:
        text = " | ".join(f"{key}: {value}" for key, value in row.items())
        pdf.drawString(40, y, text[:150])
        y -= 16
        if y < 40:
            pdf.showPage()
            y = height - 40
    pdf.save()
    return destination
