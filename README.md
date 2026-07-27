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

1. Copies nothing over the source yet.
2. Strips invisible OCR without rasterizing the PDF.
3. Skips the expensive OCR stage when substantial born-digital text remains
   and no page-sized scanned images are present.
4. Runs replacement OCR on scanned, mixed, or ambiguous PDFs.
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

## Preservation recipe

The processing arguments are intentionally fixed:

```sh
ocrmypdf \
  --mode strip \
  --output-type pdf \
  --optimize 0 \
  --fast-web-view 0 \
  input.pdf \
  stripped.pdf

ocrmypdf \
  --mode redo \
  --output-type pdf \
  --optimize 0 \
  --fast-web-view 0 \
  -l eng \
  stripped.pdf \
  output-ocr.pdf
```

There are deliberately no preferences for `force`, deskewing, rotation,
background removal, cleanup of final images, PDF/A conversion, or lossy
optimization.

Note: in current OCRmyPDF releases, `--fast-web-view 0` requests
linearization. It remains fixed here because it is part of the preservation
recipe this extension implements.

## Requirements

- Zotero 7, 8, or 9
- [OCRmyPDF](https://ocrmypdf.readthedocs.io/) with Tesseract
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

Download `lossless-ocr-for-zotero-1.1.0.xpi` from the latest GitHub release.
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
text and stale invisible text, runs the exact two-stage recipe, checks PDF
metadata and extracted text, and requires pixel-identical Poppler renders
before and after OCR.

The built XPI is written to `build/`.

## Safety notes

- Digitally signed PDFs are rejected by OCRmyPDF instead of silently
  invalidating their signatures.
- Parent items with more than one eligible PDF use the first attachment,
  matching the original extension's behavior. Select a specific attachment to
  choose another.
- Backup attachments are ignored when resolving PDFs for later runs.
- Replacing a linked attachment modifies the linked file at its existing path.

## License

MIT
