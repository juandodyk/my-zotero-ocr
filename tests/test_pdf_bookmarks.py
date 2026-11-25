"""Exercise the standalone helper against real PDFs."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

import pikepdf
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

HELPER = Path(__file__).resolve().parents[1] / "src" / "pdf_bookmarks.py"


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
