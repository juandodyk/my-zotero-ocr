#!/usr/bin/env python3
"""Detect and remove PDF watermarks and other repeated page content.

The helper is intentionally non-interactive. ``scan`` emits JSON describing
candidates: ``likely`` ones match a watermark signal, while ``possible`` ones
merely repeat at one place across pages, however they are encoded, and need a
person's judgment. ``apply`` requires exact IDs and writes a new PDF; it never
modifies the input file.

It uses pikepdf, which is already present in the Python environment bundled
with OCRmyPDF (a required dependency of this Zotero extension).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Sequence

import pikepdf


Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
TEXT_SHOW_OPERATORS = {"Tj", "TJ", "'", '"'}
WATERMARK_WORDS = re.compile(
    r"(?:\bwatermark\b|\bdraft\b|\bconfidential\b|for\s+peer\s+review|"
    r"\bpreprint\b|\bproof\b|do\s+not\s+distribute|\bsample\b|"
    r"uncorrected|review\s+copy|unedited\s+manuscript|accepted\s+manuscript|"
    r"author[’\']?s?\s+manuscript|not\s+for\s+distribution)",
    re.IGNORECASE,
)
SUBMISSION_SYSTEM_FOOTER = re.compile(
    r"powered by editorial manager®? and produxion manager®? "
    r"from aries systems corporation",
    re.IGNORECASE,
)
# Wording added by download and submission systems. It only decides whether a
# repeated mark starts ticked; detection itself does not depend on this list.
PROVENANCE_PHRASES = tuple(
    re.sub(r"[^a-z0-9]", "", phrase) for phrase in (
        "downloaded from", "downloaded by", "this content downloaded",
        "all use subject to", "powered by editorial manager",
        "for personal use", "personal use only", "brought to you by",
        "reproduced with permission of the copyright owner",
        "not for distribution", "do not distribute", "for peer review",
        "access provided by", "subject to the cambridge core terms",
        "ebscohost printed on",
    )
)
MARGIN_HEIGHT = 0.10
MARGIN_WIDTH = 0.08
MAX_POSSIBLE_CANDIDATES = 12


def matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    """Return the affine product ``left * right``."""
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def as_matrix(values: Any) -> Matrix:
    try:
        nums = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return IDENTITY
    return nums if len(nums) == 6 else IDENTITY  # type: ignore[return-value]


def transform_point(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def angle_degrees(matrix: Matrix) -> float:
    angle = math.degrees(math.atan2(matrix[1], matrix[0]))
    while angle > 90:
        angle -= 180
    while angle < -90:
        angle += 180
    return angle


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def decode_pdf_string(value: Any) -> str:
    if isinstance(value, pikepdf.String):
        try:
            return str(value)
        except UnicodeDecodeError:
            return bytes(value).decode("latin-1", "replace")
    return ""


def shown_text(operands: Sequence[Any], operator: str) -> str:
    if not operands:
        return ""
    value = operands[-1]
    if operator == "TJ" and isinstance(value, pikepdf.Array):
        return normalize_text("".join(decode_pdf_string(part) for part in value))
    return normalize_text(decode_pdf_string(value))


def object_fingerprint(obj: Any) -> str:
    hasher = hashlib.sha256()
    try:
        hasher.update(bytes(obj.read_bytes()))
    except Exception:
        hasher.update(repr(obj).encode("utf-8", "replace"))
    for key in ("/Subtype", "/BBox", "/Matrix", "/Width", "/Height"):
        try:
            hasher.update(key.encode())
            hasher.update(repr(obj.get(key)).encode("utf-8", "replace"))
        except Exception:
            pass
    try:
        resources = obj.get("/Resources", {})
        hasher.update(repr(sorted(str(key) for key in resources.keys())).encode())
    except Exception:
        pass
    return hasher.hexdigest()


def page_box(page: pikepdf.Page) -> tuple[float, float, float, float]:
    box = page.obj.get("/CropBox", page.obj.get("/MediaBox", [0, 0, 612, 792]))
    return tuple(float(value) for value in box)  # type: ignore[return-value]


def location_metrics(
    matrix: Matrix,
    local_box: Sequence[float],
    visible_box: tuple[float, float, float, float],
) -> tuple[bool, float]:
    x0, y0, x1, y1 = (float(value) for value in local_box)
    corners = [
        transform_point(matrix, x0, y0),
        transform_point(matrix, x0, y1),
        transform_point(matrix, x1, y0),
        transform_point(matrix, x1, y1),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    left, bottom, right, top = visible_box
    width = max(1.0, right - left)
    height = max(1.0, top - bottom)
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    centered = (
        left + 0.15 * width <= center_x <= right - 0.15 * width
        and bottom + 0.15 * height <= center_y <= top - 0.15 * height
    )
    span = max((max(xs) - min(xs)) / width, (max(ys) - min(ys)) / height)
    return centered, span


def box_center(
    matrix: Matrix, local_box: Sequence[float]
) -> tuple[float, float]:
    x0, y0, x1, y1 = (float(value) for value in local_box)
    corners = [transform_point(matrix, x, y) for x in (x0, x1) for y in (y0, y1)]
    return (
        (min(p[0] for p in corners) + max(p[0] for p in corners)) / 2,
        (min(p[1] for p in corners) + max(p[1] for p in corners)) / 2,
    )


def mark_zone(
    x: float, y: float, angle: float, alpha: float, lightness: float,
    visible_box: tuple[float, float, float, float],
) -> str | None:
    """Name the page region where repeated content is plausibly an overlay."""
    left, bottom, right, top = visible_box
    width = max(1.0, right - left)
    height = max(1.0, top - bottom)
    size = abs(angle)
    if 5 < size < 60:
        return "diagonal"
    if size >= 60:
        if x <= left + MARGIN_WIDTH * width:
            return "left"
        if x >= right - MARGIN_WIDTH * width:
            return "right"
        return None
    if y >= top - MARGIN_HEIGHT * height:
        return "top"
    if y <= bottom + MARGIN_HEIGHT * height:
        return "bottom"
    return "pale" if alpha <= 0.75 or lightness >= 0.55 else None


def compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def resources_for(page: pikepdf.Page, name: str) -> Any:
    resources = page.obj.get("/Resources", {})
    return resources.get(name, {}) if resources else {}


def resolve_resource(page: pikepdf.Page, category: str, name: Any) -> Any | None:
    try:
        return resources_for(page, category).get(name)
    except Exception:
        return None


def form_plain_text(form: Any) -> str:
    try:
        instructions = pikepdf.parse_content_stream(form)
    except Exception:
        return ""
    parts: list[str] = []
    for instruction in instructions:
        operator = str(instruction.operator)
        if operator in TEXT_SHOW_OPERATORS:
            text = shown_text(instruction.operands, operator)
            if text:
                parts.append(text)
    return normalize_text(" ".join(parts))


def ocg_name(page: pikepdf.Page, operand: Any) -> str:
    obj = operand
    if isinstance(operand, pikepdf.Name):
        obj = resolve_resource(page, "/Properties", operand)
    try:
        if obj and str(obj.get("/Type", "")) == "/OCG":
            return normalize_text(str(obj.get("/Name", "")))
    except Exception:
        pass
    return ""


def document_has_signatures(pdf: pikepdf.Pdf) -> bool:
    try:
        if pdf.Root.get("/Perms"):
            return True
    except Exception:
        pass

    def field_is_signed(field: Any) -> bool:
        try:
            if str(field.get("/FT", "")) == "/Sig":
                return True
            value = field.get("/V")
            if value and str(value.get("/Type", "")) == "/Sig":
                return True
            return any(field_is_signed(child) for child in field.get("/Kids", []))
        except Exception:
            return False

    try:
        fields = pdf.Root.get("/AcroForm", {}).get("/Fields", [])
        return any(field_is_signed(field) for field in fields)
    except Exception:
        return False


@dataclass
class Occurrence:
    page: int
    kind: str
    path: tuple[int, ...] = ()
    operation: int | None = None
    start: int | None = None
    end: int | None = None
    annotation: int | None = None
    eligible: bool = False
    angle: float = 0.0
    alpha: float = 1.0
    text: str = ""
    x: float = 0.0
    y: float = 0.0
    size: float = 0.0
    lightness: float = 0.0
    operations: tuple[int, ...] = ()


@dataclass
class TextRun:
    """Text shown from one positioning point up to the next move or ET."""
    occurrence: Occurrence
    parts: list[tuple[Any, Any]] = field(default_factory=list)
    operations: list[int] = field(default_factory=list)
    removable: bool = True


@dataclass
class Mark:
    """One visible item, identified by appearance rather than by encoding."""
    identity: tuple[Any, ...]
    zone: str
    occurrence: Occurrence
    kind: str
    # Fragments such as an IP address count only as part of a larger mark.
    weak: bool = False


@dataclass
class ScanContext:
    marks: list[Mark] = field(default_factory=list)
    cmaps: dict[Any, Any] = field(default_factory=dict)


@dataclass
class Group:
    key: str
    kind: str
    occurrences: list[Occurrence] = field(default_factory=list)
    text: str = ""
    explicit: bool = False
    confidence: str = "likely"
    label: str = ""
    fingerprint: str = ""
    expected_words: int = 0

    @property
    def candidate_id(self) -> str:
        digest = hashlib.sha256((self.kind + "\0" + self.key).encode()).hexdigest()
        return self.kind + "-" + digest[:12]


def parse_to_unicode(data: bytes) -> tuple[int, dict[int, str]] | None:
    text = data.decode("latin-1")
    width = 0
    mapping: dict[int, str] = {}

    def unicode_of(value: str) -> str:
        return bytes.fromhex(value).decode("utf-16-be", "replace")

    for block in re.findall(r"begincodespacerange(.*?)endcodespacerange", text, re.S):
        found = re.search(r"<([0-9A-Fa-f]+)>", block)
        if found:
            width = max(width, len(found.group(1)) // 2)
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for code, value in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            width = width or len(code) // 2
            mapping[int(code, 16)] = unicode_of(value)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        pattern = r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])"
        for low_hex, high_hex, target in re.findall(pattern, block):
            width = width or len(low_hex) // 2
            low, high = int(low_hex, 16), int(high_hex, 16)
            if high - low > 65535:
                continue
            if target.startswith("["):
                for offset, value in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", target)):
                    mapping[low + offset] = unicode_of(value)
            else:
                value = unicode_of(target[1:-1])
                for offset in range(high - low + 1):
                    if value:
                        mapping[low + offset] = value[:-1] + chr(
                            min(0x10FFFF, ord(value[-1]) + offset))
    return (width, mapping) if width and mapping else None


def font_cmap(font: Any, context: ScanContext) -> tuple[int, dict[int, str]] | None:
    cache_key = font.objgen if font.is_indirect else None
    if cache_key is not None and cache_key in context.cmaps:
        return context.cmaps[cache_key]
    cmap = None
    try:
        stream = font.get("/ToUnicode")
        if stream is not None:
            cmap = parse_to_unicode(bytes(stream.read_bytes()))
    except Exception:
        cmap = None
    if cache_key is not None:
        context.cmaps[cache_key] = cmap
    return cmap


def decode_with_font(data: bytes, font: Any, context: ScanContext) -> str | None:
    """Return real text for ``data``; None when the font does not reveal it."""
    try:
        cmap = font_cmap(font, context) if font is not None else None
        if cmap:
            width, mapping = cmap
            if len(data) % width:
                return None
            chars = [mapping.get(int.from_bytes(data[i:i + width], "big"))
                     for i in range(0, len(data), width)]
            return None if any(c is None for c in chars) else "".join(chars)  # type: ignore[arg-type]
        if font is not None and str(font.get("/Subtype", "")) == "/Type0":
            return None
    except Exception:
        return None
    return data.decode("cp1252", "replace")


def run_text(run: TextRun, context: ScanContext) -> str | None:
    pieces: list[str] = []
    for font, value in run.parts:
        if isinstance(value, bytes):
            decoded = decode_with_font(value, font, context)
            if decoded is None:
                return None
            pieces.append(decoded)
        elif value < -200:
            pieces.append(" ")  # Large TJ adjustments stand in for spaces.
    # Some stamping tools pad strings with NUL bytes that paint nothing.
    text = normalize_text("".join(pieces).replace("\x00", ""))
    return text if text and all(c.isprintable() for c in text) else None


def show_parts(operands: Sequence[Any], operator: str, font: Any) -> list[tuple[Any, Any]]:
    if not operands:
        return []
    value = operands[-1]
    items = list(value) if operator == "TJ" and isinstance(value, pikepdf.Array) else [value]
    parts: list[tuple[Any, Any]] = []
    for item in items:
        if isinstance(item, pikepdf.String):
            parts.append((font, bytes(item)))
        else:
            try:
                parts.append((None, float(item)))
            except (TypeError, ValueError):
                pass
    return parts


def collect_text_marks(
    runs: list[TextRun], visible_box: tuple[float, float, float, float],
    context: ScanContext,
) -> None:
    for run in runs:
        first = run.occurrence
        if not run.removable or not first.eligible:
            continue
        zone = mark_zone(first.x, first.y, first.angle, first.alpha,
                         first.lightness, visible_box)
        if zone is None:
            continue
        text = run_text(run, context)
        if not text:
            continue
        compact = compact_text(text)
        if not compact:
            continue
        context.marks.append(Mark(
            identity=("text", text.casefold(), round(first.size, 1), round(first.angle)),
            zone=zone,
            kind="text",
            weak=len(compact) < 4 or compact.isdigit(),
            occurrence=replace(first, kind="repeated", text=text, eligible=True,
                               operation=None, operations=tuple(run.operations)),
        ))


def is_margin_number(
    occurrence: Occurrence, visible_box: tuple[float, float, float, float]
) -> bool:
    left, _, right, _ = visible_box
    return bool(
        re.fullmatch(r"[1-9][0-9]*", occurrence.text)
        and 0 < occurrence.size <= 12 and abs(occurrence.angle) <= 1
        and left <= occurrence.x <= left + (right - left) * 0.05
    )


def add_line_number_column(
    numbers: list[Occurrence],
    visible_box: tuple[float, float, float, float],
    groups: dict[str, Group],
) -> None:
    """Record ``numbers`` if they form one sequential column from the top half."""
    _, bottom, _, top = visible_box
    if not (len(numbers) >= 20
            and [int(o.text) for o in numbers] == list(range(1, len(numbers) + 1))
            and max(o.x for o in numbers) - min(o.x for o in numbers) <= 2
            and bottom + (top - bottom) / 2 <= numbers[0].y <= top
            and all(4 <= a.y - b.y <= 24 for a, b in zip(numbers, numbers[1:]))):
        return
    key = "margin-line-numbers:1-" + str(len(numbers))
    group = groups.setdefault(key, Group(
        key=key, kind="margin-line-numbers", text="1–" + str(len(numbers))))
    for occurrence in numbers:
        group.occurrences.append(replace(
            occurrence, kind="margin-line-numbers", eligible=True))


def scan_page(
    page: pikepdf.Page, page_index: int, groups: dict[str, Group], *,
    stream: Any = None, initial_matrix: Matrix = IDENTITY, initial_alpha: float = 1.0,
    initial_lightness: float = 0.0,
    path: tuple[int, ...] = (), ancestors: tuple[Any, ...] = (),
    context: ScanContext | None = None,
) -> None:
    context = context if context is not None else ScanContext()
    try:
        instructions = pikepdf.parse_content_stream(stream if stream is not None else page)
    except Exception as error:
        raise RuntimeError(f"Could not parse page {page_index + 1} content: {error}") from error

    visible_box = page_box(page)
    ctm = initial_matrix
    alpha = initial_alpha
    render_mode = 0
    lightness = initial_lightness
    graphics_stack: list[tuple[Matrix, float, float, float, int, float, Any]] = []
    text_matrix = IDENTITY
    font_size = 0.0
    font: Any = None
    leading = 0.0
    text_block: dict[str, Any] | None = None
    marked_stack: list[dict[str, Any]] = []
    separate_numbers: list[Occurrence] = []

    for index, instruction in enumerate(instructions):
        operands = instruction.operands
        operator = str(instruction.operator)

        if operator == "q":
            graphics_stack.append((ctm, alpha, font_size, leading, render_mode, lightness, font))
        elif operator == "Q":
            ctm, alpha, font_size, leading, render_mode, lightness, font = (
                graphics_stack.pop() if graphics_stack
                else (initial_matrix, initial_alpha, 0.0, 0.0, 0, initial_lightness, None))
        elif operator == "cm" and len(operands) == 6:
            ctm = matrix_multiply(ctm, as_matrix(operands))
        elif operator == "gs" and operands:
            state = resolve_resource(page, "/ExtGState", operands[0])
            if state is not None:
                try:
                    alpha = float(state.get("/ca", alpha))
                except Exception:
                    alpha = 1.0
        elif operator == "g" and operands:
            lightness = float(operands[0])
        elif operator == "rg" and len(operands) == 3:
            lightness = min(float(value) for value in operands)
        elif operator == "k" and len(operands) == 4:
            lightness = 1 - min(1, max(float(value) for value in operands[:3]) + float(operands[3]))
        elif operator in ("cs", "sc", "scn"):
            lightness = 0.0  # Unknown color spaces must not inherit pale RGB.
        elif operator == "BT":
            text_matrix = IDENTITY
            text_block = {"start": index, "shows": [], "runs": [], "new_run": True}
        elif operator == "ET":
            if text_block:
                collect_text_marks(text_block["runs"], visible_box, context)
                shows = text_block["shows"]
                numbers = [o for o in shows
                           if is_margin_number(o, visible_box)
                           and str(instructions[o.operation].operator) == "Tj"]
                if numbers and numbers[-1] is shows[-1]:
                    add_line_number_column(numbers, visible_box, groups)
                # Some submission systems give every number its own text block.
                if len(shows) == 1 and numbers:
                    separate_numbers.append(numbers[0])
            for occurrence in text_block["shows"] if text_block else []:
                if len(text_block["shows"]) > 1:
                    # Dropping a show also drops its text advance. Shared
                    # blocks are safe only when a later Tm resets positioning
                    # before another show (or this is the final show).
                    operation = occurrence.operation
                    if str(instructions[operation].operator) not in ("Tj", "TJ"):
                        continue
                    following = instructions[operation + 1:index]
                    if next(
                        (str(item.operator) for item in following
                         if str(item.operator) in TEXT_SHOW_OPERATORS | {"Tm"}),
                        "Tm",
                    ) != "Tm":
                        continue
                keyword = bool(WATERMARK_WORDS.search(occurrence.text))
                # A known submission-system credit is safe only as a small
                # horizontal line at the foot of the page.
                footer = bool(
                    SUBMISSION_SYSTEM_FOOTER.fullmatch(occurrence.text)
                    and occurrence.size <= 12 and abs(occurrence.angle) <= 1
                    and occurrence.y <= visible_box[1] + 0.08 * (visible_box[3] - visible_box[1])
                )
                # Large oblique lettering is a strong signal even when painted
                # opaque gray. Exclude vertical chart labels and small running text.
                oblique = 10 <= abs(occurrence.angle) <= 80
                centered, span = location_metrics(
                    (math.cos(math.radians(occurrence.angle)),
                     math.sin(math.radians(occurrence.angle)),
                     -math.sin(math.radians(occurrence.angle)),
                     math.cos(math.radians(occurrence.angle)), occurrence.x, occurrence.y),
                    (0, 0, len(occurrence.text) * occurrence.size * 0.45, occurrence.size),
                    visible_box,
                )
                occurrence.eligible = bool(
                    occurrence.text and occurrence.eligible and (
                        footer
                        or keyword and (oblique or occurrence.size >= 18 or occurrence.alpha <= 0.95)
                        or occurrence.size >= 24 and centered and span >= 0.20
                        and (oblique or occurrence.alpha <= 0.75 or occurrence.lightness >= 0.55)
                        and abs(occurrence.angle) <= 80
                    )
                )
                key = "text:" + occurrence.text.casefold()
                group = groups.setdefault(key, Group(key=key, kind="text", text=occurrence.text))
                group.explicit = keyword or footer
                group.occurrences.append(occurrence)
            text_block = None
        elif operator == "Tr" and operands:
            render_mode = int(operands[0])
        elif operator == "Tf" and len(operands) >= 2:
            try:
                font_size = abs(float(operands[1]))
            except (TypeError, ValueError):
                font_size = 0.0
            font = resolve_resource(page, "/Font", operands[0])
        elif operator == "Tm" and len(operands) == 6:
            text_matrix = as_matrix(operands)
            if text_block is not None:
                text_block["new_run"] = True
        elif operator == "TL" and operands:
            leading = float(operands[0])
        elif operator == "T*":
            text_matrix = matrix_multiply(text_matrix, (1, 0, 0, 1, 0, -leading))
            if text_block is not None:
                text_block["new_run"] = True
        elif operator in ("Td", "TD") and len(operands) >= 2:
            try:
                translation = (1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1]))
                text_matrix = matrix_multiply(text_matrix, translation)
                # Line moves start from the previous line, not from where its
                # text ended, so each line can be removed on its own.
                if text_block is not None:
                    text_block["new_run"] = True
                if operator == "TD":
                    leading = -float(operands[1])
            except (TypeError, ValueError):
                pass
        elif operator in TEXT_SHOW_OPERATORS and text_block is not None:
            text = shown_text(operands, operator)
            combined = matrix_multiply(ctm, text_matrix)
            effective_size = font_size * max(
                math.hypot(combined[0], combined[1]),
                math.hypot(combined[2], combined[3]),
            )
            occurrence = Occurrence(
                page=page_index,
                kind="text",
                operation=index,
                angle=angle_degrees(combined),
                alpha=alpha,
                text=text,
                eligible=render_mode in (0, 1, 2),
                path=path,
                x=combined[4],
                y=combined[5],
                size=effective_size,
                lightness=lightness,
            )
            text_block["shows"].append(occurrence)
            if text_block["new_run"]:
                text_block["runs"].append(TextRun(occurrence=replace(occurrence)))
                text_block["new_run"] = False
            run = text_block["runs"][-1]
            run.parts.extend(show_parts(operands, operator, font))
            run.operations.append(index)
            # ' and " also move the line, so removing them would shift later text.
            run.removable = (run.removable and operator in ("Tj", "TJ")
                             and render_mode in (0, 1, 2))
        elif operator == "BDC" and len(operands) >= 2:
            name = ocg_name(page, operands[1])
            properties = operands[1]
            if isinstance(properties, pikepdf.Name):
                properties = resolve_resource(page, "/Properties", properties)
            artifact = (str(operands[0]) == "/Artifact" and isinstance(properties, pikepdf.Dictionary)
                        and str(properties.get("/Subtype", "")) == "/Watermark")
            marked_stack.append({
                "start": index,
                "name": name,
                "keyword": artifact or bool(WATERMARK_WORDS.search(name)),
                "artifact": artifact,
            })
        elif operator == "BMC":
            marked_stack.append({"start": index, "name": "", "keyword": False})
        elif operator == "EMC" and marked_stack:
            marked = marked_stack.pop()
            if marked["keyword"]:
                content_text = normalize_text(" ".join(
                    shown_text(item.operands, str(item.operator))
                    for item in instructions[marked["start"] + 1:index]
                    if str(item.operator) in TEXT_SHOW_OPERATORS))
                kind = "artifact" if marked.get("artifact") else "optional-content"
                key = kind + ":" + (content_text if kind == "artifact" else marked["name"]).casefold()
                group = groups.setdefault(
                    key,
                    Group(key=key, kind=kind, text=content_text or marked["name"], explicit=True),
                )
                group.occurrences.append(
                    Occurrence(
                        page=page_index,
                        kind=kind,
                        path=path,
                        start=marked["start"],
                        end=index,
                        eligible=True,
                        text=content_text or marked["name"],
                    )
                )
        elif operator == "Do" and operands:
            obj = resolve_resource(page, "/XObject", operands[0])
            if obj is None:
                continue
            subtype = str(obj.get("/Subtype", ""))
            if subtype not in ("/Form", "/Image"):
                continue
            local_matrix = as_matrix(obj.get("/Matrix", IDENTITY)) if subtype == "/Form" else IDENTITY
            effective = matrix_multiply(ctm, local_matrix)
            local_box = obj.get("/BBox", [0, 0, 1, 1]) if subtype == "/Form" else [0, 0, 1, 1]
            centered, span = location_metrics(effective, local_box, visible_box)
            text = form_plain_text(obj) if subtype == "/Form" else ""
            if subtype == "/Form":
                identity = obj.objgen if obj.is_indirect else id(obj)
                if identity not in ancestors and len(path) < 16:
                    child = SimpleNamespace(obj={
                        "/Resources": obj.get("/Resources", page.obj.get("/Resources", {})),
                        "/CropBox": visible_box,
                    })
                    scan_page(child, page_index, groups, stream=obj,
                              initial_matrix=effective, initial_alpha=alpha, initial_lightness=lightness,
                              path=path + (index,), ancestors=ancestors + (identity,),
                              context=context)
                if text or not any(str(i.operator) in {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
                                   for i in pikepdf.parse_content_stream(obj)):
                    continue
            keyword = bool(WATERMARK_WORDS.search(text))
            rotated = abs(angle_degrees(effective)) >= 10
            # Some publishers draw an unrotated logo as vector outlines, so it
            # has no searchable text. Very low opacity plus large, centered,
            # repeated placement is the identifying signal in that case.
            eligible = centered and (
                keyword
                or (subtype == "/Form" and rotated and span >= 0.20)
                or (subtype == "/Form" and span >= 0.20 and alpha <= 0.50)
                or (subtype == "/Image" and span >= 0.20
                    and (alpha <= 0.50 or rotated and alpha <= 0.75))
            )
            kind = "form" if subtype == "/Form" else "image"
            fingerprint = object_fingerprint(obj)
            center_x, center_y = box_center(effective, local_box)
            zone = mark_zone(center_x, center_y, angle_degrees(effective), alpha, 0.0, visible_box)
            if zone is not None:
                context.marks.append(Mark(
                    identity=(kind, fingerprint),
                    zone=zone,
                    kind=kind,
                    occurrence=Occurrence(
                        page=page_index, kind="repeated", path=path,
                        operations=(index,), eligible=True, angle=angle_degrees(effective),
                        alpha=alpha, x=center_x, y=center_y, size=span,
                    ),
                ))
            key = kind + ":" + fingerprint
            group = groups.setdefault(key, Group(key=key, kind=kind, text=text))
            group.explicit = group.explicit or keyword
            group.occurrences.append(
                Occurrence(
                    page=page_index,
                    kind=kind,
                    path=path,
                    operation=index,
                    eligible=eligible,
                    angle=angle_degrees(effective),
                    alpha=alpha,
                    text=text,
                )
            )

    add_line_number_column(separate_numbers, visible_box, groups)
    if not path:
        scan_annotations(page, page_index, groups)


def annotation_text(annotation: Any) -> str:
    parts = []
    for key in ("/Contents", "/Name", "/Subj", "/T"):
        try:
            value = annotation.get(key)
            if value:
                parts.append(str(value).lstrip("/"))
        except Exception:
            pass
    return normalize_text(" ".join(parts))


def annotation_fingerprint(annotation: Any) -> str:
    appearance = annotation.get("/AP", {})
    normal = appearance.get("/N") if appearance else None
    payload = "|".join(
        [
            str(annotation.get("/Subtype", "")),
            annotation_text(annotation).casefold(),
            object_fingerprint(normal) if normal is not None else "",
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def scan_annotations(page: pikepdf.Page, page_index: int, groups: dict[str, Group]) -> None:
    for annotation_index, annotation in enumerate(page.obj.get("/Annots", [])):
        try:
            subtype = str(annotation.get("/Subtype", ""))
        except Exception:
            continue
        if subtype not in ("/Watermark", "/Stamp"):
            continue
        text = annotation_text(annotation)
        explicit = subtype == "/Watermark" or bool(WATERMARK_WORDS.search(text))
        if not explicit:
            continue
        key = "annotation:" + annotation_fingerprint(annotation)
        group = groups.setdefault(
            key,
            Group(key=key, kind="annotation", text=text or subtype.lstrip("/"), explicit=True),
        )
        group.occurrences.append(
            Occurrence(
                page=page_index,
                kind="annotation",
                annotation=annotation_index,
                eligible=True,
                text=text,
            )
        )


def position_clusters(marks: list[Mark], tolerance: float = 2.0) -> list[list[Mark]]:
    clusters: list[list[Mark]] = []
    for mark in marks:
        for cluster in clusters:
            anchor = cluster[0].occurrence
            if (abs(anchor.x - mark.occurrence.x) <= tolerance
                    and abs(anchor.y - mark.occurrence.y) <= tolerance):
                cluster.append(mark)
                break
        else:
            clusters.append([mark])
    return clusters


def cluster_extent(cluster: list[Mark]) -> tuple[float, float, float, float]:
    """Return (across, along-start, along-end, size), estimating text width."""
    first = cluster[0].occurrence
    vertical = abs(first.angle) >= 60
    across, along = (first.x, first.y) if vertical else (first.y, first.x)
    size = first.size if cluster[0].kind == "text" else 0.0
    return across, along, along + len(first.text) * size * 0.55, size


def clusters_touch(left: list[Mark], right: list[Mark]) -> bool:
    if left[0].kind != "text" or right[0].kind != "text":
        return False
    across_a, start_a, end_a, size_a = cluster_extent(left)
    across_b, start_b, end_b, size_b = cluster_extent(right)
    size = max(size_a, size_b)
    return (abs(size_a - size_b) <= 0.5
            and abs(across_a - across_b) <= 1.6 * size
            and start_a <= end_b + 3 * size and start_b <= end_a + 3 * size)


def repeated_groups(marks: list[Mark], total_pages: int) -> list[Group]:
    """Group marks that recur at one position, however each page encodes them."""
    if total_pages < 2:
        return []
    by_identity: dict[tuple[Any, ...], list[Mark]] = defaultdict(list)
    for mark in marks:
        by_identity[mark.identity + (mark.zone,)].append(mark)
    minimum_pages = min(3, total_pages)
    buckets: dict[tuple[Any, ...], list[list[Mark]]] = defaultdict(list)
    for same_marks in by_identity.values():
        for cluster in position_clusters(same_marks):
            pages = frozenset(mark.occurrence.page for mark in cluster)
            if len(pages) >= minimum_pages:
                buckets[(pages, cluster[0].zone, round(cluster[0].occurrence.angle))].append(cluster)

    groups: list[Group] = []
    for (_, zone, _), clusters in buckets.items():
        # Fragments of one line or block are one item to the reader.
        parents = list(range(len(clusters)))

        def root(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        if len(clusters) <= 200:
            for i, left in enumerate(clusters):
                for j in range(i + 1, len(clusters)):
                    if clusters_touch(left, clusters[j]):
                        parents[root(j)] = root(i)
        merged: dict[int, list[list[Mark]]] = defaultdict(list)
        for index, cluster in enumerate(clusters):
            merged[root(index)].append(cluster)
        families = list(merged.values())
        if zone in ("left", "right"):
            families = join_stamp_lines(families)
        for members in families:
            if not all(cluster[0].weak for cluster in members):
                groups.append(group_from_clusters(members, zone, total_pages))
    return merge_same_content(groups)


def join_stamp_lines(families: list[list[list[Mark]]]) -> list[list[list[Mark]]]:
    """Keep every piece of a vertical download stamp's line together."""
    stamps = [family for family in families if has_provenance_wording(
        " ".join(cluster[0].occurrence.text for cluster in family))]
    for stamp in stamps:
        across, _, _, size = cluster_extent(stamp[0])
        for family in families:
            if family is stamp or not family:
                continue
            if abs(cluster_extent(family[0])[0] - across) <= 0.5 * size:
                stamp.extend(family)
                family.clear()
    return [family for family in families if family]


def merge_same_content(groups: list[Group]) -> list[Group]:
    """Join one item placed differently on, say, odd and even pages."""
    merged: dict[tuple[str, str], Group] = {}
    for group in groups:
        identity = group.text.casefold() or group.fingerprint
        existing = merged.get((group.label, identity))
        if existing is None:
            merged[(group.label, identity)] = group
            continue
        slots = {(item.page, item.path): item for item in existing.occurrences}
        for item in group.occurrences:
            slot = (item.page, item.path)
            if slot in slots:
                item = replace(slots[slot], operations=tuple(sorted(
                    set(slots[slot].operations + item.operations))))
            slots[slot] = item
        existing.occurrences = sorted(slots.values(), key=lambda item: (item.page, item.path))
        existing.expected_words += group.expected_words
        if group.confidence == "likely":
            existing.confidence = "likely"
    return list(merged.values())


def has_provenance_wording(text: str) -> bool:
    compact = compact_text(text)
    return any(phrase in compact for phrase in PROVENANCE_PHRASES)


def group_from_clusters(clusters: list[list[Mark]], zone: str, total_pages: int) -> Group:
    def reading_order(cluster: list[Mark]) -> tuple[float, float]:
        across, along, _, _ = cluster_extent(cluster)
        return (-round(across), along)

    ordered = sorted(clusters, key=reading_order)
    kind = ordered[0][0].kind
    text = normalize_text(" ".join(cluster[0].occurrence.text for cluster in ordered))
    combined: dict[tuple[int, tuple[int, ...]], Occurrence] = {}
    for cluster in ordered:
        for mark in cluster:
            source = mark.occurrence
            slot = (source.page, source.path)
            if slot in combined:
                existing = combined[slot]
                combined[slot] = replace(
                    existing, operations=tuple(sorted(set(existing.operations + source.operations))))
            else:
                combined[slot] = replace(source, text=text)
    place = {"top": "header", "bottom": "footer", "left": "side-margin",
             "right": "side-margin", "diagonal": "diagonal", "pale": "pale"}[zone]
    noun = {"text": "text", "image": "image", "form": "graphic"}[kind]
    first = ordered[0][0]
    identity = repr((first.identity, round(first.occurrence.x), round(first.occurrence.y), text))
    occurrences = sorted(combined.values(), key=lambda item: (item.page, item.path))
    coverage = len({item.page for item in occurrences}) / total_pages
    likely = kind == "text" and (
        has_provenance_wording(text)
        or zone == "diagonal" and coverage >= 0.8 and first.occurrence.size >= 24)
    return Group(
        key="repeated:" + zone + ":" + identity,
        kind="repeated",
        text=text,
        occurrences=occurrences,
        explicit=False,
        confidence="likely" if likely else "possible",
        label=f"repeated {place} {noun}",
        fingerprint=str(first.identity[1]),
        expected_words=sum(len(mark.occurrence.text.split())
                           for cluster in ordered for mark in cluster),
    )


def claimed_operations(groups: Iterable[Group]) -> tuple[set[Any], list[Any]]:
    operations: set[Any] = set()
    ranges: list[Any] = []
    for group in groups:
        for occurrence in group.occurrences:
            if not occurrence.eligible:
                continue
            if occurrence.operation is not None:
                operations.add((occurrence.page, occurrence.path, occurrence.operation))
            for operation in occurrence.operations:
                operations.add((occurrence.page, occurrence.path, operation))
            if occurrence.start is not None and occurrence.end is not None:
                ranges.append((occurrence.page, occurrence.path, occurrence.start, occurrence.end))
    return operations, ranges


def overlaps_claim(group: Group, operations: set[Any], ranges: list[Any]) -> bool:
    for occurrence in group.occurrences:
        for operation in occurrence.operations:
            route = occurrence.path + (operation,)
            for depth, step in enumerate(route):
                prefix = route[:depth]
                if (occurrence.page, prefix, step) in operations:
                    return True
                if any(page == occurrence.page and path == prefix and start <= step <= end
                       for page, path, start, end in ranges):
                    return True
    return False


def candidate_from_group(group: Group, total_pages: int) -> dict[str, Any] | None:
    eligible = [occurrence for occurrence in group.occurrences if occurrence.eligible]
    pages = sorted({occurrence.page for occurrence in eligible})
    if not eligible:
        return None
    minimum_pages = 1 if group.explicit else min(2, total_pages)
    coverage = len(pages) / total_pages if total_pages else 0.0
    if len(pages) < minimum_pages:
        return None

    # Sparse hits from the older shape rules are often figures or ornaments.
    confidence = group.confidence
    if (group.kind in ("text", "form", "image") and coverage < 0.25
            and not has_provenance_wording(group.text)):
        confidence = "possible"

    angles = [occurrence.angle for occurrence in eligible]
    alphas = [occurrence.alpha for occurrence in eligible]
    label_by_kind = {
        "text": "repeated page text",
        "form": "repeated form",
        "image": "repeated image",
        "optional-content": "watermark layer",
        "artifact": "explicitly tagged watermark",
        "annotation": "watermark annotation",
        "margin-line-numbers": "left-margin line numbers",
    }
    reason = f"{len(pages)}/{total_pages} pages"
    if angles and any(abs(angle) >= 1 for angle in angles):
        reason += f", angle about {sorted(angles)[len(angles) // 2]:+.0f} degrees"
    if alphas and min(alphas) < 0.99:
        reason += f", opacity {min(alphas):.2f}"
    return {
        "id": group.candidate_id,
        "kind": group.kind,
        "label": group.label or label_by_kind[group.kind],
        "confidence": confidence,
        "text": group.text[:160],
        "fullText": group.text,
        "pages": [page + 1 for page in pages],
        "pageCount": len(pages),
        "totalPages": total_pages,
        "coverage": coverage,
        "occurrences": len(eligible),
        "expectedWords": group.expected_words,
        "reason": reason,
    }


def scan_document(pdf: pikepdf.Pdf) -> tuple[dict[str, Group], list[dict[str, Any]]]:
    groups: dict[str, Group] = {}
    context = ScanContext()
    for page_index, page in enumerate(pdf.pages):
        scan_page(page, page_index, groups, context=context)

    # A keyword-named optional-content group is the safer, more complete unit
    # to remove. Do not also present nested text/forms/images as duplicates.
    ocg_ranges: dict[tuple[int, tuple[int, ...]], list[tuple[int, int]]] = defaultdict(list)
    for group in groups.values():
        if group.kind not in ("optional-content", "artifact"):
            continue
        for occurrence in group.occurrences:
            if occurrence.eligible and occurrence.start is not None and occurrence.end is not None:
                ocg_ranges[(occurrence.page, occurrence.path)].append((occurrence.start, occurrence.end))
    for group in groups.values():
        if group.kind not in ("text", "form", "image"):
            continue
        for occurrence in group.occurrences:
            if occurrence.operation is None:
                continue
            if any(
                start <= operation <= end
                for depth, operation in enumerate(occurrence.path + (occurrence.operation,))
                for start, end in ocg_ranges.get((occurrence.page, occurrence.path[:depth]), [])
            ):
                occurrence.eligible = False

    candidates = []
    accepted: list[Group] = []
    for group in groups.values():
        candidate = candidate_from_group(group, len(pdf.pages))
        if candidate:
            candidates.append(candidate)
            accepted.append(group)

    # Anything a specific rule already covers must not be offered twice.
    operations, ranges = claimed_operations(accepted)
    for group in repeated_groups(context.marks, len(pdf.pages)):
        if overlaps_claim(group, operations, ranges):
            continue
        candidate = candidate_from_group(group, len(pdf.pages))
        if candidate:
            groups[group.key] = group
            candidates.append(candidate)

    def rank(item: dict[str, Any]) -> tuple[Any, ...]:
        return (item["confidence"] != "likely", -item["coverage"], item["kind"], item["id"])

    candidates.sort(key=rank)
    likely = [item for item in candidates if item["confidence"] == "likely"]
    possible = [item for item in candidates if item["confidence"] != "likely"]
    return groups, likely + possible[:MAX_POSSIBLE_CANDIDATES]


def apply_candidates(pdf: pikepdf.Pdf, groups: dict[str, Group], selected_ids: set[str]) -> int:
    selected_groups = [group for group in groups.values() if group.candidate_id in selected_ids]
    found_ids = {group.candidate_id for group in selected_groups}
    missing = selected_ids - found_ids
    if missing:
        raise RuntimeError("Candidate IDs changed or disappeared: " + ", ".join(sorted(missing)))

    removals: dict[tuple[int, tuple[int, ...]], dict[str, Any]] = defaultdict(
        lambda: {"operations": set(), "ranges": [], "annotations": set()}
    )
    removed = 0
    for group in selected_groups:
        for occurrence in group.occurrences:
            if not occurrence.eligible:
                continue
            target = removals[(occurrence.page, occurrence.path)]
            if occurrence.operations:
                target["operations"].update(occurrence.operations)
            elif occurrence.operation is not None:
                target["operations"].add(occurrence.operation)
            elif occurrence.start is not None and occurrence.end is not None:
                target["ranges"].append((occurrence.start, occurrence.end))
            elif occurrence.annotation is not None:
                target["annotations"].add(occurrence.annotation)
            removed += 1

    def rewrite(container: Any, page_index: int, path: tuple[int, ...]) -> None:
        targets = removals.get((page_index, path), {"operations": set(), "ranges": [], "annotations": set()})
        instructions = pikepdf.parse_content_stream(container)
        resources = container.obj.get("/Resources", {}) if not path else container.get("/Resources", {})
        resources_copy = pikepdf.Dictionary(resources)
        xobjects = pikepdf.Dictionary(resources.get("/XObject", {}))
        rewritten = []
        for index, instruction in enumerate(instructions):
            if index in targets["operations"] or any(a <= index <= b for a, b in targets["ranges"]):
                continue
            child_path = path + (index,)
            if (str(instruction.operator) == "Do" and any(
                    page == page_index and route[:len(child_path)] == child_path
                    for page, route in removals)):
                # Copy per invocation: shared Forms may also contain ordinary
                # content at another position or on another page.
                original = xobjects[instruction.operands[0]]
                clone = pdf.make_stream(original.read_bytes())
                for key, value in original.items():
                    if str(key) not in ("/Length", "/Filter", "/DecodeParms"):
                        clone[key] = value
                if "/Resources" not in clone:
                    clone["/Resources"] = resources
                rewrite(clone, page_index, child_path)
                name = pikepdf.Name("/WatermarkEdited" + str(index))
                while name in xobjects:
                    name = pikepdf.Name(str(name) + "x")
                xobjects[name] = clone
                instruction = pikepdf.ContentStreamInstruction([name], pikepdf.Operator("Do"))
            rewritten.append(instruction)
        resources_copy["/XObject"] = xobjects
        data = pikepdf.unparse_content_stream(rewritten)
        if path:
            container.write(data)
            container["/Resources"] = resources_copy
        else:
            container.obj["/Contents"] = pdf.make_stream(data)
            container.obj["/Resources"] = resources_copy
            if targets["annotations"]:
                kept = [a for i, a in enumerate(container.obj.get("/Annots", []))
                        if i not in targets["annotations"]]
                container.obj["/Annots"] = pikepdf.Array(kept)

    for page_index in sorted({page for page, _ in removals}):
        rewrite(pdf.pages[page_index], page_index, ())
    return removed


def open_pdf(path: Path) -> pikepdf.Pdf:
    try:
        return pikepdf.open(path)
    except pikepdf.PasswordError as error:
        raise RuntimeError("The PDF is encrypted and requires a password.") from error


def command_scan(input_path: Path) -> int:
    with open_pdf(input_path) as pdf:
        signed = document_has_signatures(pdf)
        _, candidates = scan_document(pdf)
        print(
            json.dumps(
                {
                    "version": 1,
                    "pages": len(pdf.pages),
                    "signed": signed,
                    "encrypted": pdf.is_encrypted,
                    "candidates": candidates,
                },
                ensure_ascii=False,
            )
        )
    return 0


def command_apply(input_path: Path, output_path: Path, candidate_ids: Iterable[str]) -> int:
    selected = set(candidate_ids)
    if not selected:
        raise RuntimeError("At least one --candidate ID is required.")
    if input_path.resolve() == output_path.resolve():
        raise RuntimeError("Input and output paths must be different.")
    with open_pdf(input_path) as pdf:
        if pdf.is_encrypted:
            raise RuntimeError("The PDF is encrypted and will not be modified.")
        if document_has_signatures(pdf):
            raise RuntimeError("The PDF contains a digital signature and will not be modified.")
        groups, candidates = scan_document(pdf)
        allowed = {candidate["id"] for candidate in candidates}
        unavailable = selected - allowed
        if unavailable:
            raise RuntimeError(
                "Candidate IDs are no longer high-confidence: " + ", ".join(sorted(unavailable))
            )
        removed = apply_candidates(pdf, groups, selected)
        pdf.save(output_path)
    print(json.dumps({"version": 1, "removed": removed, "candidateIDs": sorted(selected)}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="report high-confidence watermark candidates")
    scan.add_argument("input", type=Path)
    apply = subparsers.add_parser("apply", help="write a copy with selected candidates removed")
    apply.add_argument("input", type=Path)
    apply.add_argument("output", type=Path)
    apply.add_argument("--candidate", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            return command_scan(args.input)
        return command_apply(args.input, args.output, args.candidate)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
