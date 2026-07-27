"""Emit machine-readable OCRmyPDF progress events for the Zotero extension."""

import json
import sys

from ocrmypdf import hookimpl


MARKER = "LOSSLESS_OCR_PROGRESS "


class ZoteroProgressBar:
    def __init__(
        self,
        *,
        total=None,
        desc=None,
        unit=None,
        disable=False,
        **_kwargs,
    ):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.disable = disable
        self.current = 0

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def update(self, n=1, *, completed=None):
        if completed is None:
            self.current += n
        else:
            self.current = completed
        if self.disable:
            return
        event = {
            "description": self.desc,
            "unit": self.unit,
            "completed": self.current,
            "total": self.total,
        }
        sys.stderr.write(MARKER + json.dumps(event, separators=(",", ":")) + "\n")
        sys.stderr.flush()


@hookimpl
def get_progressbar_class():
    return ZoteroProgressBar


@hookimpl
def validate(pdfinfo, options):
    # OCRmyPDF disables progress automatically when stderr is not a terminal.
    # The Zotero extension captures stderr, so explicitly enable our reporter.
    del pdfinfo
    options.progress_bar = True
