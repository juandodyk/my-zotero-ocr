#!/usr/bin/env python3
"""Conservatively detect and remove common PDF watermark structures.

The helper is intentionally non-interactive. ``scan`` emits JSON describing
high-confidence candidates. ``apply`` requires their exact IDs and writes a
new PDF; it never modifies the input file.

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


@dataclass
class Group:
    key: str
    kind: str
    occurrences: list[Occurrence] = field(default_factory=list)
    text: str = ""
    explicit: bool = False

    @property
    def candidate_id(self) -> str:
        digest = hashlib.sha256((self.kind + "\0" + self.key).encode()).hexdigest()
        return self.kind + "-" + digest[:12]


def scan_page(
    page: pikepdf.Page, page_index: int, groups: dict[str, Group], *,
    stream: Any = None, initial_matrix: Matrix = IDENTITY, initial_alpha: float = 1.0,
    initial_lightness: float = 0.0,
    path: tuple[int, ...] = (), ancestors: tuple[Any, ...] = (),
) -> None:
    try:
        instructions = pikepdf.parse_content_stream(stream if stream is not None else page)
    except Exception as error:
        raise RuntimeError(f"Could not parse page {page_index + 1} content: {error}") from error

    visible_box = page_box(page)
    ctm = initial_matrix
    alpha = initial_alpha
    render_mode = 0
    lightness = initial_lightness
    graphics_stack: list[tuple[Matrix, float, float, float, int, float]] = []
    text_matrix = IDENTITY
    font_size = 0.0
    leading = 0.0
    text_block: dict[str, Any] | None = None
    marked_stack: list[dict[str, Any]] = []

    for index, instruction in enumerate(instructions):
        operands = instruction.operands
        operator = str(instruction.operator)

        if operator == "q":
            graphics_stack.append((ctm, alpha, font_size, leading, render_mode, lightness))
        elif operator == "Q":
            ctm, alpha, font_size, leading, render_mode, lightness = (
                graphics_stack.pop() if graphics_stack else (initial_matrix, initial_alpha, 0.0, 0.0, 0, initial_lightness))
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
            text_block = {"start": index, "shows": []}
        elif operator == "ET":
            if text_block:
                left, bottom, right, top = visible_box
                numbers = [o for o in text_block["shows"]
                           if re.fullmatch(r"[1-9][0-9]*", o.text)
                           and 0 < o.size <= 12 and abs(o.angle) <= 1
                           and left <= o.x <= left + (right - left) * 0.05]
                if (len(numbers) >= 20
                        and numbers[-1] is text_block["shows"][-1]
                        and all(str(instructions[o.operation].operator) == "Tj" for o in numbers)
                        and [int(o.text) for o in numbers] == list(range(1, len(numbers) + 1))
                        and max(o.x for o in numbers) - min(o.x for o in numbers) <= 2
                        and bottom + (top - bottom) / 2 <= numbers[0].y <= top
                        and all(4 <= a.y - b.y <= 24 for a, b in zip(numbers, numbers[1:]))):
                    key = "margin-line-numbers:1-" + str(len(numbers))
                    group = groups.setdefault(key, Group(
                        key=key, kind="margin-line-numbers", text="1–" + str(len(numbers))))
                    for occurrence in numbers:
                        group.occurrences.append(replace(
                            occurrence, kind="margin-line-numbers", eligible=True))
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
                        keyword and (oblique or occurrence.size >= 18 or occurrence.alpha <= 0.95)
                        or occurrence.size >= 24 and centered and span >= 0.20
                        and (oblique or occurrence.alpha <= 0.75 or occurrence.lightness >= 0.55)
                        and abs(occurrence.angle) <= 80
                    )
                )
                key = "text:" + occurrence.text.casefold()
                group = groups.setdefault(key, Group(key=key, kind="text", text=occurrence.text))
                group.explicit = keyword
                group.occurrences.append(occurrence)
            text_block = None
        elif operator == "Tr" and operands:
            render_mode = int(operands[0])
        elif operator == "Tf" and len(operands) >= 2:
            try:
                font_size = abs(float(operands[1]))
            except (TypeError, ValueError):
                font_size = 0.0
        elif operator == "Tm" and len(operands) == 6:
            text_matrix = as_matrix(operands)
        elif operator == "TL" and operands:
            leading = float(operands[0])
        elif operator == "T*":
            text_matrix = matrix_multiply(text_matrix, (1, 0, 0, 1, 0, -leading))
        elif operator in ("Td", "TD") and len(operands) >= 2:
            try:
                translation = (1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1]))
                text_matrix = matrix_multiply(text_matrix, translation)
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
            text_block["shows"].append(
                Occurrence(
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
            )
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
                              path=path + (index,), ancestors=ancestors + (identity,))
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
            key = kind + ":" + object_fingerprint(obj)
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


def candidate_from_group(group: Group, total_pages: int) -> dict[str, Any] | None:
    eligible = [occurrence for occurrence in group.occurrences if occurrence.eligible]
    pages = sorted({occurrence.page for occurrence in eligible})
    if not eligible:
        return None
    minimum_pages = 1 if group.explicit else min(2, total_pages)
    coverage = len(pages) / total_pages if total_pages else 0.0
    if len(pages) < minimum_pages:
        return None

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
        "label": label_by_kind[group.kind],
        "text": group.text[:160],
        "fullText": group.text,
        "pages": [page + 1 for page in pages],
        "pageCount": len(pages),
        "totalPages": total_pages,
        "coverage": coverage,
        "occurrences": len(eligible),
        "reason": reason,
    }


def scan_document(pdf: pikepdf.Pdf) -> tuple[dict[str, Group], list[dict[str, Any]]]:
    groups: dict[str, Group] = {}
    for page_index, page in enumerate(pdf.pages):
        scan_page(page, page_index, groups)

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
    for group in groups.values():
        candidate = candidate_from_group(group, len(pdf.pages))
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (-item["coverage"], item["kind"], item["id"]))
    return groups, candidates


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
            if occurrence.operation is not None:
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
