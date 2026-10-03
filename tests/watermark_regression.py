#!/usr/bin/env python3
"""Regression checks for nested, opaque and horizontal publisher overlays."""
import sys
from pathlib import Path
import pikepdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import watermark_surgeon as surgeon


def stream_form(pdf, data, resources):
    form = pdf.make_stream(data)
    form['/Type'] = pikepdf.Name.XObject
    form['/Subtype'] = pikepdf.Name.Form
    form['/BBox'] = pikepdf.Array([0, 0, 612, 792])
    form['/Resources'] = resources
    return form


def main():
    directory = Path(sys.argv[1])
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    mixed = stream_form(pdf,
        b'q 0.7 g BT /F1 36 Tf 1 0 0 1 100 380 Tm (ORIGINAL UNEDITED MANUSCRIPT) Tj ET Q '
        b'BT /F1 10 Tf 1 0 0 1 72 40 Tm (KEEP COPYRIGHT FOOTER) Tj ET', resources)
    wrapper = stream_form(pdf, b'/Mixed Do', pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Mixed=mixed)))
    artifact = stream_form(pdf,
        b'/Artifact << /Subtype /Watermark >> BDC '
        b'BT /F1 8 Tf 1 0 0 1 72 80 Tm (Download notice) Tj ET EMC', resources)
    for number in range(5):
        page = pdf.add_blank_page(page_size=(612, 792))
        page.obj['/Resources'] = pikepdf.Dictionary(Font=resources.Font,
            XObject=pikepdf.Dictionary(Wrapper=wrapper, Mixed=mixed, Notice=artifact))
        data = (b'BT /F1 32 Tf 1 0 0 1 100 700 Tm (KEEP BLACK TITLE) Tj ET '
                b'BT /F1 12 Tf 1 0 0 1 72 620 Tm (KEEP BODY TEXT) Tj ET '
                b'BT /F1 32 Tf 1 0 0 1 100 500 Tm (KEEP CENTRAL BLACK HEADING) Tj ET '
                b'q 0.7 g BT /F1 10 Tf 1 0 0 1 100 520 Tm (KEEP SMALL GRAY LABEL) Tj ET Q '
                b'q /Wrapper Do Q q 0.15 0 0 0.15 10 10 cm /Mixed Do Q ')
        # Only 2/5 pages have this unknown, horizontal, opaque gray watermark.
        if number < 2:
            data += b'q 0.65 g BT /F1 32 Tf 1 0 0 1 120 450 Tm (LICENSED TO EXAMPLE) Tj ET Q '
        if number == 0:
            data += b'q /Notice Do Q '
        page.obj['/Contents'] = pdf.make_stream(data)
    source = directory / 'nested-horizontal.pdf'
    pdf.save(source)
    groups, candidates = surgeon.scan_document(pdf)
    assert len(candidates) == 3, candidates
    manuscript = next(c for c in candidates if c['text'] == 'ORIGINAL UNEDITED MANUSCRIPT')
    assert manuscript['occurrences'] == 5, manuscript
    assert next(c for c in candidates if c['text'] == 'LICENSED TO EXAMPLE')['pageCount'] == 2
    assert next(c for c in candidates if c['kind'] == 'artifact')['pageCount'] == 1
    surgeon.apply_candidates(pdf, groups, {c['id'] for c in candidates})
    assert not surgeon.scan_document(pdf)[1], surgeon.scan_document(pdf)[1]
    # Shared original Form and its ordinary, small invocation are intact.
    for page in pdf.pages:
        original = page.obj['/Resources']['/XObject']['/Mixed']
        assert b'(ORIGINAL UNEDITED MANUSCRIPT)' in original.read_bytes()
        assert b'(KEEP COPYRIGHT FOOTER)' in original.read_bytes()
        assert b'(KEEP BLACK TITLE)' in page.obj['/Contents'].read_bytes()
    output = directory / 'nested-horizontal-clean.pdf'
    pdf.save(output)
    import subprocess
    text = subprocess.check_output(['pdftotext', '-layout', str(output), '-'], text=True)
    assert text.count('KEEP COPYRIGHT FOOTER') >= 5, text
    assert text.count('KEEP BLACK TITLE') == 5
    assert text.count('KEEP BODY TEXT') == 5
    assert text.count('KEEP CENTRAL BLACK HEADING') == 5
    assert text.count('KEEP SMALL GRAY LABEL') == 5
    assert 'LICENSED TO EXAMPLE' not in text and 'Download notice' not in text
    assert text.count('ORIGINAL UNEDITED MANUSCRIPT') == 5, text
    subprocess.run(['qpdf', '--check', str(output)], check=True)
    print('Nested, horizontal, minority-coverage and shared-form regressions passed')


if __name__ == '__main__':
    main()
