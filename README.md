# Lossless OCR for Zotero

A small Zotero 7-9 extension that replaces stale OCR in scanned PDFs and
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

1. Scans without changing the PDF and reports only high-confidence candidates.
2. Recognizes repeated page-stream text, direct Form XObjects, repeated
   translucent diagonal image XObjects, keyword-named optional-content layers,
   and PDF watermark annotations.
3. Shows the candidate type, any readable text, page coverage, angle, and
   opacity, then requires confirmation.
4. Removes only the qualifying operations recorded during a verified rescan.
   If a shared Form is also used elsewhere as ordinary content, that other
   invocation is retained.
5. Writes a new PDF and checks syntax, page count, dimensions, rotations,
   extracted-text loss, file-size growth, candidate disappearance, and a
   Poppler render of every page before touching the Zotero attachment.
6. Always imports the unchanged original as a sibling backup, atomically
   replaces the file behind the existing attachment, refreshes Zotero, and
   rebuilds its full-text index.

Encrypted and digitally signed PDFs are left unchanged. Ambiguous repeated
headers, logos, decorative content, nested constructs, and unrecognized
watermark encodings are reported as no high-confidence match rather than
guessed at. The menu wording deliberately says “detect” because no safe PDF
tool can identify every possible watermark automatically.

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

- Zotero 7, 8, or 9
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
Plugin From File**, and select `build/lossless-ocr-for-zotero-1.7.0.xpi`.
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
image, optional-content, and annotation watermarks; legitimate repetition;
mixed qualifying and nonqualifying uses of one Form; signed and encrypted PDF
refusal; syntax checking; text preservation; and all-page rendering.

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
