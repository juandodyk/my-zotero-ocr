#!/usr/bin/env python3

import io
import os
import sys

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def find_font():
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, 44)
    return ImageFont.load_default()


def main(output_path, page_count=1):
    image = Image.new("RGB", (1275, 1650), "white")
    draw = ImageDraw.Draw(image)
    font = find_font()
    lines = [
        "A SCANNED JOURNAL PAGE",
        "",
        "This page is an image, not born-digital text.",
        "Replacement OCR should recognize these words.",
        "Visible pixels must remain exactly the same.",
        "The source page must not be re-rendered.",
        "A genuine text footer is added separately below.",
    ]
    y = 180
    for line in lines:
        draw.text((130, y), line, fill="black", font=font)
        y += 100

    pdf = canvas.Canvas(output_path, pagesize=letter, pageCompression=1)
    width, height = letter
    for page in range(page_count):
        page_image = image.copy()
        page_draw = ImageDraw.Draw(page_image)
        page_draw.text((1120, 1500), f"Scan {page + 1}", fill="black", font=font)
        image_bytes = io.BytesIO()
        page_image.save(image_bytes, "JPEG", quality=88)
        image_bytes.seek(0)
        pdf.drawImage(ImageReader(image_bytes), 0, 0, width=width, height=height)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(36, 18, f"Downloaded from example.org on 2026-07-27 - page {page + 1}")

        stale = pdf.beginText(36, 740)
        stale.setFont("Helvetica", 10)
        stale.setTextRenderMode(3)
        stale.textLine("WRONG STALE INVISIBLE OCR LAYER")
        pdf.drawText(stale)
        pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)
