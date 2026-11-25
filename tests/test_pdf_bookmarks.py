"""Exercise the standalone helper against real PDFs."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pikepdf
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

HELPER = Path(__file__).resolve().parents[1] / "src" / "pdf_bookmarks.py"
sys.path.insert(0, str(HELPER.parent))
import pdf_bookmarks as bookmarks


class BookmarksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source.pdf"
        self.output = Path(self.tmp.name) / "bookmarked.pdf"

    def run_helper(self, *extra, ocr=False, source=None):
        args = [sys.executable, str(HELPER), str(source or self.source), *map(str, extra)]
        if not ocr:
            args.append("--no-ocr")
        run = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def outline(self):
        with pikepdf.Pdf.open(self.output) as pdf:
            pages = {page.obj.objgen: n for n, page in enumerate(pdf.pages, 1)}

            def walk(nodes, depth=1):
                for node in nodes:
                    yield (node.title, pages[node.destination[0].objgen], depth, str(node.destination[1]))
                    yield from walk(node.children, depth + 1)

            with pdf.open_outline() as outline:
                return list(walk(outline.root))

    def make_book(self, with_links=False, ambiguous=False):
        c = canvas.Canvas(str(self.source))
        c.setFont("Helvetica-Bold", 21)
        c.drawString(55, 748, "Table of Contents")
        entries = [("1. The River", "1", "river"), ("1.1. Tributaries", "2", "tributaries"),
                   ("2. The Coast", "4", "coast")]
        if ambiguous:
            entries[1] = ("1.1. Tributaries", "3", "tributaries")
        for n, (title, label, dest) in enumerate(entries):
            y = 680 - n * 32
            x = 78 if n == 1 else 55
            c.setFont("Helvetica", 12)
            c.drawString(x, y, title)
            c.drawRightString(500, y, label)
            if with_links:
                c.linkRect("", dest, (x, y - 5, 500, y + 13), relative=0)
        c.showPage()
        for title, dest in [("Chapter 1. The River", "river"), ("Tributaries", "tributaries"),
                            ("Tributaries" if ambiguous else "Blank leaves", "unused"), ("Chapter 2. The Coast", "coast")]:
            c.bookmarkHorizontalAbsolute(dest, 710)
            c.setFont("Helvetica-Bold", 18)
            c.drawString(55, 695, title)
            c.showPage()
        c.save()

    def test_linked_contents_keep_exact_destinations_and_nesting(self):
        self.make_book(with_links=True)
        result = self.run_helper("--output", self.output)
        self.assertEqual(result, {"status": "added", "method": "contents", "count": 3, "unresolved": 0})
        self.assertEqual(self.outline(), [
            ("1. The River", 2, 1, "/XYZ"),
            ("1.1. Tributaries", 3, 2, "/XYZ"),
            ("2. The Coast", 5, 1, "/XYZ")
        ])
        self.assertEqual(self.run_helper(source=self.output)["status"], "existing")
        for n in (1, 3, 5):
            def render(path):
                return subprocess.check_output(["pdftoppm", "-q", "-scale-to", "440", "-f", str(n),
                                                "-l", str(n), "-singlefile", str(path)])
            self.assertEqual(render(self.source), render(self.output))

    def test_explicit_replacement_discards_the_old_outline(self):
        self.make_book(with_links=True)
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            with pdf.open_outline() as outline:
                old = pikepdf.OutlineItem("Old chapter", 1)
                old.children.append(pikepdf.OutlineItem("Old section", 2))
                outline.root.append(old)
            pdf.save(self.source)
        original = self.source.read_bytes()
        self.assertEqual(self.run_helper("--output", self.output)["status"], "existing")
        self.assertFalse(self.output.exists())
        result = self.run_helper("--replace-existing", "--output", self.output)
        self.assertEqual((result["status"], result["count"]), ("replaced", 3))
        self.assertEqual([item[:3] for item in self.outline()], [
            ("1. The River", 2, 1), ("1.1. Tributaries", 3, 2), ("2. The Coast", 5, 1)
        ])
        self.assertEqual(self.source.read_bytes(), original)

    def test_replacement_with_no_reliable_bookmarks_leaves_the_original(self):
        c = canvas.Canvas(str(self.source))
        c.drawString(70, 600, "Ordinary text.")
        c.showPage()
        c.save()
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            with pdf.open_outline() as outline:
                outline.root.append(pikepdf.OutlineItem("Original bookmark", 0))
            pdf.save(self.source)
        before = self.source.read_bytes()
        self.assertEqual(self.run_helper("--replace-existing", "--output", self.output)["status"], "insufficient")
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_refresh_updates_titles_without_rebuilding_the_outline(self):
        self.make_book(with_links=True)
        first = Path(self.tmp.name) / "existing.pdf"
        self.assertEqual(self.run_helper("--output", first)["status"], "added")
        with pikepdf.Pdf.open(first, allow_overwriting_input=True) as pdf:
            with pdf.open_outline() as outline:
                root = outline.root[0]
                root.title = "THE RIVER"
                root.is_closed = True
                root.color = (0.2, 0.4, 0.6)
                root.children.append(pikepdf.OutlineItem("Personal bookmark", 2))
                outline.root.append(pikepdf.OutlineItem(
                    "External resource", action=pikepdf.Dictionary(S=pikepdf.Name.URI, URI="https://example.org")
                ))
            pdf.save(first)
        before = first.read_bytes()
        preview = self.run_helper("--refresh", source=first)
        self.assertEqual(preview["changes"],
                         [{"id": 1, "page": 2, "oldTitle": "THE RIVER", "title": "1. The River"}])
        self.assertEqual(first.read_bytes(), before)
        self.assertEqual(self.run_helper("--refresh", "--output", self.output, source=first)["status"], "refreshed")
        with pikepdf.Pdf.open(first) as original, pikepdf.Pdf.open(self.output) as updated:
            old = original.open_outline().root
            new = updated.open_outline().root
            self.assertEqual([item.title for item in new], ["1. The River", "2. The Coast", "External resource"])
            self.assertEqual([item.title for item in new[0].children], ["1.1. Tributaries", "Personal bookmark"])
            self.assertEqual(list(new[0].destination)[1:], list(old[0].destination)[1:])
            self.assertEqual(new[0].is_closed, old[0].is_closed)
            self.assertEqual(new[0].color, old[0].color)
            self.assertEqual(str(new[-1].action.URI), "https://example.org")
        self.assertEqual(self.run_helper("--refresh", source=self.output)["count"], 0)
        self.assertEqual(self.run_helper("--refresh")["status"], "no_outline")

    def test_refresh_can_apply_a_reviewed_title_and_skips_ambiguous_matches(self):
        self.make_book(with_links=True)
        first = Path(self.tmp.name) / "existing.pdf"
        self.assertEqual(self.run_helper("--output", first)["status"], "added")
        with pikepdf.Pdf.open(first, allow_overwriting_input=True) as pdf:
            with pdf.open_outline() as outline:
                outline.root[0].title = "THE RIVER"
                outline.root.insert(1, pikepdf.OutlineItem("The River", 1))
            pdf.save(first)
        choices = Path(self.tmp.name) / "choices.json"
        choices.write_text('[{"id":1,"level":1,"title":"Chapter 1 The River"},'
                           '{"id":2,"level":2,"title":"1.1 Tributaries (reviewed)"}]')
        result = self.run_helper("--refresh", "--choices", choices, "--output", self.output, source=first)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.outline()[0][0], "THE RIVER")
        self.assertEqual(self.outline()[1][0], "1.1 Tributaries (reviewed)")
        self.assertEqual(self.outline()[2][0], "The River")

    def test_broken_folio_links_anchor_split_and_wrapped_contents_rows(self):
        c = canvas.Canvas(str(self.source), pagesize=(432, 648))
        c.setFont("Helvetica-Bold", 20)
        c.drawString(150, 570, "Contents")
        c.setFont("Helvetica", 10)

        def linked_folio(y, number):
            c.drawString(349, y, number[0])
            c.drawString(354.55, y, number[1])
            c.linkRect("", "body", (348, y - 3, 361, y + 10), relative=0)

        c.drawString(49, 500, "1")
        c.drawString(73, 500, "The coast")
        linked_folio(500, "25")
        c.drawString(49, 480, "1.1")
        c.drawString(73, 480, "The geometry of a long")
        c.drawString(73, 467, "shoreline beside")
        c.drawString(149, 467, "GL")
        c.drawString(162, 467, "(n)")
        linked_folio(467, "26")
        c.drawString(49, 445, "1.2")
        c.drawString(73, 445, "The headlands")
        linked_folio(445, "28")
        c.showPage()
        c.bookmarkPage("body")
        for number in range(25, 29):
            c.setFont("Helvetica", 10)
            c.drawRightString(360, 609, str(number))
            c.drawString(66, 520, "The body has no searchable chapter titles here.")
            c.showPage()
        c.save()
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            for link in pdf.pages[0].obj.Annots:
                link.Dest[0] = None
            pdf.save(self.source)
        result = self.run_helper("--output", self.output)
        self.assertEqual((result["status"], result["count"], result["unresolved"]), ("added", 3, 0))
        self.assertEqual([item[:3] for item in self.outline()], [
            ("1 The coast", 2, 1),
            ("1.1 The geometry of a long shoreline beside GL(n)", 3, 2),
            ("1.2 The headlands", 5, 2),
        ])

    def test_unlinked_volume_contents_keeps_wrapped_entries_and_skips_the_other_volume(self):
        c = canvas.Canvas(str(self.source), pagesize=(432, 648))
        c.setFont("Helvetica-Bold", 18)
        c.drawString(130, 570, "Contents for Volume I")
        c.setFont("Helvetica", 11)
        c.drawString(75, 500, "Contents for Volume II")
        c.drawRightString(360, 500, "ii")
        c.drawString(50, 460, "1")
        c.drawString(75, 460, "The coast")
        c.drawRightString(360, 460, "25")
        c.drawString(75, 437, "1.1")
        c.drawString(105, 437, "The geometry of a long")
        c.drawString(105, 424, "shoreline")
        c.drawRightString(360, 424, "26")
        c.drawString(75, 402, "1.2")
        c.drawString(105, 402, "The headlands")
        c.drawRightString(360, 402, "28")
        c.drawRightString(360, 55, "i")
        c.showPage()

        c.setFont("Helvetica-Bold", 18)
        c.drawString(130, 570, "Contents for Volume II")
        c.setFont("Helvetica", 11)
        for index in range(3):
            y = 500 - index * 23
            c.drawString(75, y, f"{index + 2} Another volume")
            c.drawRightString(360, y, str(index + 25))
        c.showPage()

        for number in range(25, 29):
            c.setFont("Helvetica", 10)
            c.drawRightString(360, 609, str(number))
            c.drawString(66, 520, "The body has no searchable chapter titles here.")
            c.showPage()
        c.save()

        result = self.run_helper("--output", self.output)
        self.assertEqual((result["status"], result["count"], result["unresolved"]), ("added", 4, 0))
        self.assertEqual([item[:3] for item in self.outline()], [
            ("Contents for Volume II", 2, 1),
            ("1 The coast", 3, 1),
            ("1.1 The geometry of a long shoreline", 4, 2),
            ("1.2 The headlands", 6, 2),
        ])

    def test_contents_keeps_missing_folios_and_corrects_stale_front_matter_numbers(self):
        c = canvas.Canvas(str(self.source), pagesize=(432, 648))
        c.setFont("Helvetica-Bold", 18)
        c.drawString(150, 570, "Contents")
        c.setFont("Helvetica", 10)
        entries = [
            ("Preface", "iv"), ("Advice to the Reader", "v"),
            ("1 The Coast", None), ("1.1 Basin", None), ("1.2 The Headlands", "3"),
            ("13 Forecasting", "4"), ("1.4 The Harbor", "5"), ("Index", "6")
        ]
        for index, (title, folio) in enumerate(entries):
            y = 520 - index * 13
            c.drawString(50, y, title)
            if folio:
                c.drawRightString(354 if title == "Index" else 360, y, folio)
        c.showPage()

        body = [("ii", "Preface"), ("iii", "Notes"), ("iv", "Advice to the Reader"),
                ("v", "Other notes"), ("1", "The Coast"), ("2", "1.1 Basin"),
                ("3", "Body"), ("4", "Body"), ("5", "Body"), ("6", "Index")]
        for folio, title in body:
            c.setFont("Helvetica", 10)
            c.drawRightString(360, 609, folio)
            c.setFont("Helvetica-Bold", 18)
            c.drawString(55, 520, title)
            c.showPage()
        c.save()

        result = self.run_helper("--output", self.output)
        self.assertEqual((result["status"], result["count"], result["unresolved"]), ("added", 8, 0))
        self.assertEqual([item[:3] for item in self.outline()], [
            ("Preface", 2, 1), ("Advice to the Reader", 4, 1),
            ("1 The Coast", 6, 1), ("1.1 Basin", 7, 2), ("1.2 The Headlands", 8, 2),
            ("1.3 Forecasting", 9, 2), ("1.4 The Harbor", 10, 2), ("Index", 11, 1),
        ])

    def test_unlinked_printed_contents_uses_chapter_starts(self):
        self.make_book()
        result = self.run_helper("--output", self.output)
        self.assertEqual(result["count"], 3)
        self.assertEqual([entry[:3] for entry in self.outline()], [
            ("1. The River", 2, 1), ("1.1. Tributaries", 3, 2), ("2. The Coast", 5, 1)
        ])

    def test_ambiguous_contents_can_be_reviewed_without_writing_first(self):
        self.make_book(ambiguous=True)
        preview = self.run_helper()
        # The other two verified entries give a consistent offset for folio 3.
        self.assertEqual([entry["page"] for entry in preview["candidates"]], [2, 4, 5])
        # Give the final heading a different offset. The duplicated heading now needs review.
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            pdf.pages.insert(4, pdf.pages[0])
            pdf.pages[4].obj.Contents = pdf.make_stream(b"")
            pdf.save(self.source)
        self.assertEqual(self.run_helper("--output", self.output)["status"], "needs_review")
        self.assertFalse(self.output.exists())
        candidates = self.run_helper()["candidates"]
        self.assertEqual([entry["page"] for entry in candidates if entry["title"] == "1.1. Tributaries"], [None, 3, 4])
        choices = Path(self.tmp.name) / "choices.json"
        choices.write_text(json.dumps([{"id": row["id"], "level": row["level"]}
                                       for row in candidates if row["page"] in {2, 4, 6}]))
        self.assertEqual(self.run_helper("--choices", choices, "--output", self.output)["status"], "added")
        self.assertEqual([entry[1] for entry in self.outline()], [2, 4, 6])

    def test_ocr_leaders_and_bad_folio_use_book_headings_and_page_labels(self):
        c = canvas.Canvas(str(self.source))
        c.setFont("Helvetica-Bold", 21)
        c.drawString(55, 748, "Contents")
        c.setFont("Helvetica", 12)
        c.drawString(55, 695, "PREFACE ooo.ccccccceeecccesssceces vii")
        c.drawString(55, 670, "INDEX OF NOTATION.")
        c.drawString(240, 670, ".........ccccccceeeccccccseeseeee viii")
        c.drawString(55, 635, "1")
        c.drawString(83, 635, "THE RIVER")
        c.drawString(84, 612, "1.1 River Basin..........cccccsesssccceeeeees 1")
        c.drawString(84, 589, "1.2 Tributarles.........ccccccccccessseeessss 2")
        c.drawString(84, 566, "EXEYCISES...............cccccccceeeceeesesss 1")
        c.drawString(83, 534, "THE COAST")
        c.drawString(84, 511, "2.1 Coastal Systems.........cccccccceeeeeeees 4")
        c.showPage()
        c.setFont("Helvetica-Bold", 17)
        c.drawString(55, 730, "Preface")
        c.showPage()
        c.setFont("Helvetica-Bold", 17)
        c.drawString(55, 730, "Index of Notation")
        c.setFont("Helvetica", 11)
        for index, text in enumerate(("compositum of fields K and L", "nxn matrices of determinant 1",
                                       "The size of the orbit is c", "norm of a bounded operator 7")):
            c.drawString(105, 660 - 17 * index, text)
        c.showPage()
        for page, headings in enumerate((("The River", "1.1 River Basin"), ("1.2 Tributaries",),
                                         ("Exercises",), ("The Coast", "2.1 Coastal Systems")), 1):
            c.setFont("Helvetica", 9)
            c.drawString(50, 788, "The River" if page < 4 else "The Coast")
            for index, title in enumerate(headings):
                c.setFont("Helvetica-Bold", 17)
                c.drawString(55, 675 - 45 * index, title)
            c.showPage()
        c.save()
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            pdf.Root.PageLabels = pikepdf.Dictionary(Nums=pikepdf.Array([
                0, pikepdf.Dictionary(S=pikepdf.Name.r, St=6), 3, pikepdf.Dictionary(S=pikepdf.Name.D)
            ]))
            pdf.save(self.source)
        report = self.run_helper("--output", self.output)
        self.assertEqual((report["status"], report["count"], report["unresolved"]), ("added", 8, 0))
        self.assertEqual([entry[:3] for entry in self.outline()], [
            ("Preface", 2, 1), ("Index of Notation", 3, 1), ("1 The River", 4, 1),
            ("1.1 River Basin", 4, 2), ("1.2 Tributaries", 5, 2), ("Exercises", 6, 2),
            ("The Coast", 7, 1), ("2.1 Coastal Systems", 7, 2)
        ])

    def test_chapter_numbers_need_a_numbered_run_and_consistent_children(self):
        def row(title, level):
            return bookmarks.Entry(title, level, None), 0

        rows = [row("1 The River", 1), row("1.1 Basin", 2), row("THE OCEAN", 1),
                row("2.1 Coast", 2), row("2.2 Tides", 2), row("3 The Lake", 1),
                row("3.1 Fish", 2), row("THE BAY", 1), row("4.1 Estuary", 2),
                row("5.2 Inlet", 2), row("5 The Sound", 1)]
        updated = bookmarks.restore_chapter_numbers(rows, 1)
        self.assertEqual([entry.title for entry, _ in updated if entry.level == 1],
                         ["1 The River", "2 THE OCEAN", "3 The Lake", "THE BAY", "5 The Sound"])

        entry = bookmarks.Entry("THE SOUND", 1, None)
        hit = bookmarks.Line(8, "5 The Sound", .1, .2, .3, 16)
        self.assertEqual(bookmarks.resolved_book_entry(entry, 8, {8: hit}, {}).title, "5 The Sound")

    def test_math_crop_disagreement_can_be_reviewed_before_embedding(self):
        c = canvas.Canvas(str(self.source))
        c.setFont("Helvetica", 14)
        c.drawString(55, 720, "1.1 The Geometry of Ay/K")
        image = Image.new("RGB", (600, 700), "white")
        image.putpixel((0, 0), (0, 0, 0))
        image_path = Path(self.tmp.name) / "scan.png"
        image.save(image_path)
        c.drawImage(str(image_path), 10, 10, width=20, height=20)
        c.showPage()
        c.save()
        heading = bookmarks.read_layout(str(self.source), 0, 0, "pdftohtml")[0][0]
        entry = bookmarks.Entry(heading.text, 2, 0, heading=heading)
        args = SimpleNamespace(pdftohtml="pdftohtml")

        with pikepdf.Pdf.open(self.source) as pdf:
            candidates = [entry]
            with patch.object(bookmarks, "heading_crop", side_effect=[
                "1.1 The Geometry of A;/K", "1.1 The Geometry of Ay/K"
            ]) as crop:
                bookmarks.review_math_titles(candidates, [(entry, 0)], {0: [heading]}, pdf, str(self.source), args)
            self.assertEqual(crop.call_count, 2)
            self.assertEqual(candidates[0].title, entry.title)
            self.assertEqual(bookmarks.report_candidates(candidates)[0]["titleReview"],
                             ["1.1 The Geometry of A;/K"])

            reviewed = bookmarks.select_entries(candidates, [{"id": 1, "level": 1,
                                                               "title": "1.1 The Geometry of Aₖ/K"}])
            self.assertIsNone(reviewed[0].title_review)
            bookmarks.output_entries(pdf, self.output, reviewed)
        self.assertEqual(self.outline()[0][:3], ("1.1 The Geometry of Aₖ/K", 1, 1))

    def test_paper_headings_exclude_title_captions_and_running_headers(self):
        c = canvas.Canvas(str(self.source))
        c.setFont("Helvetica-Bold", 24)
        c.drawString(60, 775, "A study of rivers")
        for headings in (("1. Introduction", "1.1. Setting"), ("2. Results", "References")):
            c.setFont("Helvetica-Bold", 10)
            c.drawString(60, 742, "The Journal of Rivers")
            for y, heading in zip((640, 470), headings):
                c.setFont("Helvetica-Bold", 14 if "1.1." not in heading else 11)
                c.drawString(60, y, heading)
                c.setFont("Helvetica", 11)
                c.drawString(60, y - 24, "This body text is long enough to establish the ordinary text size.")
                c.drawString(60, y - 38, "The paper describes what the researchers found in several rivers.")
            c.setFont("Helvetica-Bold", 11)
            c.drawString(60, 275, "Figure 1. Water level relative to the bank")
            c.showPage()
        c.save()
        self.assertEqual(self.run_helper("--output", self.output)["count"], 4)
        self.assertEqual([entry[:3] for entry in self.outline()], [
            ("1. Introduction", 1, 1), ("1.1. Setting", 1, 2), ("2. Results", 2, 1), ("References", 2, 1)
        ])

    def test_paper_citation_links_and_numbered_body_do_not_make_outline_entries(self):
        c = canvas.Canvas(str(self.source))
        for page, heading in enumerate(("Introduction", "Conclusion")):
            if page:
                c.bookmarkPage("references")
            c.setFont("Helvetica-Bold", 14)
            c.drawString(55, 650, heading)
            c.setFont("Helvetica", 11)
            for index, text in enumerate((
                "A paragraph ends with a citation and its number",
                "This other paragraph ends with a different citation",
                "One more sentence ends at another linked reference",
                "0.7 is the coefficient reported in the last specification",
                "1. A numbered list item begins with a plain body font",
                "2. The next list item has the same body font"
            )):
                y = 590 - 23 * index
                c.drawString(55, y, text)
                if not page and index < 3:
                    c.drawRightString(505, y, str(index + 1))
                    c.linkRect("", "references", (55, y - 4, 510, y + 11), relative=0)
            c.showPage()
        c.save()
        result = self.run_helper("--output", self.output)
        self.assertEqual((result["method"], result["count"]), ("headings", 2))
        self.assertEqual([entry[:3] for entry in self.outline()], [
            ("Introduction", 1, 1), ("Conclusion", 2, 1)
        ])

    def test_paper_body_size_sections_split_titles_and_run_in_appendix(self):
        c = canvas.Canvas(str(self.source), pagesize=(600, 800))

        def paragraph(y):
            c.setFont("Times-Roman", 10)
            c.drawString(100, y, "The researchers compiled a dataset from the observations made in the field.")
            c.drawString(100, y - 12, "They documented how each of the variables was collected and measured.")

        c.setFont("Times-Roman", 10)
        c.drawCentredString(300, 700, "1. STUDY SETTING")
        paragraph(684)
        c.drawString(100, 640, "1.1.")
        c.setFont("Times-Italic", 10)
        c.drawString(125, 640, "Data sources")
        paragraph(624)
        c.setFont("Times-Bold", 10)
        c.drawString(119, 575, "1.1.1.")
        c.drawString(151, 575, "Survey design.")
        c.setFont("Times-Roman", 10)
        c.drawString(216, 575, "The sample includes three regions.")
        paragraph(563)
        c.drawString(100, 514, "1.")
        c.setFont("Times-Bold", 10)
        c.drawString(119, 514, "First item.")
        c.setFont("Times-Roman", 10)
        c.drawString(167, 514, "This numbered list is part of the body.")
        c.drawCentredString(300, 466, "2. MODEL SPECIFICATION")
        paragraph(450)
        c.showPage()

        c.setFont("Times-Roman", 10)
        c.drawCentredString(300, 700, "APPENDIX")
        c.drawString(100, 680, "A.")
        c.setFont("Times-Italic", 10)
        c.drawString(120, 680, "Additional data")
        c.setFont("Times-Bold", 8)
        c.drawString(119, 660, "A.1.")
        c.drawString(141, 660, "Selection rule.")
        c.setFont("Times-Roman", 8)
        c.drawString(194, 660, "Only surveys with complete responses are included.")
        paragraph(648)
        c.setFont("Times-Bold", 8)
        c.drawString(100, 596, "Supplementary Data")
        paragraph(580)
        c.save()

        result = self.run_helper("--output", self.output)
        self.assertEqual((result["method"], result["count"]), ("headings", 8))
        self.assertEqual([entry[:3] for entry in self.outline()], [
            ("1. STUDY SETTING", 1, 1), ("1.1. Data sources", 1, 2),
            ("1.1.1. Survey design.", 1, 3), ("2. MODEL SPECIFICATION", 1, 1),
            ("APPENDIX", 2, 1), ("A. Additional data", 2, 2),
            ("A.1. Selection rule.", 2, 3), ("Supplementary Data", 2, 1)
        ])

    def test_tagged_heading_uses_structure_at_body_font_size(self):
        c = canvas.Canvas(str(self.source))
        c.setFont("Helvetica", 11)
        c.addLiteral("/H1 <</MCID 0>> BDC")
        c.drawString(50, 700, "Rivers and oceans")
        c.addLiteral("EMC")
        c.drawString(50, 660, "Body text here.")
        c.save()
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            page = pdf.pages[0]
            page.obj.StructParents = 0
            root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
            root.K = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.H1,
                                                         P=root, Pg=page.obj, K=0))
            root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([root.K])])))
            pdf.Root.StructTreeRoot = root
            pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
            pdf.save(self.source)
        self.assertEqual(self.run_helper("--output", self.output)["method"], "tags")
        self.assertEqual(self.outline(), [("Rivers and oceans", 1, 1, "/FitH")])

    def test_front_matter_and_numbering_resets_follow_pdf_labels(self):
        c = canvas.Canvas(str(self.source))
        c.setFont("Helvetica-Bold", 22)
        c.drawString(52, 748, "Contents")
        c.setFont("Helvetica", 13)
        for index, text in enumerate(("Preface .......... ii", "Part I .......... 1", "Part II .......... 1")):
            c.drawString(52, 690 - index * 30, text)
        c.showPage()
        for heading in ("Preface", "Part I", "Part II"):
            c.setFont("Helvetica", 12)
            c.drawString(52, 710, heading)
            c.showPage()
        c.save()
        with pikepdf.Pdf.open(self.source, allow_overwriting_input=True) as pdf:
            pdf.Root.PageLabels = pikepdf.Dictionary(Nums=pikepdf.Array([
                0, pikepdf.Dictionary(S=pikepdf.Name.r),
                2, pikepdf.Dictionary(S=pikepdf.Name.D), 3, pikepdf.Dictionary(S=pikepdf.Name.D),
            ]))
            pdf.save(self.source)
        self.assertEqual(self.run_helper("--output", self.output)["count"], 3)
        self.assertEqual([entry[1] for entry in self.outline()], [2, 3, 4])

    def test_scan_uses_page_images_without_adding_a_text_layer(self):
        c = canvas.Canvas(str(self.source))
        font = ImageFont.load_default(size=41)
        pages = (["Contents", "", "The River ................ 1", "The Coast ................ 2"],
                 ["The River"], ["The Coast"])
        for index, lines in enumerate(pages):
            image = Path(self.tmp.name) / f"page-{index}.png"
            page = Image.new("L", (1000, 1400), "white")
            pen = ImageDraw.Draw(page)
            for i, line in enumerate(lines):
                pen.text((95, 110 + i * 85), line, fill=0, font=font)
            page.save(image)
            c.drawImage(str(image), 0, 0, width=595, height=841)
            c.showPage()
        c.save()
        self.assertEqual(self.run_helper("--output", self.output, ocr=True)["count"], 2)
        self.assertEqual([entry[1] for entry in self.outline()], [2, 3])
        text = subprocess.check_output(["pdftotext", str(self.output), "-"])
        self.assertEqual(text.strip(), b"")


if __name__ == "__main__":
    unittest.main()
