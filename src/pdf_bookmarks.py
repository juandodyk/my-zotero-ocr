#!/usr/bin/env python3
"""Generate or preview an embedded PDF outline using local PDF structure and text."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import csv
import io
import json
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
BOOK_UNIT = re.compile(r"^(part|book|chapter|appendix|volume|section)\b", re.I)
SECTION = re.compile(
    r"^(abstract|introduction|background|literature review|related work|theory|"
    r"research design|(?:materials and )?methods?|methodology|data(?: and methods)?|"
    r"results|findings|analysis|discussion|results and discussion|conclusions?|"
    r"discussion and conclusions?|limitations|future work|acknowledg[em]+nts?|"
    r"references|bibliography|appendix(?:\s+[\w]+)?|supplementary materials?|"
    r"preface|foreword|index)$", re.I
)
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
                              item.find(".//b") is not None))
        pages[page_index] = lines
    return pages


def ocr_page(path, index, args):
    page = str(index + 1)
    image = spawn([args.pdftoppm, "-cropbox", "-gray", "-scale-to", "1550", "-f", page,
                   "-l", page, "-singlefile", path])
    tsv = spawn([args.tesseract, "stdin", "stdout", "-l", args.language, "--psm", "6", "tsv"], image)
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
        target, destination = resolver.resolve(dest)
        rect = annotation.get("/Rect", [])
        if target is not None and len(rect) == 4:
            left, top = transform(float(rect[0]), float(rect[1]))
            right, bottom = transform(float(rect[2]), float(rect[3]))
            result.append((min(left, right), max(left, right), min(top, bottom), max(top, bottom), target, destination))
    return result


def contents_rows(lines, links, explicit):
    # Poppler sometimes emits a right-aligned folio as its own text item.
    # Pair it with the closest title to its left on the same row.
    merged = {id(line): line for line in lines}
    for folio in lines:
        if not read_folio(folio.text) or not .05 < folio.y < .91:
            continue
        left = [line for line in merged.values() if abs(line.y - folio.y) < .011
                and 0 <= folio.x - line.x - line.width < .65 and not read_folio(line.text)
                and not FOLIO.fullmatch(line.text)]
        if left:
            title = max(left, key=lambda line: line.x + line.width)
            merged.pop(id(folio), None)
            merged[id(title)] = replace(title, text=title.text + " … " + folio.text,
                                        width=folio.x + folio.width - title.x)
    rows = []
    for line in sorted(merged.values(), key=lambda line: (round(line.y, 3), line.x)):
        if CONTENTS.fullmatch(line.text.strip(":")) or line.y < 0.035 or line.y > 0.96:
            continue
        match = FOLIO.fullmatch(line.text)
        title, folio = (match[1].rstrip(".·… "), match[3].lower()) if match and read_folio(match[3]) else (line.text, None)
        leaders = match and not match[2].isspace()
        midpoint = line.y + 0.006
        linked = [(target, dest) for left, right, top, bottom, target, dest in links
                  if top - .012 <= midpoint <= bottom + .008 and left < line.x + line.width
                  and right >= line.x - .008 and target != line.page]
        if len({target for target, _ in linked}) == 1:
            target, dest = linked[0]
        else:
            target, dest = None, None
        if not (target is not None or (folio and (explicit or leaders))):
            continue
        if len(title) < 2 or len(title) > 220 or not any(char.isalpha() for char in title):
            continue
        rows.append((line, title, folio, target, dest))
    return rows


def read_contents(pages, pdf, resolver):
    collected = []
    started = False
    for index, lines in sorted(pages.items()):
        explicit = any(CONTENTS.fullmatch(line.text.strip(":")) for line in lines)
        if not (explicit or started) and not lines:
            continue
        links = link_rows(pdf.pages[index], resolver) if explicit or started or len(lines) < 70 else []
        rows = contents_rows(lines, links, explicit or started)
        if not (explicit or started) and sum(folio is not None for _, _, folio, _, _ in rows) < 3:
            continue
        minimum = 1 if explicit or started and any(target is not None for _, _, _, target, _ in rows) else 2
        if len(rows) >= (minimum if explicit or started else 3):
            collected.extend(rows)
            started = True
        elif started:
            break
        elif explicit:
            started = True
    if len(collected) < 2:
        return []
    lefts = sorted({round(line.x, 2) for line, *_ in collected})
    # A wide column break is not an indentation level.
    column_break = next((right - .01 for left, right in zip(lefts, lefts[1:]) if right - left > .27), 1)
    if column_break < 1:
        collected.sort(key=lambda row: (row[0].page, row[0].x >= column_break, row[0].y, row[0].x))
    margins = {
        side: sorted({round(line.x, 2) for line, *_ in collected if (line.x >= column_break) == side})
        for side in {line.x >= column_break for line, *_ in collected}
    }
    parts = any(re.match(r"^(part|book|volume)\b", title, re.I) for _, title, *_ in collected)
    entries = []
    for line, title, folio, target, dest in collected:
        number = NUMBERED.match(title)
        base = 2 if parts else 1
        if re.match(r"^(part|book|volume)\b", title, re.I):
            level = 1
        elif number:
            level = base + number[1].count(".")
        else:
            indents = margins[line.x >= column_break]
            level = base + min(2, sum(round(line.x, 2) - left >= .023 for left in indents))
            if BOOK_UNIT.match(title) or SECTION.fullmatch(title):
                level = base
        entries.append(Entry(title, level, target, folio=folio, destination=dest))
    return [(entry, row[0].page) for entry, row in zip(entries, collected)]


def title_key(text):
    number = NUMBERED.match(text)
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
                    matches[key].append(line)
    return matches


def resolve_contents(rows, pages, pdf, *, alternatives=False):
    entries = [entry for entry, _ in rows]
    excluded = {index for _, index in rows}
    matches = heading_matches(entries, pages, excluded)
    labels = defaultdict(list)
    if "/PageLabels" in pdf.Root:
        for index, page in enumerate(pdf.pages):
            labels[page.label.casefold()].append(index)

    # Use exact destinations and title matches as anchors before inferring offsets.
    resolved = []
    previous = -1
    for entry in entries:
        if entry.page is not None:
            resolved.append(entry)
            previous = entry.page
            continue
        hits = {line.page: line for line in reversed(matches.get(title_key(entry.title), []))}
        labeled = [index for index in labels.get(entry.folio, []) if index not in excluded]
        if len(hits) == 1:
            page = next(iter(hits))
        elif len(labeled) == 1:
            page = labeled[0]
        elif len(labeled) > 1:
            overlap = [index for index in labeled if index in hits and index >= previous]
            page = overlap[0] if len(overlap) == 1 else None
        else:
            page = None
        line = hits.get(page)
        resolved.append(replace(entry, page=page, y=line.y if line else None))
        if page is not None:
            previous = page

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
            if 0 <= target < len(pdf.pages) and target not in excluded:
                line = next((line for line in matches.get(title_key(entry.title), []) if line.page == target), None)
                resolved[index] = replace(entry, page=target, y=line.y if line else None)
    if not alternatives:
        return resolved
    candidates = []
    for group, entry in enumerate(resolved, 1):
        candidates.append(replace(entry, group=group))
        if entry.page is None:
            hits = {line.page: line for line in reversed(matches.get(title_key(entry.title), []))}
            for hit in sorted(hits.values(), key=lambda line: line.page)[:5]:
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
    lines = [line for group in pages.values() for line in group if .045 <= line.y <= .94]
    body_sizes = Counter()
    occurrence = defaultdict(set)
    for line in lines:
        if len(line.text) > 45:
            body_sizes[round(line.size)] += len(line.text)
        occurrence[normalized(line.text)].add(line.page)
    body = body_sizes.most_common(1)[0][0] if body_sizes else 0
    headings = []
    numbered = []
    for page, group in pages.items():
        for index, line in enumerate(group):
            if not .045 <= line.y <= .94 or not (3 <= len(line.text) <= 140):
                continue
            number = NUMBERED.match(line.text)
            title = line.text[number.end():] if number else line.text
            if (len(normalized(title)) < 3 or CAPTION.match(title) or CONTENTS.fullmatch(title)
                    or re.search(r"[@]|https?://|[;,]$", title)
                    or len(occurrence[normalized(line.text)]) > 1):
                continue
            known = SECTION.fullmatch(title) is not None
            if page == 0 and line.y < .20 and not (known or number):
                continue
            large = bool(body and line.size >= body * 1.16)
            strong = large or line.bold
            previous = group[index - 1] if index else None
            following = group[index + 1] if index + 1 < len(group) else None
            separated = (previous is None or line.y - previous.y > .025) and (
                following is None or following.y - line.y > .022)
            if number and len(title) < 105:
                numbered.append((page, line, number[1], strong or separated or known))
            elif known or BOOK_UNIT.match(title) and strong or strong and separated and len(title) <= 95:
                # A known heading with the body font is useful for OCRed papers.
                headings.append((line, known or strong, None))
    plausible_numbering = sum(strong for _, _, _, strong in numbered) >= 2
    headings.extend((line, strong, number) for _, line, number, strong in numbered if plausible_numbering or strong)
    if sum(suggested for _, suggested, _ in headings) < 2:
        headings = [(line, False, number) for line, _, number in headings]
    order = {id(line): index for page in pages.values() for index, line in enumerate(page)}
    headings.sort(key=lambda item: (item[0].page, order[id(item[0])]))
    heading_sizes = sorted({round(line.size) for line, yes, number in headings
                            if yes and not number and body and line.size >= body * 1.16}, reverse=True)
    entries = []
    for line, suggested, number in headings:
        level = number.count(".") + 1 if number else 1
        if number is None and round(line.size) in heading_sizes:
            level = min(3, 1 + heading_sizes.index(round(line.size)))
        entries.append(Entry(line.text, level, line.page, line.y, suggested=suggested))
    return entries


def geometry_top(page, fraction):
    # A rotated page can use a page-level destination. For other pages, translate
    # Poppler's media-box-relative height and keep the destination within the crop.
    if page.rotation % 360:
        return None
    bottom, top = float(page.mediabox[1]), float(page.mediabox[3])
    return max(float(page.cropbox[1]), min(float(page.cropbox[3]), top - (fraction - .01) * (top - bottom)))


def output_entries(pdf, output, selected):
    geometry = page_geometry(pdf)
    with pdf.open_outline(max_depth=32) as outline:
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

    def read_back(nodes, resolver, depth=1):
        for node in nodes:
            page, _ = resolver.resolve(node.destination)
            yield node.title, page, depth
            yield from read_back(node.children, resolver, depth + 1)

    # Save and verify in the destination directory, then publish without clobbering.
    with tempfile.TemporaryDirectory(prefix=".bookmark-", dir=output.parent) as staging:
        path = Path(staging) / "bookmarked.pdf"
        pdf.save(path, fix_metadata_version=False)
        with pikepdf.Pdf.open(path, attempt_recovery=False) as reopened:
            with reopened.open_outline(strict=True, max_depth=32) as outline:
                actual = list(read_back(outline.root, Destinations(reopened)))
            expected = []
            depth = 0
            for entry in selected:
                depth = min(entry.level, depth + 1)
                expected.append((entry.title, entry.page, depth))
            if actual != expected or page_geometry(reopened) != geometry:
                raise ValueError("The saved outline or page geometry failed verification.")
        os.link(path, output)


def select_entries(entries, choices):
    if choices is None:
        return [entry for entry in entries if entry.suggested and entry.page is not None]
    if not isinstance(choices, list) or len(choices) > len(entries):
        raise ValueError("Bookmark choices must be a list of candidate IDs and levels.")
    selected = {}
    groups = set()
    for choice in choices:
        if not isinstance(choice, dict) or set(choice) != {"id", "level"}:
            raise ValueError("Each bookmark choice requires an id and a level.")
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
        selected[id_] = replace(entry, level=level)
    return [selected[id_] for id_ in sorted(selected)]


def report_candidates(entries):
    return [{"id": index, "title": entry.title, "level": entry.level,
             "page": entry.page + 1 if entry.page is not None else None,
             **({"printedPage": entry.folio} if entry.folio else {}),
             **({"group": entry.group} if entry.group is not None and (entry.page is None or not entry.suggested) else {}),
             **({"suggested": False} if not entry.suggested or entry.page is None else {})}
            for index, entry in enumerate(entries, 1)]


def generate(args):
    output = Path(args.output).expanduser() if args.output else None
    path = str(Path(args.input).expanduser().resolve(strict=True))
    if output is not None and (output.exists() or output.is_symlink()):
        raise ValueError(f"Output already exists: {output}")
    with pikepdf.Pdf.open(path, attempt_recovery=False) as pdf:
        if "/First" in pdf.Root.get("/Outlines", {}):
            return {"status": "existing", "count": 0}
        if pdf.is_encrypted or signed(pdf):
            return {"status": "protected", "count": 0}
        count = len(pdf.pages)
        front_end = min(count, FRONT_PAGES)
        pages = read_layout(path, 0, front_end - 1, args.pdftohtml)
        resolver = Destinations(pdf)
        can_ocr = not args.no_ocr
        rows = read_contents(pages, pdf, resolver)
        if not rows and can_ocr:
            for index in range(min(count, OCR_FRONT_PAGES)):
                if enough_text(pages[index]) or not has_page_image(pdf.pages[index]):
                    continue
                pages[index] = ocr_page(path, index, args)
                rows = read_contents(pages, pdf, resolver)
                if rows and index > max(page for _, page in rows):
                    break
        if rows:
            method = "contents"
            entries = resolve_contents(rows, pages, pdf)
            if any(entry.page is None for entry in entries):
                pages.update(read_layout(path, front_end, count - 1, args.pdftohtml))
                entries = resolve_contents(rows, pages, pdf)
                after_contents = max(page for _, page in rows) + 1
                if (can_ocr and any(entry.page is None for entry in entries)
                        and not any(enough_text(pages[index]) for index in range(after_contents, min(count, after_contents + 6)))):
                    probe_chapters(rows, pages, path, args)
                entries = resolve_contents(rows, pages, pdf, alternatives=True)
        else:
            pages.update(read_layout(path, front_end, count - 1, args.pdftohtml))
            method = "tags"
            entries = tagged_headings(path, pages, args.pdfinfo) if "/StructTreeRoot" in pdf.Root else []
            if not entries:
                method = "headings"
                entries = layout_headings(pages)
        choices = json.loads(Path(args.choices).read_text(encoding="utf-8")) if args.choices else None
        selected = select_entries(entries, choices)
        unresolved = sum(entry.page is None for entry in entries)
        needs_review = choices is None and unresolved > 0
        status = "preview" if output is None else "needs_review" if needs_review else "insufficient"
        result = {"status": status, "method": method, "count": len(selected), "unresolved": unresolved}
        if output is None or args.candidates:
            result["candidates"] = report_candidates(entries)
        if output is not None and selected and not needs_review:
            output_entries(pdf, output, selected)
            result["status"] = "added"
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output", help="New PDF path. Omit for a candidate preview.")
    parser.add_argument("--choices", help="JSON file containing reviewed candidate IDs and levels.")
    parser.add_argument("--candidates", action="store_true", help="Include candidates even when writing a PDF.")
    parser.add_argument("--language", default="eng")
    parser.add_argument("--no-ocr", action="store_true", help="Use only the existing PDF text layer.")
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
