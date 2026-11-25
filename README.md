# Lossless OCR for Zotero

A small Zotero 7-10 extension that replaces stale OCR in scanned PDFs and
conservatively detects and removes common PDF watermarks while preserving the
rest of the document.

It is designed for mixed PDFs such as JSTOR downloads that contain scanned
page images, an old invisible OCR layer, and a little real visible text such as
a download footer. The command appears in Zotero's item context menu as
**Replace OCR losslessly**. A separate **Detect and remove PDF watermark...**
command handles watermarks without running OCR.

## What it does

For the first eligible PDF under each selected bibliographic item, or for each
directly selected PDF attachment, the extension:

1. Makes a byte-for-byte temporary copy; the source is not changed yet.
2. Recursively removes conventional invisible text and prior OCRmyPDF
   `/OCR-*` Form layers from that copy, then runs OCRmyPDF's `strip` mode.
3. Skips the expensive OCR stage when substantial born-digital text remains
   and no scanned-page images are present. Older pages assembled from multiple
   full-width image strips are recognized as scans.
4. Runs replacement OCR with the selected text-layer renderer on scanned,
   mixed, or ambiguous PDFs.
5. Validates PDF syntax, page count, page dimensions, rotations, extracted
   text, and file-size growth.
6. Optionally imports the unchanged source directly as a sibling backup.
7. Replaces the file behind the existing Zotero attachment and asks Zotero to
   reindex it.

Keeping the attachment item in place preserves Zotero annotations, relations,
and links to that attachment.

qpdf warnings (including stale linearization hints) are logged and do not stop
processing; qpdf errors still fail validation.

If processing or a hard validation check fails, the source is not changed and
the temporary work directory is retained. Soft text-quality warnings require
confirmation before replacement.

During processing, Zotero shows an in-window horizontal progress bar with the
current stage and batch-aware percentage. It advances after each completed OCR
page and displays the completed and total page counts. The OCR stage also names
the renderer actually selected.

Running the extension again is safe: it removes the existing removable
invisible layers before creating one fresh OCR layer. Visible text such as
download footers is preserved.

## Watermark removal

For the first eligible PDF under each selected bibliographic item, or for each
directly selected PDF attachment, **Detect and remove PDF watermark...**:

1. Scans without changing the PDF and looks for content that repeats at one
   place across pages, judged by its real text or image and final position
   rather than by how the PDF stores it. Text in margins, pale or diagonal text,
   and margin images and graphics are compared this way, including text in
   subset fonts whose codes change from page to page.
2. Also recognizes repeated page-stream text, including text in shared blocks when
   subsequent text positioning is independent, direct Form XObjects, centered
   low-opacity vector Form XObjects, repeated translucent image XObjects,
   keyword-named optional-content layers, explicit watermark artifacts, and
   PDF watermark annotations. Text inside nested Forms is inspected and
   removed per invocation. Large centered pale or translucent text can qualify
   horizontally; large diagonal text can qualify even at full opacity. Known
   watermark wording or explicit tags can qualify on a single page; other
   marks require repetition on at least two pages, with no coverage threshold. Repeated sequential line numbers in the left margin are
   offered as a separate candidate, whether they share one text block or each
   number has its own, as in Editorial Manager review PDFs. The Editorial Manager credit line is
   offered when it appears as a small footer.
3. Shows a checklist. Likely watermarks, such as download notices and
   submission-system marks, start ticked. Other repeated content, such as
   running headers, is listed unticked so nothing is removed unless you choose it.
4. Removes only the qualifying operations recorded during a verified rescan.
   If a shared Form is also used elsewhere as ordinary content, that other
   invocation is retained.
5. Writes a new PDF and checks syntax, page count, dimensions, rotations,
   extracted-text loss, file-size growth, candidate disappearance, and a
   Poppler render of every page before touching the Zotero attachment.
6. Always imports the unchanged original as a sibling backup, atomically
   replaces the file behind the existing attachment, refreshes Zotero, and
   rebuilds its full-text index.

Encrypted and digitally signed PDFs are left unchanged. Marks baked into scanned
page images, and stamps whose wording or position changes on every page, cannot
be detected this way. The menu wording deliberately says “detect” because no
safe PDF tool can identify every possible watermark automatically.

## PDF bookmarks

Right-click one or more PDF attachments or their parent items and choose
**Add PDF Bookmarks**. Bookmarks are embedded in the PDF and appear in the
reader’s **Document Outline** when you reopen it. A parent selection covers
its eligible PDF attachments. If the PDF already has an outline, Zotero asks
whether to replace it. Confirming rebuilds the whole outline. Cancelling or
failing to generate a verified replacement leaves the existing outline in
place. The attachment key and Zotero annotations are preserved. Bookmark
generation during OCR keeps an existing outline.

Books use their printed contents, internal links, page labels, and verified
chapter starts. Papers use tagged headings or text layout. On image-only books
the helper uses Tesseract on selected front-matter pages and likely chapter
starts. If existing OCR garbles the contents, it checks the body headings and
re-reads just the affected contents pages. It leaves an ambiguous book
unchanged until its candidates are reviewed. It can restore missing chapter
numbers from the chapter opening or a consistent run of numbered sections.
For scanned headings with suspicious mathematical notation, it compares small
crops of the heading and flags conflicting readings. The process is local and
makes no model or network calls.

Enable **Add PDF bookmarks when running OCR** in the extension’s Zotero settings
to generate them from the new text layer. A born-digital PDF can receive
bookmarks when OCR itself is skipped.

The same helper works on a standalone PDF. Use Python with `pikepdf` installed
(such as OCRmyPDF’s own interpreter) and Poppler’s `pdftohtml` and `pdfinfo`
on your path. Scanned input also uses `pdftoppm` and Tesseract.

```sh
python src/pdf_bookmarks.py paper.pdf
python src/pdf_bookmarks.py paper.pdf --output paper-bookmarked.pdf
python src/pdf_bookmarks.py paper-bookmarked.pdf --refresh
python src/pdf_bookmarks.py paper-bookmarked.pdf --replace-existing --output paper-rebuilt.pdf
python src/pdf_bookmarks.py book.pdf --choices choices.json --output book-bookmarked.pdf
```

Preview returns candidate IDs, levels, and one-based PDF pages. A choices file
is a JSON list such as `[{"id":1,"level":1},{"id":3,"level":2}]`. Choose
candidates from a preview of the same PDF with the same OCR options. Candidates
without resolved destinations cannot be selected. A reviewed choice can include
a `"title"` field to correct a bookmark label before embedding it. Preview
includes alternate OCR readings in `titleReview` when available. Combine
`--refresh` and `--output` to save a refreshed copy after reviewing the
proposed changes.

## Preservation recipe

The processing arguments are intentionally fixed:

```sh
ocrmypdf \
  --mode strip \
  --output-type pdf \
  --optimize 0 \
  input.pdf \
  stripped.pdf

ocrmypdf \
  --mode redo \
  --output-type pdf \
  --optimize 0 \
  --pdf-renderer fpdf2 \
  -l eng \
  stripped.pdf \
  output-ocr.pdf
```

There are deliberately no preferences for `force`, deskewing, rotation,
background removal, cleanup of final images, PDF/A conversion, or lossy
optimization.

The extension leaves `--fast-web-view` at OCRmyPDF's default. This avoids
forcing both stages to linearize while still allowing OCRmyPDF to linearize
larger outputs when useful.

## Text-layer renderer

Choose the renderer in **Zotero Settings → Lossless OCR**:

- **fpdf2** is OCRmyPDF's default and recommended renderer. It has the best
  multilingual, right-to-left, and complex-script support.
- **sandwich** uses Tesseract's text-only PDF layer. On some Latin-script scans
  its selection boxes fit the printed lines more tightly, but OCRmyPDF
  documents word-selection issues in PDF.js and macOS Preview and no
  right-to-left support.

Both choices are OCRmyPDF's maintained built-in renderers. For Latin-script
scans, `sandwich` is often the better choice for text selection in Zotero.

## Requirements

- Zotero 7, 8, 9, or 10
- [OCRmyPDF 17.6 or newer](https://ocrmypdf.readthedocs.io/) with Tesseract
- `qpdf`, `pdfinfo`, `pdftotext`, `pdfimages`, and `pdftoppm` for preflight
  detection and pre-replacement validation
- `pikepdf` from OCRmyPDF's own Python environment; no separate Python package
  installation is needed when OCRmyPDF is installed normally

On macOS with Homebrew:

```sh
brew install ocrmypdf
```

The extension searches Homebrew and common Unix executable locations. A custom
path to `ocrmypdf` and one or more Tesseract languages can be set in Zotero's
plugin preferences.

To disable original-PDF backups, open **Zotero Settings → Lossless OCR** and
uncheck **Keep the pre-OCR PDF as a sibling attachment**.

## Install

Run `./build.sh`, then in Zotero open **Tools → Plugins**, choose **Install
Plugin From File**, and select `build/lossless-ocr-for-zotero-1.9.5.xpi`.
Published releases provide the same versioned XPI on GitHub.

This is a full replacement for the earlier local “OCRmyPDF for Zotero”
extension and uses the same extension ID, so installing it upgrades that copy.

## Build and test

```sh
npm test
./build.sh
```

The OCR end-to-end test creates a scanned fixture containing both visible
footer text and stale invisible text, exercises both renderers and a repeated
`sandwich` OCR run, checks that layers do not accumulate, validates PDF
metadata and extracted text, and requires pixel-identical Poppler renders
before and after OCR. The watermark suite separately exercises text, Form,
low-opacity vector Form, image, optional-content, and annotation watermarks;
legitimate repetition; mixed qualifying and nonqualifying uses of one Form;
signed and encrypted PDF refusal; syntax checking; text preservation; and
all-page rendering.

The built XPI is written to `build/`.

## Safety notes

- Digitally signed PDFs are rejected by OCRmyPDF instead of silently
  invalidating their signatures.
- Watermark removal also rejects signed and encrypted PDFs, always creates a
  backup attachment, and changes the source only after all validation passes.
- Parent items with more than one eligible PDF use the first attachment,
  matching the original extension's behavior. Select a specific attachment to
  choose another.
- Backup attachments are ignored when resolving PDFs for later runs.
- Replacing a linked attachment modifies the linked file at its existing path.
- The recursive cleanup removes conventional PDF text with render mode 3 and
  OCRmyPDF `/OCR-*` Form layers. It intentionally does not guess at unusual
  constructs such as visible text hidden behind an image, clipping-only text,
  transparency tricks, or optional-content layers, because removing those
  automatically could damage genuine page content.

## License

MIT
