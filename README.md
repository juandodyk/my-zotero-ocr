# Lossless OCR for Zotero

A small Zotero 7-9 extension that replaces stale OCR in scanned PDFs while
preserving their visible page content.

It is designed for mixed PDFs such as JSTOR downloads that contain scanned
page images, an old invisible OCR layer, and a little real visible text such as
a download footer. The command appears in Zotero's item context menu as
**Replace OCR losslessly**.

## What it does

For the first eligible PDF under each selected bibliographic item, or for each
directly selected PDF attachment, the extension:

1. Makes a byte-for-byte temporary copy; the source is not changed yet.
2. Recursively removes conventional invisible text and prior OCRmyPDF
   `/OCR-*` Form layers from that copy, then runs OCRmyPDF's `strip` mode.
3. Skips the expensive OCR stage when substantial born-digital text remains
   and no page-sized scanned images are present.
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
page and displays the completed and total page counts.

Running the extension again is safe: it removes the existing removable
invisible layers before creating one fresh OCR layer. Visible text such as
download footers is preserved.

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
- **word boxes** is the extension's experimental renderer for the tightest
  Latin-text selection. It uses each word's hOCR bounding box and line
  baseline, while retaining Tesseract's invisible Unicode PDF font. Tesseract
  emits hOCR, text, and its PDF font in one OCR invocation, so this does not
  run recognition twice. It adds no dependency beyond OCRmyPDF.

`fpdf2` and `sandwich` are OCRmyPDF's built-in renderers. The word-box renderer
is implemented by the extension's bundled OCRmyPDF plugin. It changes only the
invisible text positioning; visible page content still comes untouched from
the stripped source PDF.

## Requirements

- Zotero 7, 8, or 9
- [OCRmyPDF 17.6 or newer](https://ocrmypdf.readthedocs.io/) with Tesseract
- `qpdf`, `pdfinfo`, `pdftotext`, and `pdfimages` for preflight detection and
  pre-replacement validation

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

Download `lossless-ocr-for-zotero-1.5.0.xpi` from the latest GitHub release.
In Zotero, open **Tools → Plugins**, choose **Install Plugin From File**, and
select the XPI.

This is a full replacement for the earlier local “OCRmyPDF for Zotero”
extension and uses the same extension ID, so installing it upgrades that copy.

## Build and test

```sh
npm test
./build.sh
```

The end-to-end test creates a scanned fixture containing both visible footer
text and stale invisible text, exercises all three renderers and a repeated
word-box OCR run, checks that layers do not accumulate, compares selection-box
geometry, validates PDF metadata and extracted text, and requires
pixel-identical Poppler renders before and after OCR.

The built XPI is written to `build/`.

## Safety notes

- Digitally signed PDFs are rejected by OCRmyPDF instead of silently
  invalidating their signatures.
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
