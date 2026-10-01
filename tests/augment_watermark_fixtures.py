#!/usr/bin/env python3
"""Add PDF structures that ReportLab does not conveniently generate."""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String


def make_annotation(source: Path, destination: Path) -> None:
    with pikepdf.open(source) as pdf:
        for page in pdf.pages:
            annotation = pdf.make_indirect(
                Dictionary(
                    Type=Name.Annot,
                    Subtype=Name.Watermark,
                    Rect=Array([120, 300, 490, 390]),
                    Contents=String("CONFIDENTIAL watermark"),
                    F=4,
                )
            )
            page.obj[Name.Annots] = Array([annotation])
        pdf.save(destination)


def make_optional_content(source: Path, destination: Path) -> None:
    with pikepdf.open(source) as pdf:
        ocg = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("DRAFT watermark")))
        pdf.Root[Name.OCProperties] = Dictionary(
            OCGs=Array([ocg]),
            D=Dictionary(Order=Array([ocg]), ON=Array([ocg])),
        )
        for page in pdf.pages:
            resources = page.obj.get(Name.Resources, Dictionary())
            properties = resources.get(Name.Properties, Dictionary())
            properties[Name("/WM")] = ocg
            resources[Name.Properties] = properties
            page.obj[Name.Resources] = resources
            overlay = pdf.make_stream(
                b"/OC /WM BDC q 0.7 g BT /F1 30 Tf 0.819 0.574 -0.574 0.819 150 300 Tm "
                b"(DRAFT) Tj ET Q EMC\n"
            )
            contents = page.obj.get(Name.Contents)
            page.obj[Name.Contents] = Array([overlay, contents])
        pdf.save(destination)


def make_signed(source: Path, destination: Path) -> None:
    with pikepdf.open(source) as pdf:
        signature = pdf.make_indirect(Dictionary(FT=Name.Sig, T=String("Test signature")))
        pdf.Root[Name.AcroForm] = Dictionary(Fields=Array([signature]))
        pdf.save(destination)


def make_shared_text(source: Path, destination: Path, reset_position: bool) -> None:
    with pikepdf.open(source) as pdf:
        for page in pdf.pages:
            positioning = b"1 0 0 1 72 650 Tm " if reset_position else b"0 -60 Td "
            overlay = pdf.make_stream(
                b"q BT /F1 48 Tf 0.84 0.95 1 rg "
                b"0.623 -0.782 0.782 0.623 194 520 Tm "
                b"(For Peer Review) Tj 0 g /F1 12 Tf " + positioning +
                b"(Shared-block ordinary text must survive.) Tj ET Q\n"
            )
            page.obj[Name.Contents] = Array([overlay, page.obj[Name.Contents]])
        pdf.save(destination)


def make_line_numbers(source: Path, destination: Path, x: int) -> None:
    with pikepdf.open(source) as pdf:
        for page in pdf.pages:
            numbering = b"q BT /F1 10 Tf 1 0 0 1 200 770 Tm (Journal header) Tj "
            numbering += f"1 0 0 1 {x} 740 Tm 12 TL (1) Tj ".encode()
            numbering += b" ".join(f"T* ({n}) Tj".encode() for n in range(2, 61))
            overlay = pdf.make_stream(numbering + b" ET Q\n")
            page.obj[Name.Contents] = Array([overlay, page.obj[Name.Contents]])
        pdf.save(destination)


def make_encrypted(source: Path, destination: Path) -> None:
    with pikepdf.open(source) as pdf:
        pdf.save(
            destination,
            encryption=pikepdf.Encryption(owner="fixture-owner", user="", R=6),
        )


def main() -> None:
    directory = Path(sys.argv[1])
    clean = directory / "legitimate-repetition.pdf"
    make_annotation(clean, directory / "annotation-watermark.pdf")
    make_optional_content(clean, directory / "optional-content-watermark.pdf")
    make_shared_text(clean, directory / "shared-text-watermark.pdf", True)
    make_shared_text(clean, directory / "dependent-text.pdf", False)
    make_line_numbers(clean, directory / "line-number-watermark.pdf", 8)
    make_line_numbers(clean, directory / "body-number-column.pdf", 200)
    make_signed(directory / "text-watermark.pdf", directory / "signed-watermark.pdf")
    make_encrypted(directory / "text-watermark.pdf", directory / "encrypted-watermark.pdf")


if __name__ == "__main__":
    main()
