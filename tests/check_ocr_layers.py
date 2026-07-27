#!/usr/bin/env python3
"""Verify that each PDF has exactly one OCRmyPDF text-layer Form per page."""

from pathlib import Path
import sys

from pikepdf import Name, Pdf


def count_ocr_forms(path):
    with Pdf.open(path) as pdf:
        seen = set()
        count = 0

        def visit(container):
            nonlocal count
            obj = container.obj if hasattr(container, "obj") else container
            objgen = getattr(obj, "objgen", None)
            if objgen and objgen != (0, 0):
                if objgen in seen:
                    return
                seen.add(objgen)

            resources = obj.get(Name.Resources, {})
            xobjects = resources.get(Name.XObject, {})
            for name, xobject in xobjects.items():
                if xobject.get(Name.Subtype) != Name.Form:
                    continue
                if str(name).startswith("/OCR-"):
                    count += 1
                else:
                    visit(xobject)

        for page in pdf.pages:
            visit(page)
        return len(pdf.pages), count


def main(paths):
    for value in paths:
        path = Path(value)
        pages, forms = count_ocr_forms(path)
        if forms != pages:
            raise SystemExit(
                f"{path}: expected one OCR Form per page ({pages}), found {forms}"
            )
        print(f"{path}: {forms} OCR Form(s) across {pages} page(s)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: check_ocr_layers.py PDF [PDF ...]")
    main(sys.argv[1:])
