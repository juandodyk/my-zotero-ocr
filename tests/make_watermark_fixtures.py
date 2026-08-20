#!/usr/bin/env python3
"""Create small PDFs that exercise conservative watermark detection."""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw


def body(pdf: canvas.Canvas, page: int) -> None:
    pdf.setFillColor(black)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 720, f"Fixture body page {page}")
    pdf.drawString(72, 695, "This ordinary text must survive watermark removal.")


def make_text_watermark(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    for page in range(1, 6):
        body(pdf, page)
        pdf.saveState()
        pdf.setFillColor(Color(0.55, 0.55, 0.55, alpha=0.35))
        pdf.setFont("Helvetica-Bold", 34)
        pdf.translate(140, 300)
        pdf.rotate(35)
        pdf.drawString(0, 0, "DRAFT - FOR PEER REVIEW")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def make_form_watermark(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.beginForm("watermark", 0, 0, 310, 55)
    pdf.setFillColor(Color(0.45, 0.45, 0.45))
    pdf.setFont("Helvetica-Bold", 32)
    pdf.drawString(0, 10, "CONFIDENTIAL")
    pdf.endForm()
    for page in range(1, 6):
        body(pdf, page)
        pdf.saveState()
        pdf.translate(150, 285)
        pdf.rotate(35)
        pdf.doForm("watermark")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def make_vector_form_watermark(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.beginForm("vector-watermark", 0, 0, 36, 17)
    pdf.setFillColor(Color(0.35, 0.35, 0.35))
    pdf.rect(0, 9, 11, 8, fill=1, stroke=0)
    pdf.rect(13, 9, 10, 8, fill=1, stroke=0)
    pdf.rect(25, 9, 11, 8, fill=1, stroke=0)
    pdf.rect(2, 0, 32, 6, fill=1, stroke=0)
    pdf.endForm()
    for page in range(1, 6):
        body(pdf, page)
        pdf.saveState()
        pdf.setFillAlpha(0.13)
        pdf.translate(180, 330)
        pdf.scale(7, 7)
        pdf.doForm("vector-watermark")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def make_legitimate_vector_repetition(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.beginForm("centerpiece", 0, 0, 36, 17)
    pdf.setFillColor(Color(0.35, 0.35, 0.35))
    pdf.rect(0, 0, 36, 17, fill=1, stroke=0)
    pdf.endForm()
    for page in range(1, 6):
        body(pdf, page)
        pdf.saveState()
        pdf.translate(180, 330)
        pdf.scale(7, 7)
        pdf.doForm("centerpiece")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def make_legitimate_repetition(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    for page in range(1, 6):
        body(pdf, page)
        pdf.saveState()
        pdf.setFont("Helvetica", 10)
        pdf.translate(22, 620)
        pdf.rotate(90)
        pdf.drawString(0, 0, "CHAPTER ONE")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def make_image_watermark(path: Path) -> None:
    image = Image.new("RGBA", (360, 80), (255, 255, 255, 0))
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((4, 4, 355, 75), outline=(120, 120, 120, 180), width=4)
    drawing.text((35, 26), "REVIEW COPY", fill=(100, 100, 100, 180))
    watermark = ImageReader(image)
    pdf = canvas.Canvas(str(path), pagesize=letter)
    for page in range(1, 6):
        body(pdf, page)
        pdf.saveState()
        pdf.setFillAlpha(0.40)
        pdf.translate(145, 285)
        pdf.rotate(35)
        pdf.drawImage(watermark, 0, 0, width=330, height=74, mask="auto")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def make_mixed_form_uses(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.beginForm("mixed", 0, 0, 310, 55)
    pdf.setFillColor(Color(0.45, 0.45, 0.45))
    pdf.setFont("Helvetica-Bold", 32)
    pdf.drawString(0, 10, "CONFIDENTIAL")
    pdf.endForm()
    for page in range(1, 21):
        body(pdf, page)
        pdf.saveState()
        if page < 20:
            pdf.translate(150, 285)
            pdf.rotate(35)
        else:
            # The same Form is deliberately reused as ordinary corner content.
            # Exact-occurrence removal must retain this nonqualifying invocation.
            pdf.translate(20, 25)
            pdf.scale(0.25, 0.25)
        pdf.doForm("mixed")
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def main() -> None:
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    make_text_watermark(destination / "text-watermark.pdf")
    make_form_watermark(destination / "form-watermark.pdf")
    make_vector_form_watermark(destination / "vector-form-watermark.pdf")
    make_image_watermark(destination / "image-watermark.pdf")
    make_mixed_form_uses(destination / "mixed-form-uses.pdf")
    make_legitimate_repetition(destination / "legitimate-repetition.pdf")
    make_legitimate_vector_repetition(destination / "legitimate-vector-repetition.pdf")


if __name__ == "__main__":
    main()
