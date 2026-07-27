# Performance notes

## `--fast-web-view` benchmark

Tested on 2026-07-27 with OCRmyPDF 17.8.1 on macOS. The fixture was a
12-page, 1.7 MiB mixed PDF containing page-sized scan images, visible footer
text, and stale invisible OCR.

Each variant ran the complete `strip` then `redo` pipeline twice. The second
round reversed the order to reduce filesystem-cache bias.

| Setting in both stages | Mean time | Output size | Linearized |
| --- | ---: | ---: | --- |
| `--fast-web-view 0` | 6.580 s | 1,811,848 bytes | yes |
| Flag omitted | 5.516 s | 1,811,838 bytes | yes |
| `--fast-web-view 999999` | 5.471 s | 1,808,138 bytes | no |

All outputs:

- passed `qpdf --check`;
- extracted the same 600 words;
- retained the same page geometry and rotations; and
- produced pixel-identical Poppler renders.

On this synthetic fixture, omitting or effectively disabling the flag was
about 16-17% faster than forcing the threshold to zero. Beginning with version
1.2.0, the extension omits the flag and uses OCRmyPDF's default behavior. This
retains automatic linearization for larger files without forcing both stages
to rewrite the PDF structure.

## Text-layer renderer comparison

Tested on 2026-07-27 with OCRmyPDF 17.8.1 and Tesseract 5.5.2. The source was
the first page of a 300 dpi JSTOR scan. All three outputs passed `qpdf --check`,
extracted equivalent body text, and produced pixel-identical Poppler renders.

Selection height for the first word `This` in the abstract:

| Renderer | Selection height | One-page output size |
| --- | ---: | ---: |
| `fpdf2` | 8.581 pt | 95,947 bytes |
| `sandwich` | 8.008 pt | 91,005 bytes |
| `word-box` | 6.306 pt | 93,319 bytes |

The detected hOCR word itself was approximately 6.24 pt high. The word-box
renderer therefore tracked the printed glyph height much more closely than
the line-oriented alternatives. For a larger body-text occurrence of `This`,
the corresponding heights were 10.787 pt, 10.010 pt, and 8.288 pt.

The word-box renderer asks Tesseract for hOCR, text, and a text-only PDF in one
invocation. It keeps the PDF's embedded Unicode `GlyphLessFont` but replaces
Tesseract's line-oriented content stream with invisible, horizontally scaled
text positioned from each hOCR word box and baseline.

A 32-page end-to-end run on the complete article took 13.16 seconds. A second
run took 14.50 seconds, removed all 32 earlier OCR Forms, and again produced
exactly one OCR Form per page. Both runs extracted 13,089 words. The first
output was 2,496,526 bytes versus 2,531,067 bytes for the source, and sampled
pages were pixel-identical across the source and both outputs.

### Other free renderer tested

[ExactImage `hocr2pdf`](https://exactcode.com/opensource/exactimage/) 1.2.1 was
also tested. It preserved rendered pixels when its text-only PDF was placed
under the source page, but only 47 words remained extractable from the same
368-word hOCR fixture. Its positioning and basic font encoding were a
regression, so it was not added as a runtime dependency.
