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


def main(output_path):
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

    image_bytes = io.BytesIO()
    image.save(image_bytes, "JPEG", quality=88)
    image_bytes.seek(0)

    pdf = canvas.Canvas(output_path, pagesize=letter, pageCompression=1)
    width, height = letter
    pdf.drawImage(ImageReader(image_bytes), 0, 0, width=width, height=height)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(36, 18, "Downloaded from example.org on 2026-07-27")

    stale = pdf.beginText(36, 740)
    stale.setFont("Helvetica", 10)
    stale.setTextRenderMode(3)
    stale.textLine("WRONG STALE INVISIBLE OCR LAYER")
    pdf.drawText(stale)
    pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    main(sys.argv[1])
