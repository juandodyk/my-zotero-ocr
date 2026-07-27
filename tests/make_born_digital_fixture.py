#!/usr/bin/env python3

import sys

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def main(output_path):
    pdf = canvas.Canvas(output_path, pagesize=letter, pageCompression=1)
    width, height = letter
    paragraph = (
        "This is genuine born-digital text created directly in the PDF content stream. "
        "It remains searchable after invisible OCR is stripped and does not require "
        "Tesseract recognition. The conservative preflight should detect enough real "
        "text and confirm that no page-sized scanned image is present before skipping "
        "the expensive OCR stage. This sentence is repeated to provide an unambiguous "
        "amount of extractable prose for the performance test. "
    )

    for page in range(2):
        text = pdf.beginText(54, height - 72)
        text.setFont("Helvetica", 11)
        words = (paragraph * 4).split()
        line = []
        for word in words:
            line.append(word)
            if len(" ".join(line)) > 82:
                text.textLine(" ".join(line[:-1]))
                line = [word]
        if line:
            text.textLine(" ".join(line))
        pdf.drawText(text)
        pdf.setFont("Helvetica", 8)
        pdf.drawRightString(width - 54, 30, f"Born-digital fixture page {page + 1}")
        pdf.showPage()

    pdf.save()


if __name__ == "__main__":
    main(sys.argv[1])
