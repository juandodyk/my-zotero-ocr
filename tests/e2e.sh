#!/bin/sh
set -eu

for command in ocrmypdf qpdf pdfinfo pdftotext pdfimages pdftoppm python3; do
	if ! command -v "$command" >/dev/null 2>&1; then
		printf 'missing required test command: %s\n' "$command" >&2
		exit 1
	fi
done

work_dir="tmp/pdfs/e2e"
rm -rf "$work_dir"
mkdir -p "$work_dir"
trap 'status=$?; if [ "$status" -eq 0 ]; then rm -rf "$work_dir"; else printf "test files kept at %s\n" "$work_dir" >&2; fi' EXIT

input="$work_dir/input.pdf"
stripped="$work_dir/stripped.pdf"
output="$work_dir/output.pdf"
progress_log="$work_dir/progress.log"

python3 tests/make_fixture.py "$input"
python3 tests/make_born_digital_fixture.py "$work_dir/born-digital.pdf"

ocrmypdf \
	--mode strip \
	--output-type pdf \
	--optimize 0 \
	"$input" \
	"$stripped"

if ! ocrmypdf \
	--plugin src/ocrmypdf_progress_plugin.py \
	--mode redo \
	--output-type pdf \
	--optimize 0 \
	-l eng \
	"$stripped" \
	"$output" \
	2>"$progress_log"; then
	cat "$progress_log" >&2
	exit 1
fi
node - "$progress_log" <<'NODE'
const fs = require("node:fs");
const core = require("./src/core.js");
const events = fs.readFileSync(process.argv[2], "utf8")
	.split(/\r?\n/)
	.map(line => core.parseOCRProgressEvent(line))
	.filter(Boolean);
if (!events.some(event =>
	event.description === "OCR"
	&& event.unit === "page"
	&& event.completed === 1
	&& event.total === 1
)) {
	throw new Error("OCRmyPDF did not report completed-page progress");
}
console.log("page progress reporting passed");
NODE

qpdf --check "$output"

input_pages="$(pdfinfo "$input" | awk '/^Pages:/ { print $2 }')"
output_pages="$(pdfinfo "$output" | awk '/^Pages:/ { print $2 }')"
test "$input_pages" = "$output_pages"
pdfinfo -f 1 -l "$input_pages" -box "$input" > "$work_dir/input-info.txt"
pdfinfo -f 1 -l "$output_pages" -box "$output" > "$work_dir/output-info.txt"
node - "$work_dir/input-info.txt" "$work_dir/output-info.txt" <<'NODE'
const fs = require("node:fs");
const core = require("./src/core.js");
const input = core.parsePDFInfo(fs.readFileSync(process.argv[2], "utf8"));
const output = core.parsePDFInfo(fs.readFileSync(process.argv[3], "utf8"));
core.compareGeometry(input, output);
NODE

pdftotext "$stripped" "$work_dir/stripped.txt"
pdftotext "$output" "$work_dir/output.txt"
pdfimages -list "$stripped" > "$work_dir/stripped-images.txt"
stripped_words="$(wc -w < "$work_dir/stripped.txt" | tr -d ' ')"
output_words="$(wc -w < "$work_dir/output.txt" | tr -d ' ')"
test "$output_words" -gt "$stripped_words"
test "$output_words" -ge 25

ocrmypdf \
	--mode strip \
	--output-type pdf \
	--optimize 0 \
	"$work_dir/born-digital.pdf" \
	"$work_dir/born-digital-stripped.pdf"
born_pages="$(pdfinfo "$work_dir/born-digital-stripped.pdf" | awk '/^Pages:/ { print $2 }')"
pdfinfo -f 1 -l "$born_pages" -box "$work_dir/born-digital-stripped.pdf" > "$work_dir/born-info.txt"
pdftotext "$work_dir/born-digital-stripped.pdf" "$work_dir/born-text.txt"
pdfimages -list "$work_dir/born-digital-stripped.pdf" > "$work_dir/born-images.txt"
node - "$work_dir/input-info.txt" "$work_dir/stripped.txt" "$work_dir/stripped-images.txt" \
	"$work_dir/born-info.txt" "$work_dir/born-text.txt" "$work_dir/born-images.txt" <<'NODE'
const fs = require("node:fs");
const core = require("./src/core.js");
const read = path => fs.readFileSync(path, "utf8");
const scanned = core.assessPreflight({
	pdfInfo: core.parsePDFInfo(read(process.argv[2])),
	text: read(process.argv[3]),
	pdfImages: read(process.argv[4])
});
const bornDigital = core.assessPreflight({
	pdfInfo: core.parsePDFInfo(read(process.argv[5])),
	text: read(process.argv[6]),
	pdfImages: read(process.argv[7])
});
if (scanned.shouldSkip) throw new Error("scanned fixture was incorrectly skipped");
if (!bornDigital.shouldSkip) throw new Error("born-digital fixture was not skipped");
console.log(`preflight passed (${scanned.words} scanned words; ${bornDigital.words} born-digital words)`);
NODE

pdftoppm -f 1 -singlefile -r 120 -png "$input" "$work_dir/input-render" >/dev/null 2>&1
pdftoppm -f 1 -singlefile -r 120 -png "$output" "$work_dir/output-render" >/dev/null 2>&1
python3 tests/compare_renders.py \
	"$work_dir/input-render.png" \
	"$work_dir/output-render.png"

printf 'end-to-end PDF test passed (%s -> %s words)\n' "$stripped_words" "$output_words"
