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
