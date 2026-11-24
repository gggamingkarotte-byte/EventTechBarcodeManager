import random
from datetime import datetime
from io import BytesIO
from pathlib import Path

import barcode
import qrcode
from barcode.writer import ImageWriter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from PIL import Image


MIN_BARCODE_SCALE = 25
MAX_BARCODE_SCALE = 125
LABELS_PER_PAGE = 10


def _is_code_used(db, code: str) -> bool:
    return bool(
        db.query("SELECT 1 FROM articles WHERE barcode = ?", (code,))
        or db.query("SELECT 1 FROM barcodes WHERE code = ?", (code,))
    )


def _ean13_check_digit(first_twelve: str) -> str:
    digits = [int(value) for value in first_twelve]
    weighted = sum(digit * (1 if index % 2 == 0 else 3) for index, digit in enumerate(digits))
    return str((10 - weighted % 10) % 10)


def generate_unique_code(db, code_type: str = "Code 128") -> str:
    normalized_type = code_type.upper().replace("-", "").replace(" ", "")
    while True:
        if normalized_type == "EAN13":
            prefix = f"2{random.randrange(10**11):011d}"
            code = prefix + _ean13_check_digit(prefix)
        else:
            code = f"ET-{datetime.now():%y%m}-{random.randint(100000, 999999)}"
        if not _is_code_used(db, code):
            return code


def validate_ean13(code: str) -> bool:
    code = code.strip()
    if not code.isdigit() or len(code) != 13:
        return False
    return _ean13_check_digit(code[:12]) == code[-1]


def _validate_scale(scale: int) -> int:
    try:
        scale = int(scale)
    except (TypeError, ValueError) as exc:
        raise ValueError("Skalierung muss eine ganze Prozentzahl sein.") from exc
    if not MIN_BARCODE_SCALE <= scale <= MAX_BARCODE_SCALE:
        raise ValueError(f"Skalierung muss zwischen {MIN_BARCODE_SCALE} und {MAX_BARCODE_SCALE} Prozent liegen.")
    return scale


def render_barcode_png(code: str, code_type: str, scale: int = 100) -> bytes:
    scale = _validate_scale(scale)
    normalized_type = code_type.upper().replace("-", "").replace(" ", "")
    output = BytesIO()
    if normalized_type in ("QR", "QRCODE"):
        qrcode.make(code).save(output, format="PNG")
    elif normalized_type == "EAN13":
        if not validate_ean13(code):
            raise ValueError("EAN-13 ist ungültig.")
        barcode.get_barcode_class("ean13")(code[:-1], writer=ImageWriter()).write(
            output,
            options={
                "write_text": False,
                "module_width": 0.3,
                "module_height": 16,
                "quiet_zone": 3,
            },
        )
    elif normalized_type in ("CODE128", "CODE"):
        barcode.get_barcode_class("code128")(code, writer=ImageWriter()).write(
            output,
            options={
                "write_text": False,
                "module_width": 0.25,
                "module_height": 16,
                "quiet_zone": 3,
            },
        )
    else:
        raise ValueError(f"Unbekannter Barcode-Typ: {code_type}")
    image_bytes = output.getvalue()
    if scale == 100:
        return image_bytes
    with Image.open(BytesIO(image_bytes)) as image:
        width = max(1, round(image.width * scale / 100))
        height = max(1, round(image.height * scale / 100))
        resized = image.resize((width, height), Image.Resampling.NEAREST)
        scaled_output = BytesIO()
        resized.save(scaled_output, format="PNG")
    return scaled_output.getvalue()


def make_image(code: str, code_type: str, destination: Path) -> Path:
    destination = Path(destination)
    if destination.suffix.lower() != ".png":
        destination = destination.with_suffix(".png")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(render_barcode_png(code, code_type))
    return destination


def create_label_pdf(destination: Path, items: list[dict]) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered_items = []
    for item in items:
        scale = _validate_scale(item.get("scale", 100)) / 100
        image_bytes = render_barcode_png(
            str(item.get("barcode", "")), str(item.get("code_type", "Code 128"))
        )
        rendered_items.append((item, image_bytes, scale))

    pdf = canvas.Canvas(str(destination), pagesize=A4)
    page_height = A4[1]
    x_positions = (15 * mm, 110 * mm)
    label_height = 42 * mm
    for index, (item, image_bytes, scale) in enumerate(rendered_items):
        position_on_page = index % LABELS_PER_PAGE
        if index and position_on_page == 0:
            pdf.showPage()
        row = position_on_page // 2
        column = position_on_page % 2
        offset_x = float(item.get("offset_x", 0))
        offset_y = float(item.get("offset_y", 0))
        top = page_height - 25 * mm - row * 49 * mm - offset_y
        x = x_positions[column] + offset_x
        bottom = top - label_height
        name = str(item.get("name") or "").strip()
        barcode_value = str(item.get("barcode", ""))
        if name == barcode_value:
            name = ""
        image = ImageReader(BytesIO(image_bytes))
        code_type = str(item.get("code_type", "Code 128")).upper().replace("-", "")
        if code_type in ("QR", "QRCODE"):
            target_width = 22 * mm * scale
            target_height = target_width
        else:
            target_width = 65 * mm * scale
            target_height = 18 * mm * scale
        image_width, image_height = image.getSize()
        fit = min(target_width / image_width, target_height / image_height)
        image_width *= fit
        image_height *= fit

        name_font_size = 9 * scale if name else 0
        value_font_size = 8 * scale
        name_line_height = name_font_size * 1.2
        value_line_height = value_font_size * 1.2
        gap = 2 * mm * scale
        group_height = (
            name_line_height + value_line_height + image_height
            + gap * (2 if name else 1)
        )
        group_bottom = bottom + (label_height - group_height) / 2
        value_y = group_bottom + (value_line_height - value_font_size) / 2
        image_y = group_bottom + value_line_height + gap
        center_x = x + 85 * mm / 2
        pdf.drawImage(
            image, center_x - image_width / 2, image_y,
            width=image_width, height=image_height, mask="auto",
        )
        if name:
            name_y = image_y + image_height + gap + (name_line_height - name_font_size) / 2
            pdf.setFont("Helvetica-Bold", name_font_size)
            pdf.drawCentredString(center_x, name_y, name[:45])
        pdf.setFont("Helvetica", value_font_size)
        pdf.drawCentredString(center_x, value_y, barcode_value[:42])
    pdf.save()
    return destination
