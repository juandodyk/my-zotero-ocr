#!/usr/bin/env python3
"""Regression tests for mixed visible/invisible PDF text objects."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from pikepdf import Pdf, Stream


PLUGIN_PATH = Path(__file__).parents[1] / "src" / "ocrmypdf_progress_plugin.py"
SPEC = spec_from_file_location("ocrmypdf_progress_plugin", PLUGIN_PATH)
PLUGIN = module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)


def clean(content):
    with Pdf.new() as pdf:
        stream = Stream(pdf, content)
        cleaned, removed, invocations = PLUGIN._clean_instructions(stream, set())
    return cleaned.decode("latin-1"), removed, invocations


mixed, removed, invocations = clean(
    b"BT\n/F1 12 Tf\n3 Tr\n(hidden) Tj\n0 Tr\n(visible) Tj\nET\n"
)
assert "hidden" not in mixed
assert "visible" in mixed
assert removed == 1
assert invocations == 0

reset_before_end, removed, _ = clean(
    b"BT\n/F1 12 Tf\n3 Tr\n(stale OCR) Tj\n0 Tr\nET\n"
)
assert "stale OCR" not in reset_before_end
assert removed == 1

quoted, removed, _ = clean(
    b'BT\n/F1 12 Tf\n3 Tr\n2 1 (hidden quote) \"\n0 Tr\n(visible) Tj\nET\n'
)
assert "hidden quote" not in quoted
assert "visible" in quoted
assert "Tw" in quoted and "Tc" in quoted and "T*" in quoted
assert removed == 1

print("invisible cleanup tests passed")
