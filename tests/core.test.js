"use strict";

const assert = require("node:assert/strict");
const core = require("../src/core.js");

assert.deepEqual(core.stageArgs("eng+fra"), {
	strip: [
		"--mode", "strip",
		"--output-type", "pdf",
		"--optimize", "0",
		"--fast-web-view", "0"
	],
	redo: [
		"--mode", "redo",
		"--output-type", "pdf",
		"--optimize", "0",
		"--fast-web-view", "0",
		"-l", "eng+fra"
	]
});

assert.throws(() => core.normalizeLanguage("eng --force-ocr"), /Invalid OCR language/);

const info = [
	"Pages:           2",
	"Page    1 size:  612 x 792 pts (letter)",
	"Page    1 rot:   0",
	"Page    2 size:  792 x 612 pts (letter)",
	"Page    2 rot:   90"
].join("\n");
const parsed = core.parsePDFInfo(info);
assert.equal(parsed.pages, 2);
assert.deepEqual(parsed.geometry[1], {
	page: 2,
	width: 792,
	height: 612,
	rotation: 90
});
assert.doesNotThrow(() => core.compareGeometry(parsed, parsed));
assert.throws(
	() => core.compareGeometry(parsed, {
		pages: 2,
		geometry: [
			parsed.geometry[0],
			{ ...parsed.geometry[1], rotation: 0 }
		]
	}),
	/rotation changed/
);

assert.deepEqual(core.assessText({
	pages: 1,
	strippedText: "visible footer",
	outputText: Array.from({ length: 30 }, (_, i) => "word" + i).join(" ")
}), {
	strippedWords: 2,
	outputWords: 30,
	warnings: []
});
assert.throws(
	() => core.assessText({ pages: 1, strippedText: "", outputText: "" }),
	/no extractable text/
);
assert.doesNotThrow(() => core.assessSize(10_000_000, 12_000_000));
assert.throws(() => core.assessSize(100_000_000, 150_000_000), /grew unexpectedly/);

assert.equal(core.isBackupAttachment({
	attachmentFilename: "paper - original.pdf",
	getDisplayTitle: () => "PDF"
}), true);
assert.equal(core.isBackupAttachment({
	attachmentFilename: "paper.pdf",
	getDisplayTitle: () => "Paper"
}), false);

console.log("core tests passed");
