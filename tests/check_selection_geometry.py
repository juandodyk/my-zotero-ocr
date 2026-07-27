#!/usr/bin/env python3
"""Confirm that the word-box renderer produces a tighter selection box."""

from pathlib import Path
import sys
from xml.etree import ElementTree


def word_box(path, target):
    root = ElementTree.parse(path)
    for element in root.iter():
        if element.tag.endswith("word") and "".join(element.itertext()).strip() == target:
            return tuple(
                float(element.attrib[name])
                for name in ("xMin", "yMin", "xMax", "yMax")
            )
    raise SystemExit(f"{path}: could not find OCR word {target!r}")


def height(box):
    return box[3] - box[1]


def main(fpdf2_path, sandwich_path, word_box_path):
    target = "This"
    measurements = {
        "fpdf2": word_box(Path(fpdf2_path), target),
        "sandwich": word_box(Path(sandwich_path), target),
        "word-box": word_box(Path(word_box_path), target),
    }
    tight_height = height(measurements["word-box"])
    other_height = min(
        height(measurements["fpdf2"]),
        height(measurements["sandwich"]),
    )
    if tight_height >= other_height * 0.95:
        raise SystemExit(
            "word-box selection was not materially tighter: "
            + ", ".join(
                f"{name}={height(box):.3f}pt"
                for name, box in measurements.items()
            )
        )
    print(
        "selection geometry passed ("
        + ", ".join(
            f"{name}={height(box):.3f}pt"
            for name, box in measurements.items()
        )
        + ")"
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: check_selection_geometry.py FPDF2_XML SANDWICH_XML WORD_BOX_XML"
        )
    main(*sys.argv[1:])
