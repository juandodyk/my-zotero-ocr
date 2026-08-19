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
    make_signed(directory / "text-watermark.pdf", directory / "signed-watermark.pdf")
    make_encrypted(directory / "text-watermark.pdf", directory / "encrypted-watermark.pdf")


if __name__ == "__main__":
    main()
