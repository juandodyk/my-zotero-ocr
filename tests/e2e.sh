#!/bin/sh
set -eu

for command in ocrmypdf qpdf pdfinfo pdftotext pdftoppm python3; do
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

python3 tests/make_fixture.py "$input"

ocrmypdf \
	--mode strip \
	--output-type pdf \
	--optimize 0 \
	--fast-web-view 0 \
	"$input" \
	"$stripped"

ocrmypdf \
	--mode redo \
	--output-type pdf \
	--optimize 0 \
	--fast-web-view 0 \
	-l eng \
	"$stripped" \
	"$output"

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
stripped_words="$(wc -w < "$work_dir/stripped.txt" | tr -d ' ')"
output_words="$(wc -w < "$work_dir/output.txt" | tr -d ' ')"
test "$output_words" -gt "$stripped_words"
test "$output_words" -ge 25

pdftoppm -f 1 -singlefile -r 120 -png "$input" "$work_dir/input-render" >/dev/null 2>&1
pdftoppm -f 1 -singlefile -r 120 -png "$output" "$work_dir/output-render" >/dev/null 2>&1
python3 tests/compare_renders.py \
	"$work_dir/input-render.png" \
	"$work_dir/output-render.png"

printf 'end-to-end PDF test passed (%s -> %s words)\n' "$stripped_words" "$output_words"
