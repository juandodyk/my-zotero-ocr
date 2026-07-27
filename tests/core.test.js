"use strict";

const assert = require("node:assert/strict");
const core = require("../src/core.js");

assert.deepEqual(core.stageArgs("eng+fra", "sandwich"), {
	strip: [
		"--mode", "strip",
		"--output-type", "pdf",
		"--optimize", "0"
	],
	redo: [
		"--mode", "redo",
		"--output-type", "pdf",
		"--optimize", "0",
		"--pdf-renderer", "sandwich",
		"-l", "eng+fra"
	]
});

assert.throws(() => core.normalizeLanguage("eng --force-ocr"), /Invalid OCR language/);
assert.equal(core.normalizePDFRenderer(" FPDF2 "), "fpdf2");
assert.throws(() => core.normalizePDFRenderer("hocr"), /Invalid PDF renderer/);
assert.equal(core.describePDFRenderer("word-box"), "word boxes");
assert.doesNotThrow(() => core.validatePDFRenderer("word-box", {
	creator: "OCRmyPDF / Lossless OCR word-box renderer + Tesseract OCR"
}));
assert.throws(() => core.validatePDFRenderer("word-box", {
	creator: "OCRmyPDF / Tesseract OCR + PDF"
}), /different text layer/);
assert.doesNotThrow(() => core.validatePDFRenderer("sandwich", {
	creator: "OCRmyPDF / Tesseract OCR + PDF"
}));
assert.deepEqual(core.stageArgs("eng", "word-box").redo, [
	"--mode", "redo",
	"--output-type", "pdf",
	"--optimize", "0",
	"--pdf-renderer", "sandwich",
	"--lossless-word-box-renderer",
	"-l", "eng"
]);
assert.equal(core.mapBatchProgress(0, 2, 0), 5);
assert.equal(core.mapBatchProgress(0, 2, 1), 52);
assert.equal(core.mapBatchProgress(1, 2, 1), 99);
const ocrProgress = core.parseOCRProgressEvent(
	'LOSSLESS_OCR_PROGRESS {"description":"OCR","unit":"page","completed":4,"total":8}'
);
assert.deepEqual(ocrProgress, {
	description: "OCR",
	unit: "page",
	completed: 4,
	total: 8
});
assert.deepEqual(core.describeOCRProgress(ocrProgress), {
	text: "OCR pages: 4 of 8",
	fraction: 0.5
});
assert.equal(core.describeOCRProgress({
	...ocrProgress,
	completed: 4.5
}), null);
assert.deepEqual(core.describeOCRProgress({
	description: "Linearizing",
	unit: "%",
	completed: 50,
	total: 100
}), {
	text: "Writing PDF structure",
	fraction: 0.71
});
assert.equal(core.parseOCRProgressEvent("ordinary OCRmyPDF output"), null);

const info = [
	"Creator:         OCRmyPDF / Lossless OCR word-box renderer",
	"Pages:           2",
	"Page    1 size:  612 x 792 pts (letter)",
	"Page    1 rot:   0",
	"Page    2 size:  792 x 612 pts (letter)",
	"Page    2 rot:   90"
].join("\n");
const parsed = core.parsePDFInfo(info);
assert.equal(parsed.pages, 2);
assert.equal(parsed.creator, "OCRmyPDF / Lossless OCR word-box renderer");
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

const scannedImageList = [
	"page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio",
	"--------------------------------------------------------------------------------------------",
	"   1     0 image    1275  1650  rgb     3   8  jpeg   no         3  0   150   150  142K 2.3%"
].join("\n");
const singlePageInfo = {
	pages: 1,
	geometry: [{ page: 1, width: 612, height: 792, rotation: 0 }]
};
const images = core.parsePDFImages(scannedImageList, singlePageInfo);
assert.equal(images.length, 1);
assert.ok(images[0].coverage > 0.99);
assert.equal(core.assessPreflight({
	pdfInfo: singlePageInfo,
	text: "visible footer",
	pdfImages: scannedImageList
}).shouldSkip, false);
assert.equal(core.assessPreflight({
	pdfInfo: singlePageInfo,
	text: Array.from({ length: 120 }, (_, i) => "word" + i).join(" "),
	pdfImages: ""
}).shouldSkip, true);
assert.equal(core.assessPreflight({
	pdfInfo: singlePageInfo,
	text: Array.from({ length: 120 }, (_, i) => "word" + i).join(" "),
	pdfImages: scannedImageList
}).shouldSkip, false);

console.log("core tests passed");
