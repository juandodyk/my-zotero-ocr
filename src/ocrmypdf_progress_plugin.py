"""OCRmyPDF integration helpers for the Zotero extension."""

from argparse import SUPPRESS
from contextlib import suppress
from os import fspath
import json
from pathlib import Path
import re
from subprocess import PIPE, STDOUT, CalledProcessError, TimeoutExpired
import sys

from lxml import etree, html as lxml_html
from ocrmypdf import hookimpl
from ocrmypdf._exec import tesseract
from ocrmypdf._exec.tesseract import ThresholdingMethod
from ocrmypdf.builtin_plugins.tesseract_ocr import TesseractOcrEngine
from ocrmypdf.exceptions import SubprocessOutputError
from pikepdf import (
    Name,
    Operator,
    Pdf,
    Stream,
    parse_content_stream,
    unparse_content_stream,
)


MARKER = "LOSSLESS_OCR_PROGRESS "
BBOX_PATTERN = re.compile(
    r"(?:^|;)\s*bbox\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+"
    r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)"
)
BASELINE_PATTERN = re.compile(
    r"(?:^|;)\s*baseline\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)"
)
SCAN_RES_PATTERN = re.compile(r"(?:^|;)\s*scan_res\s+(\d+)\s+(\d+)")


def _parse_bbox(element):
    match = BBOX_PATTERN.search(element.get("title", ""))
    if not match:
        return None
    return tuple(float(value) for value in match.groups())


def _pdf_number(value):
    return f"{value:.5f}".rstrip("0").rstrip(".")


def _encode_text(value):
    return value.encode("utf-16-be").hex().upper()


def render_word_box_layer(hocr_path, template_pdf):
    """Replace Tesseract's line layer with tight, invisible word boxes."""
    tree = etree.parse(str(hocr_path), lxml_html.XHTMLParser())
    pages = tree.xpath(
        '//*[contains(concat(" ", normalize-space(@class), " "), " ocr_page ")]'
    )
    if len(pages) != 1:
        raise ValueError(f"Expected one hOCR page, found {len(pages)}")

    page = pages[0]
    scan_res_match = SCAN_RES_PATTERN.search(page.get("title", ""))
    if scan_res_match:
        dpi_x = float(scan_res_match.group(1))
        dpi_y = float(scan_res_match.group(2))
    else:
        dpi_x = dpi_y = 300.0
    instructions = []

    lines = page.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " ocr_line ")]'
    )
    for line in lines:
        line_box = _parse_bbox(line)
        if not line_box:
            continue
        baseline_match = BASELINE_PATTERN.search(line.get("title", ""))
        slope, intercept = (
            (float(value) for value in baseline_match.groups())
            if baseline_match
            else (0.0, 0.0)
        )
        words = line.xpath(
            './/*[contains(concat(" ", normalize-space(@class), " "),'
            ' " ocrx_word ")]'
        )
        line_instructions = []
        for word in words:
            word_box = _parse_bbox(word)
            text = "".join(word.itertext()).strip()
            if not word_box or not text:
                continue

            left, top, right, bottom = word_box
            font_size = (bottom - top) * 72.0 / dpi_y
            if font_size <= 0:
                continue
            center = (left + right) / 2.0
            baseline_y = line_box[3] + intercept + slope * (center - line_box[0])
            x_pt = left * 72.0 / dpi_x
            y_pt = baseline_y * 72.0 / dpi_y
            width_pt = (right - left) * 72.0 / dpi_x

            # Tesseract's embedded GlyphLessFont assigns every UTF-16 code
            # unit a width of 500/1000 em.
            code_units = len(text.encode("utf-16-be")) / 2
            natural_width = code_units * font_size * 0.5
            horizontal_scale = (
                100.0 * width_pt / natural_width if natural_width > 0 else 100.0
            )
            line_instructions.append(
                f"1 0 0 1 {_pdf_number(x_pt)} {_pdf_number(y_pt)} Tm\n"
                f"/f-0-0 {_pdf_number(font_size)} Tf\n"
                f"{_pdf_number(horizontal_scale)} Tz\n"
                f"<{_encode_text(text)}> Tj\n"
            )

        if line_instructions:
            instructions.append(
                "BT\n3 Tr\n" + "".join(line_instructions) + "ET\n"
            )

    template_path = Path(template_pdf)
    replacement_path = template_path.with_suffix(".word-box.pdf")
    with Pdf.open(template_path) as pdf:
        page_height = float(pdf.pages[0].mediabox[3])
        transform = (
            f"q\n1 0 0 -1 0 {_pdf_number(page_height)} cm\n".encode("ascii")
        )
        content = "".join(instructions).encode("ascii")
        pdf.pages[0].Contents = Stream(pdf, transform + content + b"Q\n")
        pdf.save(replacement_path)
    replacement_path.replace(template_path)


class WordBoxOcrEngine(TesseractOcrEngine):
    """Tesseract engine with hOCR geometry and its Unicode PDF font."""

    @staticmethod
    def creator_tag(options):
        del options
        return (
            "Lossless OCR word-box renderer + Tesseract OCR "
            + TesseractOcrEngine.version()
        )

    @staticmethod
    def generate_pdf(input_file, output_pdf, output_text, options):
        prefix = output_pdf.parent / Path(output_pdf.stem)
        output_hocr = prefix.with_suffix(".hocr")
        args = tesseract.tess_base_args(
            options.languages,
            options.tesseract.oem,
        )
        if options.tesseract.pagesegmode is not None:
            args.extend(["--psm", str(options.tesseract.pagesegmode)])
        args.extend(["-c", "textonly_pdf=1"])
        if (
            options.tesseract.thresholding != ThresholdingMethod.AUTO
            and tesseract.has_thresholding()
        ):
            args.extend(
                ["-c", f"thresholding_method={options.tesseract.thresholding}"]
            )
        if options.tesseract.user_words:
            args.extend(["--user-words", options.tesseract.user_words])
        if options.tesseract.user_patterns:
            args.extend(["--user-patterns", options.tesseract.user_patterns])
        args.extend(
            [
                fspath(input_file),
                fspath(prefix),
                "hocr",
                "pdf",
                "txt",
            ]
        )
        args.extend(options.tesseract.config)

        try:
            process = tesseract.run(
                args,
                stdout=PIPE,
                stderr=STDOUT,
                timeout=options.tesseract.timeout,
                check=True,
                env=tesseract._tesseract_env(
                    options.tesseract.omp_thread_limit
                ),
            )
            stdout = process.stdout
            with suppress(FileNotFoundError):
                prefix.with_suffix(".txt").replace(output_text)
            if not output_pdf.exists() or not output_hocr.exists():
                raise SubprocessOutputError(
                    "Tesseract did not produce the PDF and hOCR files "
                    "required by the word-box renderer."
                )
            render_word_box_layer(output_hocr, output_pdf)
        except TimeoutExpired:
            tesseract.page_timedout(options.tesseract.timeout)
            tesseract.use_skip_page(output_pdf, output_text)
        except CalledProcessError as error:
            tesseract.tesseract_log_output(error.output)
            if (
                b"Image too large" in error.output
                or b"Empty page!!" in error.output
            ):
                tesseract.use_skip_page(output_pdf, output_text)
                return
            raise SubprocessOutputError() from error
        else:
            tesseract.tesseract_log_output(stdout)
        finally:
            with suppress(FileNotFoundError):
                output_hocr.unlink()


def _clean_instructions(container, removed_xobjects):
    stream = []
    text_object = []
    in_text_object = False
    removed_text_from_object = False
    render_mode = 0
    render_mode_stack = []
    removed_text_objects = 0
    removed_invocations = 0
    text_showing_operators = {
        Operator("Tj"),
        Operator("TJ"),
        Operator("'"),
        Operator('"'),
    }

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
                removed_text_from_object = False
                text_object.append((operands, operator))
            else:
                stream.append((operands, operator))
        else:
            if operator in text_showing_operators and render_mode == 3:
                removed_text_from_object = True
                # The quote operators also change text position and spacing.
                # Preserve those non-painting effects when removing their text.
                if operator == Operator("'"):
                    text_object.append(([], Operator("T*")))
                elif operator == Operator('"'):
                    text_object.extend(
                        [
                            ([operands[0]], Operator("Tw")),
                            ([operands[1]], Operator("Tc")),
                            ([], Operator("T*")),
                        ]
                    )
            else:
                text_object.append((operands, operator))
            if operator == Operator("ET"):
                in_text_object = False
                if removed_text_from_object:
                    removed_text_objects += 1
                stream.extend(text_object)
                text_object.clear()

    if text_object:
        if removed_text_from_object:
            removed_text_objects += 1
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
    parser.add_argument(
        "--lossless-word-box-renderer",
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


@hookimpl(tryfirst=True)
def get_ocr_engine(options):
    if options and getattr(options, "lossless_word_box_renderer", False):
        return WordBoxOcrEngine()
    return None


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
