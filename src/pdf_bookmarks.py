#!/usr/bin/env python3
"""Generate or preview an embedded PDF outline using local PDF structure and text."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import csv
from difflib import SequenceMatcher
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

import pikepdf


FRONT_PAGES = 24
OCR_FRONT_PAGES = 16
CONTENTS = re.compile(
    r"^(?:(?:brief |detailed )?(?:table of )?contents|sommaire|table des matières|"
    r"inhaltsverzeichnis|[íi]ndice(?: general)?|contenido|sum[aá]rio)$", re.I
)
ROMAN = re.compile(r"(?=[mdclxvi]+$)m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})", re.I)
FOLIO = re.compile(r"^(.+?)((?:[.·]{2,}|…)\s*|\s+)(\d{1,5}|[ivxlcdm]{1,12})$", re.I)
NUMBERED = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)(?:[.)])?\s+(?=\S)")
TOC_NUMBERED = re.compile(r"^((?:\d{1,3}|[A-Z])(?:\.\d{1,3})+)(?:[.)])?\s+(?=\S)")
CHAPTER_NUMBER = re.compile(r"^\d{1,3}[.)]?(?:\s|$)")
PRINTED_CHAPTER = re.compile(r"^(?:chapter\s+)?(\d{1,3})[.)]?(?:\s|$)", re.I)
MATH_NOTATION = re.compile(
    r"\b\w+\s*[/=^_]\s*\w+|[α-ωΑ-Ω₀-₉ₐ-ₜ⁰-⁹²³¹±≤≥∑∫]|"
    r"\b[A-Z]\s+(?:and|or)\s+[A-Z]\b"
)
ODD_MATH_GLYPH = re.compile(r"[�¢¤]|(?<=\w)\?(?=[-\w])")
LEADER_RUN = re.compile(r"[.·…:]{2,}|[a-z]{12,}", re.I)
APPENDICES = re.compile(r"^appendices$", re.I)
APPENDIX = re.compile(r"^appendix\s+[a-z0-9]+(?:[:.\s]|$)", re.I)
BOOK_UNIT = re.compile(r"^(part|book|chapter|appendix|volume|section)\b", re.I)
SECTION = re.compile(
    r"^(abstract|introduction|background|literature review|related work|theory|"
    r"research design|(?:materials and )?methods?|methodology|data(?: and methods)?|"
    r"results|findings|analysis|discussion|results and discussion|conclusions?|"
    r"discussion and conclusions?|limitations|future work|acknowledg[em]+nts?|"
    r"references|bibliography|appendix(?:\s+[\w]+)?|supplementary materials?|"
    r"preface|foreword|index|list of symbols)$", re.I
)
DATE = re.compile(r"^(?:[A-Za-z]+\s+)?\d{1,2},?\s+\d{4}$|^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
CAPTION = re.compile(r"^(?:fig(?:ure)?\.?|table|equation|scheme|plate|algorithm|listing)\s+[\dA-Z]+\b", re.I)


@dataclass(frozen=True)
class Line:
    page: int
    text: str
    x: float
    y: float
    width: float
    size: float
    bold: bool = False
    height: float = 0


@dataclass(frozen=True)
class Entry:
    title: str
    level: int
    page: int | None
    y: float | None = None
    folio: str | None = None
    destination: object = None
    suggested: bool = True
    group: int | None = None
    repair_title: bool = False
    heading: Line | None = None
    title_review: tuple[str, ...] | None = None


def clean(value):
    return " ".join(value.split())


def normalized(text):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def spawn(args, data=None):
    completed = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"{Path(args[0]).name} failed: {message[-600:]}")
    return completed.stdout


def read_layout(path, first, last, tool):
    if first > last:
        return {}
    xml = spawn([tool, "-xml", "-i", "-stdout", "-q", "-zoom", "1", "-noroundcoord",
                 "-nomerge", "-hidden", "-f", str(first + 1), "-l", str(last + 1), path])
    pages = {}
    sizes = {}
    for page in ET.fromstring(xml).findall("page"):
        page_index = int(page.attrib["number"]) - 1
        w, h = float(page.attrib["width"]), float(page.attrib["height"])
        sizes.update({font.attrib["id"]: float(font.attrib["size"]) for font in page.findall("fontspec")})
        lines = []
        for item in page.findall("text"):
            raw = "".join(item.itertext())
            title = clean(raw)
            if not title:
                continue
            x, y, width, height = (float(item.attrib[key]) for key in ("left", "top", "width", "height"))
            lines.append(Line(page_index, title, x / w, y / h, width / w,
                              sizes.get(item.attrib["font"], height),
                              item.find(".//b") is not None, height / h))
        pages[page_index] = lines
    return pages


def ocr_page(path, index, args, *, resolution=1550, segmentation=6):
    page = str(index + 1)
    image = spawn([args.pdftoppm, "-cropbox", "-gray", "-scale-to", str(resolution), "-f", page,
                   "-l", page, "-singlefile", path])
    tsv = spawn([args.tesseract, "stdin", "stdout", "-l", args.language, "--psm", str(segmentation), "tsv"], image)
    rows = list(csv.DictReader(io.StringIO(tsv.decode("utf-8")), delimiter="\t"))
    if not rows:
        return []
    w, h = int(rows[0]["width"]), int(rows[0]["height"])
    words = defaultdict(list)
    for row in rows:
        if row["level"] == "5" and row["text"] and float(row["conf"]) >= 20:
            words[tuple(row[key] for key in ("block_num", "par_num", "line_num"))].append(row)
    lines = []
    for group in words.values():
        x = min(int(row["left"]) for row in group)
        y = min(int(row["top"]) for row in group)
        right = max(int(row["left"]) + int(row["width"]) for row in group)
        height = statistics.median(int(row["height"]) for row in group)
        combined = []
        end = x
        for row in group:
            left = int(row["left"])
            combined.append((" … " if left - end > height * 3 else " ") + row["text"])
            end = left + int(row["width"])
        lines.append(Line(index, clean("".join(combined)), x / w, y / h, (right - x) / w, height * 700 / h))
    return lines


def has_page_image(page):
    def images_in(resources, seen):
        for xobject in resources.get("/XObject", {}).values():
            if xobject.get("/Subtype") == pikepdf.Name.Image:
                yield int(xobject.get("/Width", 0)) * int(xobject.get("/Height", 0))
            elif xobject.get("/Subtype") == pikepdf.Name.Form and xobject.objgen not in seen:
                seen.add(xobject.objgen)
                yield from images_in(xobject.get("/Resources", {}), seen)

    return sum(images_in(page.resources, set())) >= 150_000


def enough_text(lines):
    return sum(len(re.findall(r"[^\W\d_]", line.text)) for line in lines) >= 55


def read_folio(label):
    if label.isascii() and label.isdecimal():
        return "arabic", int(label)
    if ROMAN.fullmatch(label):
        digits = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
        values = [digits[char] for char in label.lower()]
        return "roman", sum(-value if value < following else value
                            for value, following in zip(values, values[1:] + [0]))
    return None


def page_geometry(pdf):
    return [(tuple(page.mediabox), tuple(page.cropbox), page.rotation, page.label) for page in pdf.pages]


def signed(pdf):
    fields = list(pdf.Root.get("/AcroForm", {}).get("/Fields", []))
    seen = set()
    while fields:
        field = fields.pop()
        if field.is_indirect:
            if field.objgen in seen:
                continue
            seen.add(field.objgen)
        value = field.get("/V")
        if isinstance(value, pikepdf.Dictionary) and (
            value.get("/Type") == pikepdf.Name.Sig or field.get("/FT") == pikepdf.Name.Sig
        ):
            return True
        fields.extend(field.get("/Kids", []))
    return "/DocMDP" in pdf.Root.get("/Perms", {})


class Destinations:
    def __init__(self, pdf):
        self.pages = {page.obj.objgen: i for i, page in enumerate(pdf.pages)}
        names = pdf.Root.get("/Names", {})
        self.named = pikepdf.NameTree(names.Dests) if "/Dests" in names else {}
        self.legacy = pdf.Root.get("/Dests", {})

    def resolve(self, value):
        if isinstance(value, pikepdf.Name):
            value = self.legacy.get(str(value))
        elif isinstance(value, pikepdf.String):
            value = self.named.get(str(value))
        if isinstance(value, pikepdf.Dictionary):
            value = value.get("/D")
        if isinstance(value, pikepdf.Array) and len(value) >= 2:
            target = self.pages.get(value[0].objgen) if isinstance(value[0], pikepdf.Dictionary) else None
            if target is not None:
                return target, value
        return None, None


def link_rows(page, resolver):
    # Poppler XML uses the rotated media box with a top-left origin.
    x0, y0, x1, y1 = map(float, page.mediabox)
    w, h = x1 - x0, y1 - y0
    rotation = page.rotation % 360

    def transform(x, y):
        x, y = (x - x0) / w, (y - y0) / h
        if rotation == 90:
            return y, x
        if rotation == 180:
            return 1 - x, y
        if rotation == 270:
            return 1 - y, 1 - x
        return x, 1 - y

    result = []
    for annotation in page.obj.get("/Annots", []):
        if annotation.get("/Subtype") != pikepdf.Name.Link:
            continue
        action = annotation.get("/A", {})
        dest = annotation.get("/Dest")
        if dest is None and action.get("/S") == pikepdf.Name.GoTo:
            dest = action.get("/D")
        if dest is None:
            continue
        target, destination = resolver.resolve(dest)
        rect = annotation.get("/Rect", [])
        if len(rect) == 4:
            left, top = transform(float(rect[0]), float(rect[1]))
            right, bottom = transform(float(rect[2]), float(rect[3]))
            result.append((min(left, right), max(left, right), min(top, bottom), max(top, bottom), target, destination))
    return result


def strip_leaders(text):
    """Return title and whether a dotted or OCR-corrupted leader was removed."""
    for match in LEADER_RUN.finditer(text):
        token = match[0].casefold()
        if token.isalpha() and sum(char in "ces" for char in token) < len(token) * .70:
            continue
        tail = text[match.start():].casefold()
        letters = re.findall(r"[a-z0-9]", tail)
        if any(char not in "0o" for char in letters) and (
            len(letters) < 8 or sum(char in "ces" for char in letters) < len(letters) * .62
        ):
            continue
        title = text[:match.start()].rstrip(" .·…:")
        title = re.sub(r"(?:^|\s)[.·…:o0]{2,}$", "", title, flags=re.I).rstrip(" .·…:")
        return title, True
    return text, False


def toc_folio(text):
    if read_folio(text):
        return "", text.casefold(), False
    match = FOLIO.fullmatch(text)
    if match and read_folio(match[3]):
        title, noisy = strip_leaders(match[1])
        return title.rstrip(" .·…:"), match[3].casefold(), noisy or not match[2].isspace()
    return text, None, False


def contents_rows(lines, links, explicit):
    lines = sorted(lines, key=lambda line: (line.y, line.x))
    entries = []
    fragments = []
    for line in lines:
        title, folio, leaders = toc_folio(line.text)
        fragments.append([line, title, folio, leaders])
    # A right-aligned page number can be a separate text fragment, sometimes
    # with the OCR's interpretation of the dots attached to it.
    for index, (line, title, folio, leaders) in enumerate(fragments):
        if title or folio is None or not .05 < line.y < .95:
            continue
        left = [row for row in fragments if row[1] and row[2] is None
                and abs(row[0].y - line.y) < .011 and row[0].x < line.x
                and -.025 < line.x - row[0].x - row[0].width < .65
                and not CONTENTS.fullmatch(row[1])]
        if not left:
            continue
        previous = max(left, key=lambda row: row[0].x + row[0].width)
        previous[2], previous[3] = folio, True
        previous[0] = replace(previous[0], width=line.x + line.width - previous[0].x)
        fragments[index][2] = None

    # Rejoin chapter numbers and titles split into several nearby fragments.
    fragments.sort(key=lambda row: (round(row[0].y, 2), row[0].x))
    for index, row in enumerate(fragments):
        line, title, folio, _ = row
        if not CHAPTER_NUMBER.match(title or line.text) or not (title or folio):
            continue
        for next_row in fragments[index + 1:]:
            following, next_title, next_folio, _ = next_row
            if following.y - line.y > .012:
                break
            if (not next_title or next_folio or abs(following.y - line.y) > .009
                    or not -.025 < following.x - line.x - line.width < .075):
                continue
            row[1] = (row[1] or line.text) + " " + next_title
            row[2] = None
            line = replace(line, width=following.x + following.width - line.x)
            row[0] = line
            next_row[1] = ""

    for line, title, folio, leaders in fragments:
        if not title or CONTENTS.fullmatch(title.strip(" =:")) or not .035 < line.y < .96:
            continue
        if len(title) > 220 or not any(char.isalpha() for char in title):
            continue
        midpoint = line.y + .006
        linked = [(target, dest) for left, right, top, bottom, target, dest in links
                  if top - .012 <= midpoint <= bottom + .008 and left < line.x + line.width
                  and right >= line.x - .008 and target is not None and target != line.page]
        if len({target for target, _ in linked}) == 1:
            target, dest = linked[0]
        else:
            target, dest = None, None
        entries.append((line, title, folio, target, dest, leaders))

    credible_edges = [line.x + line.width for line, _, folio, _, _, leaders in entries if folio and leaders]
    # Without leaders or internal links, require a visible contents heading
    # and a column of at least two aligned page numbers.
    if explicit and not credible_edges:
        edges = [line.x + line.width for line, _, folio, _, _, _ in entries if folio]
        credible_edges = [edge for edge in edges if edge > .50
                          and sum(abs(edge - other) < .025 for other in edges) >= 2]
    return [(line, title, folio, target, dest, leaders)
            for line, title, folio, target, dest, leaders in entries
            if (target is not None or folio and (
                leaders or any(abs(line.x + line.width - edge) < .025 for edge in credible_edges)
            ) or folio is None and target is None)]


def join_contents_title(fragments):
    baselines = []
    for line in sorted(fragments, key=lambda line: line.y + line.height):
        if not baselines or line.y + line.height - baselines[-1][0] > .009:
            baselines.append((line.y + line.height, []))
        baselines[-1][1].append(line)
    title_lines = []
    for _, group in baselines:
        words = []
        right = None
        for line in sorted(group, key=lambda line: line.x):
            if right is not None and line.x - right > .0035:
                words.append(" ")
            words.append(line.text)
            right = line.x + line.width
        title_lines.append("".join(words))
    return clean(" ".join(title_lines))


def linked_folio_rows(lines, links):
    # Some publishers link just the printed folio. The small right-aligned
    # rectangles delimit entries, including wrapped titles and split digits.
    if len(links) < 3 or any(right < .6 or right - left > .10
                             for left, right, *_ in links):
        return []
    if max(right for _, right, *_ in links) - min(right for _, right, *_ in links) > .025:
        return []
    rows = []
    previous_bottom = None
    for left, right, top, bottom, target, dest in sorted(links, key=lambda link: link[2]):
        if previous_bottom is not None and top < previous_bottom:
            return []
        in_band = [line for line in lines if top - .004 <= line.y + line.height / 2 <= bottom + .003]
        digits = sorted((line for line in in_band
                         if left - .006 <= line.x + line.width / 2 <= right + .006), key=lambda line: line.x)
        folio = "".join(line.text for line in digits).lower()
        title_fragments = [line for line in lines if line.x + line.width / 2 < left - .006
                           and max(top - .037, previous_bottom or 0) < line.y + line.height / 2 < bottom + .003]
        previous_bottom = bottom
        if not title_fragments or not read_folio(folio):
            return []
        rightmost = max(title_fragments, key=lambda line: line.x)
        if rightmost.text.casefold() == "page" and left - .10 < rightmost.x:
            title_fragments.remove(rightmost)
        title = join_contents_title(title_fragments)
        if not any(char.isalpha() for char in title) or len(title) > 220:
            return []
        first = min(title_fragments, key=lambda line: (line.y + line.height, line.x))
        start = min(line.x for line in title_fragments)
        row = replace(first, text=title, x=start, width=right - start)
        rows.append((row, title, folio, target, dest, False))
    return rows


def read_contents(pages, pdf, resolver):
    collected = []
    started = False
    for index, lines in sorted(pages.items()):
        explicit = any(CONTENTS.fullmatch(toc_folio(line.text)[0].strip(" =:")) for line in lines)
        if not (explicit or started):
            parsed = [toc_folio(line.text) for line in lines]
            if sum(bool(folio and leaders) for _, folio, leaders in parsed) < 3:
                continue
        links = link_rows(pdf.pages[index], resolver)
        rows = linked_folio_rows(lines, links) or contents_rows(lines, links, explicit)
        destinations = sum(folio is not None or target is not None for _, _, folio, target, _, _ in rows)
        minimum = 1 if explicit or started and any(target is not None for _, _, _, target, _, _ in rows) else 2
        if destinations >= (minimum if explicit or started else 3):
            collected.extend(rows)
            started = True
        elif started:
            break
        elif explicit:
            started = True
    anchor_rows = [row for row in collected if row[2] is not None or row[3] is not None]
    if len(anchor_rows) < 2:
        return []
    lefts = sorted({round(line.x, 2) for line, *_ in anchor_rows})
    # A wide column break is not an indentation level.
    column_break = next((right - .01 for left, right in zip(lefts, lefts[1:]) if right - left > .27), 1)
    collected.sort(key=lambda row: (row[0].page, row[0].x >= column_break, round(row[0].y, 2), row[0].x))
    margins = {
        side: sorted({round(line.x, 2) for line, *_ in anchor_rows if (line.x >= column_break) == side})
        for side in {line.x >= column_break for line, *_ in anchor_rows}
    }
    parts = any(re.match(r"^(part|book|volume)\b", title, re.I) for _, title, *_ in collected)
    entries = []
    chapter_prefix = None
    inside_appendices = False
    has_appendix_group = any(APPENDICES.fullmatch(title) for _, title, *_ in collected)
    for index, (line, title, folio, target, dest, leaders) in enumerate(collected):
        number = TOC_NUMBERED.match(title) or NUMBERED.match(title)
        base = 2 if parts else 1
        if folio is None and target is None:
            next_number = next(
                (number for following, next_title, *_ in collected[index + 1:index + 3]
                 if following.page == line.page and 0 < following.y - line.y < .09
                 and (number := TOC_NUMBERED.match(next_title))),
                None,
            )
            if APPENDICES.fullmatch(title):
                inside_appendices = True
                level = 1
            elif BOOK_UNIT.match(title) and (next_number or APPENDIX.match(title)):
                level = 2 if inside_appendices else base
            elif next_number and (title.isupper() or CHAPTER_NUMBER.match(title)):
                level = base
                chapter_prefix = next_number[1].split(".")[0]
            else:
                continue
        elif re.match(r"^(part|book|volume)\b", title, re.I):
            level = 1
        elif number:
            prefix = number[1].split(".")[0]
            level = base + number[1].count(".") + int(has_appendix_group and prefix.isalpha())
            chapter_prefix = prefix
        else:
            indents = margins.get(line.x >= column_break, [])
            level = base + min(2, sum(round(line.x, 2) - left >= .023 for left in indents))
            if BOOK_UNIT.match(title) or SECTION.fullmatch(title):
                level = base
            if re.fullmatch(r"exe.{1,6}ises", title, re.I):
                title, level = "Exercises", base + int(chapter_prefix is not None)
        raw_folio = FOLIO.fullmatch(line.text)
        repair_title = bool(raw_folio and strip_leaders(raw_folio[1])[1])
        entries.append((Entry(title, level, target, folio=folio, destination=dest, repair_title=repair_title), line.page))
    return restore_chapter_numbers(entries, 2 if parts else 1)


def restore_chapter_numbers(rows, level):
    """Infer a lost numeral inside an otherwise numbered run of chapters."""
    boundaries = [index for index, (entry, _) in enumerate(rows) if entry.level <= level]
    run = []

    def apply_run():
        if sum(explicit for _, _, explicit in run) < 2:
            return
        for index, number, explicit in run:
            if not explicit:
                entry, toc_page = rows[index]
                rows[index] = replace(entry, title=f"{number} {entry.title}"), toc_page

    for index, end in zip(boundaries, boundaries[1:] + [len(rows)]):
        entry = rows[index][0]
        printed = PRINTED_CHAPTER.match(entry.title) if entry.level == level else None
        children = [TOC_NUMBERED.match(child.title) for child, _ in rows[index + 1:end]
                    if child.level == level + 1]
        numbers = [number[1].split(".") for number in children if number and number[1].count(".") == 1]
        prefixes = {prefix for prefix, _ in numbers}
        sections = [int(section) for _, section in numbers]
        prefix = numbers[0][0] if len(prefixes) == 1 and len(sections) >= 2 and sections == sorted(set(sections)) else ""
        if printed and (not prefixes or set(prefixes) == {printed[1]}):
            number = int(printed[1])
        elif (entry.level == level and entry.page is None and entry.folio is None
              and not (printed or BOOK_UNIT.match(entry.title) or APPENDICES.fullmatch(entry.title))
              and prefix.isdecimal()):
            number = int(prefix)
        else:
            number = 0
        if not number or run and number != run[-1][1] + 1:
            apply_run()
            run.clear()
        if number:
            run.append((index, number, printed is not None))
    apply_run()
    return rows


def recover_missing_contents_rows(rows, pages, pdf, path, args):
    noisy = Counter(page for entry, page in rows if entry.repair_title)
    for index in sorted(noisy, key=lambda page: (-noisy[page], page))[:6]:
        if noisy[index] < 2 or not has_page_image(pdf.pages[index]):
            continue
        try:
            fresh = ocr_page(path, index, args, resolution=2300, segmentation=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # This refinement is optional; the existing OCR is still usable.
            continue
        lines = pages[index]
        for line in fresh:
            title, folio, leaders = toc_folio(line.text)
            if not (folio and leaders and len(normalized(title)) >= 3):
                continue
            if any(abs(old.y - line.y) < .012
                   and old.x < line.x + line.width and line.x < old.x + old.width for old in lines):
                continue
            lines.append(line)


def title_key(text):
    number = TOC_NUMBERED.match(text) or NUMBERED.match(text)
    if number:
        text = text[number.end():]
    text = re.sub(r"^(?:chapter|part|book|section)\s+(?:\d+|[ivxlcdm]+)\W+", "", text, flags=re.I)
    return normalized(text)


def heading_matches(entries, pages, excluded):
    wanted = {title_key(entry.title) for entry in entries}
    matches = defaultdict(list)
    for page, lines in pages.items():
        if page in excluded:
            continue
        for index, line in enumerate(lines):
            if not .035 <= line.y <= .94:
                continue
            # Handle a title broken across two consecutive lines.
            texts = [line.text]
            if index + 1 < len(lines):
                following = lines[index + 1]
                if 0 < following.y - line.y < .05 and abs(following.x - line.x) < .06:
                    texts.append(line.text + " " + following.text)
            for text in texts:
                key = title_key(text)
                if key in wanted and len(key) >= 3:
                    matches[key].append(replace(line, text=text, height=0) if text != line.text else line)
    return matches


def book_heading_hits(lines):
    # A repeated running header can spell the chapter title exactly.
    hits = {}
    for line in lines:
        current = hits.get(line.page)
        if current is None or (line.y >= .10, line.size) > (current.y >= .10, current.size):
            hits[line.page] = line
    if len(hits) > 1 and any(line.y >= .10 for line in hits.values()):
        return {page: line for page, line in hits.items() if line.y >= .10}
    return hits


def resolved_book_entry(entry, page, hits, pages):
    if page is None:
        return entry
    line = hits.get(page)
    title = entry.title
    if line and not TOC_NUMBERED.match(title) and (entry.repair_title or entry.folio is None or title.isupper()):
        chapter = CHAPTER_NUMBER.match(title)
        if chapter:
            title = line.text if CHAPTER_NUMBER.match(line.text) else chapter[0].strip() + " " + line.text
        elif entry.folio is None and PRINTED_CHAPTER.match(line.text):
            title = line.text
        elif not NUMBERED.match(line.text):
            title = line.text
            if entry.level == 1 and entry.folio is None:
                number = next((candidate.text for candidate in pages.get(page, [])
                               if candidate.text.isdecimal() and candidate.size >= line.size
                               and abs(candidate.x - line.x) < .05
                               and 0 < line.y - candidate.y < .065), None)
                if number:
                    title = number + " " + title
    if entry.repair_title and (candidate := numbered_body_heading(entry, pages.get(page, []))):
        line, title = candidate
    return replace(entry, title=title, page=page, y=line.y if line else None, heading=line)


def numbered_body_heading(entry, lines):
    number = TOC_NUMBERED.match(entry.title)
    current = title_key(entry.title)
    options = []
    for candidate in lines:
        candidate_number = TOC_NUMBERED.match(candidate.text)
        if not candidate_number or number and number[1] != candidate_number[1]:
            continue
        proposed = candidate.text
        if candidate.y < .10 and entry.folio:
            match = FOLIO.fullmatch(proposed)
            if match and match[3].casefold() == entry.folio:
                proposed = match[1]
        ratio = SequenceMatcher(None, current, title_key(proposed)).ratio()
        if ratio >= (.66 if number else .77):
            options.append((candidate, proposed, ratio))
    if options:
        line, title, _ = max(options, key=lambda row: (row[0].y >= .10, row[2]))
        return line, title


def short_symbol_disagrees(left, right):
    a, b = left.split(), right.split()
    if len(a) != len(b):
        return False
    return any(x != y and all(re.fullmatch(r"[A-Z][A-Za-z0-9]{0,2}", token) for token in (x, y))
               for x, y in zip(a, b))


def heading_width(line, lines):
    """Include a formula that Poppler emitted as adjacent text on this line."""
    right = line.x + line.width
    for fragment in sorted(lines, key=lambda item: item.x):
        if (fragment.x > line.x and -.02 <= fragment.x - right < .04
                and abs(fragment.y - line.y) < .005
                and .8 * line.size <= fragment.size <= 1.2 * line.size):
            right = max(right, fragment.x + fragment.width)
    return right - line.x


def heading_crop(path, page, line, args, resolution):
    # Poppler XML and the raster use the same unrotated MediaBox.
    x0, y0, x1, y1 = map(float, page.mediabox)
    width, height = x1 - x0, y1 - y0
    scale = resolution / 72
    x = max(0, math.floor((width * line.x - 6) * scale))
    y = max(0, math.floor((height * line.y - 4) * scale))
    right = min(math.ceil(width * scale), math.ceil((width * (line.x + line.width) + 8) * scale))
    line_height = max(height * line.height, line.size)
    bottom = min(math.ceil(height * scale), math.ceil((height * line.y + line_height + 3) * scale))
    number = str(line.page + 1)
    image = spawn([args.pdftoppm, "-gray", "-r", str(resolution), "-f", number, "-l", number,
                   "-x", str(x), "-y", str(y), "-W", str(right - x), "-H", str(bottom - y), "-singlefile", path])
    text = spawn([args.tesseract, "stdin", "stdout", "-l", args.language, "--psm", "7"], image)
    return clean(text.decode("utf-8"))


def title_reading(text, expected):
    number = TOC_NUMBERED.match(text)
    if number is None or number[1] != expected:
        return None
    value = unicodedata.normalize("NFC", text[number.end():])
    return re.sub(r"\s*([/=^_])\s*", r"\1", value)


def review_math_titles(entries, rows, pages, pdf, path, args):
    """Compare small heading crops for notation the text layer may have mangled."""
    checked = 0
    for index, entry in enumerate(entries):
        number = TOC_NUMBERED.match(entry.title)
        if not number or entry.page is None or not entry.suggested:
            continue
        source = rows[entry.group - 1 if entry.group is not None else index][0].title
        texts = (source, entry.title)
        suspicion = any(MATH_NOTATION.search(title) or ODD_MATH_GLYPH.search(title) for title in texts)
        if not suspicion and not short_symbol_disagrees(*texts):
            continue
        page = pdf.pages[entry.page]
        if page.rotation % 360 or not has_page_image(page):
            continue
        if entry.page not in pages:
            pages.update(read_layout(path, entry.page, entry.page, args.pdftohtml))
        lines = pages.get(entry.page, [])
        candidate = numbered_body_heading(entry, lines)
        line = candidate[0] if candidate else entry.heading
        if not line or not line.height or line.y < .10:
            continue
        line = replace(line, width=heading_width(line, lines))
        readings = []
        baseline = title_reading(entry.title, number[1])
        if checked < 10:
            checked += 1
            for resolution in (280, 360):
                try:
                    text = heading_crop(path, page, line, args, resolution)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    break
                reading = title_reading(text, number[1])
                if reading and SequenceMatcher(None, normalized(baseline), normalized(reading)).ratio() >= .78:
                    readings.append(reading)
        original = title_reading(source, number[1])
        # Reuse a contents spelling only when both crops independently agree
        # with it. Alternate OCR readings otherwise stay available for review.
        if original and readings == [original, original] and not ODD_MATH_GLYPH.search(original):
            if original != baseline:
                entries[index] = replace(entry, title=f"{number[1]} {original}")
            continue
        variants = dict.fromkeys(reading for reading in [original, *readings] if reading and reading != baseline)
        if variants or ODD_MATH_GLYPH.search(baseline):
            entries[index] = replace(entry, title_review=tuple(f"{number[1]} {reading}" for reading in variants))



def printed_folio_pages(pages, excluded):
    """Derive printed page numbers from consistent runs of header/footer folios."""
    offsets = defaultdict(set)
    for page, lines in pages.items():
        if page in excluded:
            continue
        for line in lines:
            if .028 < line.y < .095 or .92 < line.y < .975:
                folio = read_folio(line.text)
                if folio and folio[1] > 0:
                    offsets[folio[0], page + 1 - folio[1]].add(page)

    result = defaultdict(list)
    for (kind, delta), indices in offsets.items():
        runs = []
        for page in sorted(indices):
            if not runs or page - runs[-1][-1] > 3:
                runs.append([])
            runs[-1].append(page)
        for run in runs:
            span = run[-1] - run[0] + 1
            if len(run) < 3 or len(run) < .75 * span:
                continue
            for page in range(run[0], run[-1] + 1):
                if page not in excluded:
                    result[kind, page + 1 - delta].append(page)
    return result


def resolve_contents(rows, pages, pdf, *, alternatives=False):
    entries = [entry for entry, _ in rows]
    excluded = {index for _, index in rows}
    matches = heading_matches(entries, pages, excluded)
    labels = defaultdict(list)
    if "/PageLabels" in pdf.Root:
        for index, page in enumerate(pdf.pages):
            labels[page.label.casefold()].append(index)

    printed = printed_folio_pages(pages, excluded) if not labels else {}
    hits = [book_heading_hits(matches.get(title_key(entry.title), [])) for entry in entries]
    resolved = []
    previous = -1
    for entry, candidates in zip(entries, hits):
        if entry.page is not None:
            resolved.append(entry)
            previous = entry.page
            continue
        labeled = [index for index in labels.get(entry.folio, []) if index not in excluded]
        if not labeled and entry.folio:
            labeled = printed.get(read_folio(entry.folio), [])
        forward = [index for index in candidates if index >= previous]
        if len(labeled) == 1 and labeled[0] >= previous:
            page = labeled[0]
        elif len(forward) == 1:
            page = forward[0]
        else:
            overlap = [index for index in labeled if index in forward]
            page = overlap[0] if len(overlap) == 1 else None
        resolved.append(resolved_book_entry(entry, page, candidates, pages))
        if page is not None:
            previous = page

    # A bad OCR folio can point backward to another chapter. Search for its
    # heading between independently mapped neighboring entries.
    previous = -1
    next_pages = [len(pdf.pages)] * len(resolved)
    following = len(pdf.pages)
    for index in reversed(range(len(resolved))):
        next_pages[index] = following
        if resolved[index].page is not None:
            following = resolved[index].page
    for index, entry in enumerate(resolved):
        if entry.page is None:
            candidates = hits[index]
            in_range = [page for page in candidates if previous <= page <= next_pages[index]]
            if len(in_range) == 1:
                entry = resolved_book_entry(entry, in_range[0], candidates, pages)
                resolved[index] = entry
        if entry.page is not None:
            previous = entry.page

    sequences = []
    previous_folio = None
    for index, entry in enumerate(resolved):
        folio = read_folio(entry.folio) if entry.folio else None
        if folio is None:
            continue
        if previous_folio is None or folio[0] != previous_folio[0] or folio[1] < previous_folio[1]:
            sequences.append([])
        sequences[-1].append((index, folio[1]))
        previous_folio = folio
    for group in sequences:
        offsets = [(index, number, resolved[index].page + 1 - number)
                   for index, number in group if resolved[index].page is not None]
        for index, number in group:
            entry = resolved[index]
            if entry.page is not None:
                continue
            before = next((offset for k, _, offset in reversed(offsets) if k < index), None)
            after = next((offset for k, _, offset in offsets if k > index), None)
            if before is not None and before == after:
                offset = before
            elif len({n for _, n, _ in offsets}) >= 2 and len({delta for _, _, delta in offsets}) == 1:
                offset = offsets[0][2]
            else:
                continue
            target = number + offset - 1
            lower = next((row.page for row in reversed(resolved[:index]) if row.page is not None), 0)
            if lower <= target < len(pdf.pages) and target <= next_pages[index] and target not in excluded:
                resolved[index] = resolved_book_entry(entry, target, hits[index], pages)
    if not alternatives:
        return resolved
    candidates = []
    for group, entry in enumerate(resolved, 1):
        candidates.append(replace(entry, group=group))
        if entry.page is None:
            for hit in sorted(hits[group - 1].values(), key=lambda line: line.page)[:5]:
                candidates.append(replace(entry, page=hit.page, y=hit.y, suggested=False, group=group))
    return candidates


def probe_chapters(rows, pages, path, args):
    # Sample chapter starts, then spend the remaining budget near the best offset.
    excluded = {index for _, index in rows}
    front_end = max(excluded)
    anchors = [(entry, read_folio(entry.folio)) for entry, _ in rows if entry.page is None and entry.folio]
    anchors = [(entry, folio[1]) for entry, folio in anchors if folio and folio[0] == "arabic"]
    if len(anchors) < 2:
        return
    targets = list(dict.fromkeys([anchors[0], anchors[-1], anchors[len(anchors) // 2], anchors[1]]))
    budget = 16
    offsets = [front_end + 1, front_end + 2, front_end, front_end + 3, front_end + 4, 0,
               front_end - 1, front_end + 5, front_end + 6]
    known_offset = None
    for entry, number in targets:
        for offset in ([known_offset] if known_offset is not None else offsets):
            index = number + offset - 1
            if index in excluded or index not in pages:
                continue
            if not enough_text(pages[index]) and budget:
                pages[index] = ocr_page(path, index, args)
                budget -= 1
            if heading_matches([entry], {index: pages[index]}, excluded):
                known_offset = offset
                break
            if not budget:
                return


def tagged_headings(path, pages, tool):
    structure = spawn([tool, "-struct-text", path]).decode("utf-8", errors="replace")
    lines = structure.splitlines()
    headings = []
    for index, line in enumerate(lines):
        tag = re.match(r"^(\s*)H([1-6]?)\s+\(", line)
        if tag is None:
            continue
        text = []
        for child in lines[index + 1:]:
            if len(child) - len(child.lstrip()) <= len(tag[1]):
                break
            value = child.strip()
            if value.startswith('"') and value.endswith('"'):
                text.append(value[1:-1])
        if text:
            headings.append(Entry(clean(" ".join(text)), int(tag[2] or "1"), None))
    matches = heading_matches(headings, pages, set())
    result = []
    previous = (-1, -1)
    for entry in headings:
        anchor = next((line for line in matches[title_key(entry.title)]
                       if (line.page, line.y) > previous), None)
        if anchor:
            previous = (anchor.page, anchor.y)
            result.append(replace(entry, page=anchor.page, y=anchor.y))
    return result


def layout_headings(pages):
    body_sizes = Counter()
    occurrence = defaultdict(set)
    for group in pages.values():
        for line in group:
            if not .045 <= line.y <= .94:
                continue
            if len(line.text) > 45:
                body_sizes[round(line.size)] += len(line.text)
            occurrence[normalized(line.text)].add(line.page)
    body = body_sizes.most_common(1)[0][0] if body_sizes else 0
    headings = []
    for page, group in pages.items():
        skip_through = -1
        for index, line in enumerate(group):
            if index <= skip_through or not .045 <= line.y <= .94 or not 3 <= len(line.text) <= 140:
                continue
            number = NUMBERED.match(line.text)
            title = line.text[number.end():] if number else line.text
            known = SECTION.fullmatch(title) is not None
            unit = BOOK_UNIT.match(title)
            large = bool(body and line.size >= body * 1.16)
            strong = large or line.bold
            if (len(normalized(title)) < 3 or CAPTION.match(title) or CONTENTS.fullmatch(title)
                    or DATE.fullmatch(title) or re.search(r"[@]|https?://|[;,]$", title)
                    or len(occurrence[normalized(line.text)]) > 1 and not (known and strong)):
                continue
            centered = abs(line.x + line.width / 2 - .5) < .05
            if not (known or number or unit) and (
                page == 0 and line.y < .20 or page < 6 and line.y < .28 and centered and large
            ):
                continue
            previous = group[index - 1] if index else None
            following = group[index + 1] if index + 1 < len(group) else None
            separated = (previous is None or line.y - previous.y > .025) and (
                following is None or following.y - line.y > .022)
            if number:
                root = int(number[1].split(".")[0])
                if not 0 < root < 100 or len(title) >= 105 or not title[0].isupper():
                    continue
                if strong or known or separated and len(title) <= 65:
                    headings.append((line, line.text, strong or known, number[1]))
                continue
            # Join a prominent centered heading wrapped across two lines.
            if (large and centered and following and body and line.size >= body * 1.45
                    and 0 < following.y - line.y <= .038 and following.size == line.size
                    and abs(following.x + following.width / 2 - .5) < .05
                    and 3 <= len(following.text) <= 45 and not SECTION.fullmatch(following.text)
                    and not NUMBERED.match(following.text)):
                headings.append((line, line.text + " " + following.text, True, None))
                skip_through = index + 1
            elif known or unit and strong or strong and separated and len(title) <= 95:
                headings.append((line, line.text, True, None))
    enough_headings = sum(suggested for _, _, suggested, _ in headings) >= 2
    size_counts = Counter(round(line.size) for line, _, yes, number in headings
                          if yes and not number and body and line.size >= body * 1.16)
    ranks = sorted(size_counts, reverse=True)
    if ranks:
        # A single oversized title (e.g. an appendix cover) is at the same
        # level as regular section titles, rather than parenting the paper.
        top = next((size for size in ranks if size_counts[size] > 1), ranks[0])
        ranks = [size for size in ranks if size <= top]
    entries = []
    for line, title, suggested, number in headings:
        level = number.count(".") + 1 if number else 1
        if number is None and round(line.size) in ranks:
            level = min(3, 1 + ranks.index(round(line.size)))
        entries.append(Entry(title, level, line.page, line.y, suggested=suggested and enough_headings))
    return entries


def geometry_top(page, fraction):
    # A rotated page can use a page-level destination. For other pages, translate
    # Poppler's media-box-relative height and keep the destination within the crop.
    if page.rotation % 360:
        return None
    bottom, top = float(page.mediabox[1]), float(page.mediabox[3])
    return max(float(page.cropbox[1]), min(float(page.cropbox[3]), top - (fraction - .01) * (top - bottom)))


def outline_items(pdf, resolver):
    def walk(nodes, depth=1):
        for node in nodes:
            destination = node.destination
            if destination is None and node.action is not None and node.action.get("/S") == pikepdf.Name.GoTo:
                destination = node.action.get("/D")
            page, _ = resolver.resolve(destination)
            yield node, page, depth
            yield from walk(node.children, depth + 1)
    return list(walk(pdf.open_outline(strict=True, max_depth=32).root))


def save_verified_outline(pdf, output, expected):
    geometry = page_geometry(pdf)
    # Save and verify in the destination directory, then publish without clobbering.
    with tempfile.TemporaryDirectory(prefix=".bookmark-", dir=output.parent) as staging:
        path = Path(staging) / "bookmarked.pdf"
        pdf.save(path, fix_metadata_version=False)
        with pikepdf.Pdf.open(path, attempt_recovery=False) as reopened:
            actual = [(node.title, page, depth) for node, page, depth in outline_items(reopened, Destinations(reopened))]
            if actual != expected or page_geometry(reopened) != geometry:
                raise ValueError("The saved outline or page geometry failed verification.")
        os.link(path, output)


def output_entries(pdf, output, selected, *, replace_existing=False):
    expected = []
    with pdf.open_outline(max_depth=32) as outline:
        if replace_existing:
            outline.root.clear()
        stack = []
        for entry in selected:
            level = min(entry.level, len(stack) + 1)
            del stack[level - 1:]
            top = geometry_top(pdf.pages[entry.page], entry.y) if entry.y is not None else None
            if entry.destination is not None:
                item = pikepdf.OutlineItem(entry.title, entry.destination)
            elif top is not None:
                item = pikepdf.OutlineItem(entry.title, entry.page, "FitH", top=top)
            else:
                item = pikepdf.OutlineItem(entry.title, entry.page)
            (stack[-1].children if stack else outline.root).append(item)
            stack.append(item)
            expected.append((entry.title, entry.page, level))
    save_verified_outline(pdf, output, expected)


def refresh_outline(pdf, resolver, output, entries, choices, include_candidates):
    existing = outline_items(pdf, resolver)
    by_key = defaultdict(list)
    for position, (node, page, depth) in enumerate(existing):
        key = title_key(node.title)
        if page is not None and key:
            by_key[page, depth, key].append(position)

    approved = select_entries(entries, choices)
    if choices is None:
        ids = [index for index, entry in enumerate(entries, 1) if entry.suggested and entry.page is not None]
    else:
        ids = sorted(choice["id"] for choice in choices)
    # Validate that an existing and proposed title uniquely identify the same
    # outline item. Use the extracted title for matching even after a manual edit.
    matches = defaultdict(list)
    for id_, proposed in zip(ids, approved):
        source = entries[id_ - 1]
        key = title_key(source.title)
        positions = by_key[source.page, source.level, key]
        if key and len(positions) == 1:
            position = positions[0]
            old_title = existing[position][0].title
            old_number = TOC_NUMBERED.match(old_title) or PRINTED_CHAPTER.match(old_title)
            new_number = TOC_NUMBERED.match(source.title) or PRINTED_CHAPTER.match(source.title)
            if old_number and new_number and old_number[1] != new_number[1]:
                continue
            matches[position].append((id_, proposed))
    titles = {}
    changes = []
    title_warnings = []
    visible_ids = set()
    for position, candidates in sorted(matches.items()):
        if len(candidates) != 1:
            continue
        id_, entry = candidates[0]
        old = existing[position][0].title
        if entry.title != old and entry.title_review is None:
            changes.append({"id": id_, "page": entry.page + 1, "oldTitle": old, "title": entry.title})
            titles[position] = entry.title
            visible_ids.add(id_)
        if entry.title_review is not None:
            title_warnings.append(old)
            visible_ids.add(id_)

    result = {"status": "preview" if output is None else "unchanged",
              "count": len(changes), "changes": changes}
    if title_warnings:
        result["titleWarnings"] = title_warnings
    if output is None or include_candidates:
        result["candidates"] = [row for row in report_candidates(entries) if row["id"] in visible_ids]
    if output is not None and changes:
        expected = []
        for position, (node, page, depth) in enumerate(existing):
            title = titles.get(position, node.title)
            if position in titles:
                # Edit the existing dictionary directly, preserving the tree,
                # named and external actions, styles, and exact view coordinates.
                node.obj["/Title"] = pikepdf.String(title)
            expected.append((title, page, depth))
        save_verified_outline(pdf, output, expected)
        result["status"] = "refreshed"
    return result


def select_entries(entries, choices):
    if choices is None:
        return [entry for entry in entries if entry.suggested and entry.page is not None]
    if not isinstance(choices, list) or len(choices) > len(entries):
        raise ValueError("Bookmark choices must be a list of candidate IDs and levels.")
    selected = {}
    groups = set()
    for choice in choices:
        if not isinstance(choice, dict) or not {"id", "level"} <= choice.keys() <= {"id", "level", "title"}:
            raise ValueError("Each bookmark choice requires an id and a level, with an optional title.")
        id_, level = choice["id"], choice["level"]
        if type(id_) is not int or not 1 <= id_ <= len(entries) or id_ in selected:
            raise ValueError(f"Unknown or duplicate bookmark candidate ID: {id_}")
        if type(level) is not int or not 1 <= level <= 12 or entries[id_ - 1].page is None:
            raise ValueError(f"Bookmark candidate {id_} has an invalid level or no verified page.")
        entry = entries[id_ - 1]
        if entry.group in groups:
            raise ValueError(f"Multiple candidates selected for contents entry {entry.group}.")
        if entry.group is not None:
            groups.add(entry.group)
        if "title" in choice:
            title = choice["title"]
            if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 220
                    or any(unicodedata.category(char) in {"Cc", "Cs"} for char in title)):
                raise ValueError(f"Bookmark candidate {id_} has an invalid title.")
            entry = replace(entry, title=title.strip(), title_review=None)
        selected[id_] = replace(entry, level=level)
    return [selected[id_] for id_ in sorted(selected)]


def report_candidates(entries):
    return [{"id": index, "title": entry.title, "level": entry.level,
             "page": entry.page + 1 if entry.page is not None else None,
             **({"printedPage": entry.folio} if entry.folio else {}),
             **({"group": entry.group} if entry.group is not None and (entry.page is None or not entry.suggested) else {}),
             **({"suggested": False} if not entry.suggested or entry.page is None else {}),
             **({"titleReview": list(entry.title_review)} if entry.title_review is not None else {})}
            for index, entry in enumerate(entries, 1)]


def generate(args):
    output = Path(args.output).expanduser() if args.output else None
    path = str(Path(args.input).expanduser().resolve(strict=True))
    if output is not None and (output.exists() or output.is_symlink()):
        raise ValueError(f"Output already exists: {output}")
    with pikepdf.Pdf.open(path, attempt_recovery=False) as pdf:
        has_outline = "/First" in pdf.Root.get("/Outlines", {})
        if has_outline and not (args.refresh or args.replace_existing):
            return {"status": "existing", "count": 0}
        if pdf.is_encrypted or signed(pdf):
            return {"status": "protected", "count": 0}
        if args.refresh and not has_outline:
            return {"status": "no_outline", "count": 0}
        count = len(pdf.pages)
        front_end = min(count, FRONT_PAGES)
        pages = read_layout(path, 0, front_end - 1, args.pdftohtml)
        resolver = Destinations(pdf)
        can_ocr = not args.no_ocr
        rows = read_contents(pages, pdf, resolver)
        can_probe = can_ocr and not args.reuse_text
        if not rows and can_probe:
            for index in range(min(count, OCR_FRONT_PAGES)):
                if enough_text(pages[index]) or not has_page_image(pdf.pages[index]):
                    continue
                pages[index] = ocr_page(path, index, args)
                rows = read_contents(pages, pdf, resolver)
                if rows and index > max(page for _, page in rows):
                    break
        if rows:
            if can_ocr:
                recover_missing_contents_rows(rows, pages, pdf, path, args)
                rows = read_contents(pages, pdf, resolver)
            method = "contents"
            entries = resolve_contents(rows, pages, pdf)
            if any(entry.page is None or entry.repair_title for entry in entries):
                pages.update(read_layout(path, front_end, count - 1, args.pdftohtml))
                entries = resolve_contents(rows, pages, pdf)
                after_contents = max(page for _, page in rows) + 1
                if (can_probe and any(entry.page is None for entry in entries)
                        and not any(enough_text(pages[index]) for index in range(after_contents, min(count, after_contents + 6)))):
                    probe_chapters(rows, pages, path, args)
                entries = resolve_contents(rows, pages, pdf, alternatives=True)
            if can_ocr:
                review_math_titles(entries, rows, pages, pdf, path, args)
        else:
            pages.update(read_layout(path, front_end, count - 1, args.pdftohtml))
            method = "tags"
            entries = tagged_headings(path, pages, args.pdfinfo) if "/StructTreeRoot" in pdf.Root else []
            if not entries:
                method = "headings"
                entries = layout_headings(pages)
        choices = json.loads(Path(args.choices).read_text(encoding="utf-8")) if args.choices else None
        if args.refresh:
            return refresh_outline(pdf, resolver, output, entries, choices, args.candidates)
        selected = select_entries(entries, choices)
        unresolved = sum(entry.page is None for entry in entries)
        needs_review = choices is None and unresolved > 0
        status = "preview" if output is None else "needs_review" if needs_review else "insufficient"
        result = {"status": status, "method": method, "count": len(selected), "unresolved": unresolved}
        review_titles = [entry.title for entry in selected if entry.title_review is not None]
        if review_titles:
            result["titleWarnings"] = review_titles
        if output is None or args.candidates:
            result["candidates"] = report_candidates(entries)
        if output is not None and selected and not needs_review:
            output_entries(pdf, output, selected, replace_existing=has_outline)
            result["status"] = "replaced" if has_outline else "added"
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output", help="New PDF path. Omit for a candidate preview.")
    existing_outline = parser.add_mutually_exclusive_group()
    existing_outline.add_argument("--refresh", action="store_true", help="Update uniquely matched titles in an existing outline.")
    existing_outline.add_argument("--replace-existing", action="store_true", help="Replace the entire existing outline.")
    parser.add_argument("--choices", help="JSON file containing reviewed candidate IDs and levels.")
    parser.add_argument("--candidates", action="store_true", help="Include candidates even when writing a PDF.")
    parser.add_argument("--language", default="eng")
    parser.add_argument("--no-ocr", action="store_true", help="Use only the existing PDF text layer.")
    parser.add_argument("--reuse-text", action="store_true", help="OCR only to repair visibly corrupted contents.")
    for tool in ("pdftohtml", "pdfinfo", "pdftoppm", "tesseract"):
        parser.add_argument("--" + tool, default=tool)
    args = parser.parse_args()
    try:
        print(json.dumps(generate(args), ensure_ascii=True))
    except pikepdf.PasswordError:
        print(json.dumps({"status": "protected", "count": 0}))
    except (OSError, ValueError, pikepdf.PdfError, ET.ParseError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"PDF bookmarks: {error}\n")


if __name__ == "__main__":
    main()
