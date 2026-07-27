"""OCRmyPDF integration helpers for the Zotero extension."""

from argparse import SUPPRESS
from contextlib import suppress
import json
from pathlib import Path
import sys

from ocrmypdf import hookimpl
from pikepdf import (
    Name,
    Operator,
    Pdf,
    Stream,
    parse_content_stream,
    unparse_content_stream,
)


MARKER = "LOSSLESS_OCR_PROGRESS "


def _clean_instructions(container, removed_xobjects):
    stream = []
    text_object = []
    in_text_object = False
    render_mode = 0
    render_mode_stack = []
    removed_text_objects = 0
    removed_invocations = 0

    for instruction in parse_content_stream(container, ""):
        operands, operator = instruction.operands, instruction.operator
        if operator == Operator("Tr"):
            render_mode = int(operands[0])
        elif operator == Operator("q"):
            render_mode_stack.append(render_mode)
        elif operator == Operator("Q"):
            with suppress(IndexError):
                render_mode = render_mode_stack.pop()

        if not in_text_object:
            if (
                operator == Operator("Do")
                and operands
                and operands[0] in removed_xobjects
            ):
                removed_invocations += 1
            elif operator == Operator("BT"):
                in_text_object = True
                text_object.append((operands, operator))
            else:
                stream.append((operands, operator))
        else:
            text_object.append((operands, operator))
            if operator == Operator("ET"):
                in_text_object = False
                if render_mode == 3:
                    removed_text_objects += 1
                else:
                    stream.extend(text_object)
                text_object.clear()

    if text_object:
        if render_mode == 3:
            removed_text_objects += 1
        else:
            stream.extend(text_object)

    return (
        unparse_content_stream(stream),
        removed_text_objects,
        removed_invocations,
    )


def _clean_container(pdf, container, seen):
    obj = container.obj if hasattr(container, "obj") else container
    objgen = getattr(obj, "objgen", None)
    if objgen and objgen != (0, 0):
        if objgen in seen:
            return 0, 0
        seen.add(objgen)

    resources = obj.get(Name.Resources, {})
    xobjects = resources.get(Name.XObject, {})
    removed_xobjects = {
        name
        for name, xobject in xobjects.items()
        if str(name).startswith("/OCR-")
        and xobject.get(Name.Subtype) == Name.Form
    }

    removed_text_objects = 0
    removed_invocations = 0
    for name, xobject in list(xobjects.items()):
        if name in removed_xobjects or xobject.get(Name.Subtype) != Name.Form:
            continue
        text_count, invocation_count = _clean_container(pdf, xobject, seen)
        removed_text_objects += text_count
        removed_invocations += invocation_count

    content, local_text_count, local_invocation_count = _clean_instructions(
        container,
        removed_xobjects,
    )
    removed_text_objects += local_text_count
    removed_invocations += local_invocation_count

    if local_text_count or local_invocation_count:
        if hasattr(container, "obj"):
            container.Contents = Stream(pdf, content)
        else:
            container.write(content)

    for name in removed_xobjects:
        del xobjects[name]

    return removed_text_objects, removed_invocations


def clean_invisible_layers(input_file):
    path = Path(input_file)
    with Pdf.open(path, allow_overwriting_input=True) as pdf:
        seen = set()
        removed_text_objects = 0
        removed_invocations = 0
        for page in pdf.pages:
            text_count, invocation_count = _clean_container(pdf, page, seen)
            removed_text_objects += text_count
            removed_invocations += invocation_count
        pdf.save(path)
    sys.stderr.write(
        "LOSSLESS_OCR_CLEANUP "
        + json.dumps(
            {
                "removedInvisibleTextObjects": removed_text_objects,
                "removedOCRFormInvocations": removed_invocations,
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stderr.flush()


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
def add_options(parser):
    parser.add_argument(
        "--lossless-clean-invisible-layers",
        action="store_true",
        help=SUPPRESS,
    )


@hookimpl
def check_options(options):
    if getattr(options, "lossless_clean_invisible_layers", False):
        clean_invisible_layers(options.input_file)


@hookimpl
def get_progressbar_class():
    return ZoteroProgressBar


@hookimpl
def validate(pdfinfo, options):
    # OCRmyPDF disables progress automatically when stderr is not a terminal.
    # The Zotero extension captures stderr, so explicitly enable our reporter.
    del pdfinfo
    options.progress_bar = not getattr(
        options,
        "lossless_clean_invisible_layers",
        False,
    )
