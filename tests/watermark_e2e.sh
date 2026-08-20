#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1

for command in ocrmypdf qpdf pdfinfo pdftotext pdftoppm python3; do
	if ! command -v "$command" >/dev/null 2>&1; then
		printf 'missing required watermark test command: %s\n' "$command" >&2
		exit 1
	fi
done

mkdir -p tmp/pdfs
work_dir="$(mktemp -d tmp/pdfs/watermark.XXXXXX)"
trap 'status=$?; if [ "$status" -eq 0 ]; then rm -rf "$work_dir"; else printf "watermark test files kept at %s\n" "$work_dir" >&2; fi' EXIT

ocrmypdf_python="$(sed -n '1s/^#!//p' "$(command -v ocrmypdf)")"
python3 tests/make_watermark_fixtures.py "$work_dir"
"$ocrmypdf_python" tests/augment_watermark_fixtures.py "$work_dir"

scan_and_apply() {
	name="$1"
	expected_candidates="${2:-1}"
	forbidden_pattern="${3:-DRAFT|PEER REVIEW|CONFIDENTIAL}"
	input="$work_dir/$name-watermark.pdf"
	scan="$work_dir/$name-scan.json"
	output="$work_dir/$name-clean.pdf"
	"$ocrmypdf_python" src/watermark_surgeon.py scan "$input" > "$scan"
	candidate_id="$(node -e '
		const fs = require("node:fs");
		const report = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
		if (report.signed) throw new Error("fixture unexpectedly signed");
		const expected = Number(process.argv[2]);
		if (report.candidates.length !== expected) {
			throw new Error(`expected ${expected} candidate(s), found ${report.candidates.length}`);
		}
		process.stdout.write(report.candidates.map(candidate => candidate.id).join("\n"));
	' "$scan" "$expected_candidates")"
	set --
	for candidate in $candidate_id; do
		set -- "$@" --candidate "$candidate"
	done
	"$ocrmypdf_python" src/watermark_surgeon.py apply \
		"$input" "$output" "$@" > "$work_dir/$name-apply.json"
	"$ocrmypdf_python" src/watermark_surgeon.py scan "$output" > "$work_dir/$name-post-scan.json"
	node -e '
		const fs = require("node:fs");
		const applied = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
		const postScan = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
		if (applied.removed < 1) throw new Error("watermark apply removed nothing");
		if (postScan.candidates.length) throw new Error("watermark candidate survived removal");
	' "$work_dir/$name-apply.json" "$work_dir/$name-post-scan.json"
	qpdf --check "$output"
	test "$(pdfinfo "$input" | awk '/^Pages:/ { print $2 }')" = \
		"$(pdfinfo "$output" | awk '/^Pages:/ { print $2 }')"
	pdftotext "$output" "$work_dir/$name-clean.txt"
	grep -q 'ordinary text must survive' "$work_dir/$name-clean.txt"
	if grep -Eiq "$forbidden_pattern" "$work_dir/$name-clean.txt"; then
		printf 'watermark text survived in %s output\n' "$name" >&2
		exit 1
	fi
	pdftoppm -r 48 -png "$output" "$work_dir/$name-render" >/dev/null 2>&1
	test "$(find "$work_dir" -name "$name-render-*.png" | wc -l | tr -d ' ')" = 5
}

scan_and_apply text
scan_and_apply form
scan_and_apply vector-form 1 'pattern-that-does-not-occur'
scan_and_apply image 1 'REVIEW COPY'
scan_and_apply annotation 1 'CONFIDENTIAL watermark'
scan_and_apply optional-content 1 'DRAFT'

"$ocrmypdf_python" src/watermark_surgeon.py scan \
	"$work_dir/mixed-form-uses.pdf" > "$work_dir/mixed-scan.json"
mixed_candidate="$(node -e '
	const fs = require("node:fs");
	const report = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
	if (report.candidates.length !== 1) throw new Error("expected one mixed-use candidate");
	if (report.candidates[0].occurrences !== 19) {
		throw new Error(`expected 19 qualifying uses, found ${report.candidates[0].occurrences}`);
	}
	process.stdout.write(report.candidates[0].id);
' "$work_dir/mixed-scan.json")"
"$ocrmypdf_python" src/watermark_surgeon.py apply \
	"$work_dir/mixed-form-uses.pdf" "$work_dir/mixed-clean.pdf" \
	--candidate "$mixed_candidate" > "$work_dir/mixed-apply.json"
pdftotext "$work_dir/mixed-clean.pdf" "$work_dir/mixed-clean.txt"
test "$(grep -Eic '^CONFIDENTIAL$' "$work_dir/mixed-clean.txt")" = 1
qpdf --check "$work_dir/mixed-clean.pdf"

"$ocrmypdf_python" src/watermark_surgeon.py scan \
	"$work_dir/legitimate-repetition.pdf" > "$work_dir/legitimate-scan.json"
"$ocrmypdf_python" src/watermark_surgeon.py scan \
	"$work_dir/legitimate-vector-repetition.pdf" > "$work_dir/legitimate-vector-scan.json"
node -e '
	const fs = require("node:fs");
	for (const path of process.argv.slice(1)) {
		const report = JSON.parse(fs.readFileSync(path, "utf8"));
		if (report.candidates.length) {
			throw new Error(`legitimate repeated content was classified as a watermark: ${path}`);
		}
	}
' "$work_dir/legitimate-scan.json" "$work_dir/legitimate-vector-scan.json"

"$ocrmypdf_python" src/watermark_surgeon.py scan \
	"$work_dir/signed-watermark.pdf" > "$work_dir/signed-scan.json"
signed_candidate="$(node -e '
	const fs = require("node:fs");
	const report = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
	if (!report.signed) throw new Error("signature was not detected");
	process.stdout.write(report.candidates[0].id);
' "$work_dir/signed-scan.json")"
if "$ocrmypdf_python" src/watermark_surgeon.py apply \
	"$work_dir/signed-watermark.pdf" "$work_dir/signed-clean.pdf" \
	--candidate "$signed_candidate" > /dev/null 2>&1; then
	printf 'signed PDF was modified unexpectedly\n' >&2
	exit 1
fi

"$ocrmypdf_python" src/watermark_surgeon.py scan \
	"$work_dir/encrypted-watermark.pdf" > "$work_dir/encrypted-scan.json"
encrypted_candidate="$(node -e '
	const fs = require("node:fs");
	const report = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
	if (!report.encrypted) throw new Error("encryption was not detected");
	process.stdout.write(report.candidates[0].id);
' "$work_dir/encrypted-scan.json")"
if "$ocrmypdf_python" src/watermark_surgeon.py apply \
	"$work_dir/encrypted-watermark.pdf" "$work_dir/encrypted-clean.pdf" \
	--candidate "$encrypted_candidate" > /dev/null 2>&1; then
	printf 'encrypted PDF was modified unexpectedly\n' >&2
	exit 1
fi

printf 'watermark end-to-end tests passed\n'
