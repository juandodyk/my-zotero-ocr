var LosslessOCRCore = (() => {
	"use strict";

	const PRESERVATION_ARGS = [
		"--output-type", "pdf",
		"--optimize", "0",
		"--fast-web-view", "0"
	];

	function normalizeLanguage(value) {
		const language = String(value || "eng").trim();
		if (!/^[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*$/.test(language)) {
			throw new Error(
				"Invalid OCR language. Use Tesseract language codes such as eng or eng+fra+deu."
			);
		}
		return language;
	}

	function stageArgs(language) {
		return {
			strip: ["--mode", "strip", ...PRESERVATION_ARGS],
			redo: ["--mode", "redo", ...PRESERVATION_ARGS, "-l", normalizeLanguage(language)]
		};
	}

	function parsePDFInfo(text) {
		const pagesMatch = text.match(/^Pages:\s+(\d+)\s*$/m);
		if (!pagesMatch) {
			throw new Error("pdfinfo did not report a page count.");
		}

		const pages = Number(pagesMatch[1]);
		const sizes = new Map();
		const rotations = new Map();
		const sizePattern = /^Page\s+(\d+)\s+size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts\b/gm;
		const rotationPattern = /^Page\s+(\d+)\s+rot:\s+(-?\d+)\s*$/gm;
		let match;

		while ((match = sizePattern.exec(text))) {
			sizes.set(Number(match[1]), [Number(match[2]), Number(match[3])]);
		}
		while ((match = rotationPattern.exec(text))) {
			rotations.set(Number(match[1]), Number(match[2]));
		}

		if (!sizes.size) {
			const sizeMatch = text.match(/^Page size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts\b/m);
			if (sizeMatch && pages === 1) {
				sizes.set(1, [Number(sizeMatch[1]), Number(sizeMatch[2])]);
			}
		}
		if (!rotations.size) {
			const rotationMatch = text.match(/^Page rot:\s+(-?\d+)\s*$/m);
			if (rotationMatch && pages === 1) {
				rotations.set(1, Number(rotationMatch[1]));
			}
		}

		const geometry = [];
		for (let page = 1; page <= pages; page++) {
			const size = sizes.get(page);
			if (!size) {
				throw new Error("pdfinfo did not report dimensions for page " + page + ".");
			}
			geometry.push({
				page,
				width: size[0],
				height: size[1],
				rotation: rotations.get(page) || 0
			});
		}

		return { pages, geometry };
	}

	function compareGeometry(input, output, tolerance = 0.01) {
		if (input.pages !== output.pages) {
			throw new Error(
				"Page count changed from " + input.pages + " to " + output.pages + "."
			);
		}

		for (let index = 0; index < input.geometry.length; index++) {
			const before = input.geometry[index];
			const after = output.geometry[index];
			if (
				Math.abs(before.width - after.width) > tolerance
				|| Math.abs(before.height - after.height) > tolerance
			) {
				throw new Error(
					"Page " + before.page + " dimensions changed from "
					+ before.width + " x " + before.height + " pt to "
					+ after.width + " x " + after.height + " pt."
				);
			}
			if (before.rotation !== after.rotation) {
				throw new Error(
					"Page " + before.page + " rotation changed from "
					+ before.rotation + " to " + after.rotation + " degrees."
				);
			}
		}
	}

	function countWords(text) {
		return (String(text || "").trim().match(/\S+/g) || []).length;
	}

	function parsePDFImages(text, pdfInfo) {
		const pages = new Map(pdfInfo.geometry.map(page => [page.page, page]));
		const images = [];

		for (const line of String(text || "").split(/\r?\n/)) {
			const columns = line.trim().split(/\s+/);
			if (columns.length < 14 || !/^\d+$/.test(columns[0]) || columns[2] !== "image") {
				continue;
			}

			const pageNumber = Number(columns[0]);
			const page = pages.get(pageNumber);
			const width = Number(columns[3]);
			const height = Number(columns[4]);
			const xPPI = Number(columns[12]);
			const yPPI = Number(columns[13]);
			if (!page || !width || !height || !xPPI || !yPPI) continue;

			const displayedWidth = width * 72 / xPPI;
			const displayedHeight = height * 72 / yPPI;
			const pageArea = page.width * page.height;
			const coverage = pageArea
				? Math.min(1, displayedWidth * displayedHeight / pageArea)
				: 0;
			images.push({
				page: pageNumber,
				width,
				height,
				xPPI,
				yPPI,
				coverage
			});
		}

		return images;
	}

	function assessPreflight({ pdfInfo, text, pdfImages }) {
		const words = countWords(text);
		const substantial = Math.max(100, pdfInfo.pages * 20);
		const images = parsePDFImages(pdfImages, pdfInfo);
		const pageSizedImages = images.filter(image => image.coverage >= 0.65);
		const shouldSkip = words >= substantial && pageSizedImages.length === 0;

		return {
			shouldSkip,
			words,
			substantial,
			imageCount: images.length,
			pageSizedImageCount: pageSizedImages.length,
			reason: shouldSkip
				? "substantial text remains after stripping and no page-sized scanned images were found"
				: "the PDF is scanned, mixed, or ambiguous"
		};
	}

	function assessText({ pages, strippedText, outputText }) {
		const strippedWords = countWords(strippedText);
		const outputWords = countWords(outputText);
		if (!outputWords) {
			throw new Error("The OCR output contains no extractable text.");
		}

		const warnings = [];
		const substantial = Math.max(25, pages * 5);
		const usefulGain = Math.max(10, pages * 2);
		if (outputWords < substantial) {
			warnings.push(
				"Only " + outputWords + " words were extracted from " + pages + " page(s)."
			);
		}
		if (outputWords < strippedWords + usefulGain) {
			warnings.push(
				"Extracted text increased from " + strippedWords + " to only "
				+ outputWords + " words after OCR."
			);
		}

		return { strippedWords, outputWords, warnings };
	}

	function assessSize(inputBytes, outputBytes) {
		if (!inputBytes || !outputBytes) {
			throw new Error("Could not determine PDF file sizes.");
		}
		const maximum = Math.max(
			Math.ceil(inputBytes * 1.35),
			inputBytes + 15 * 1024 * 1024
		);
		if (outputBytes > maximum) {
			throw new Error(
				"OCR output grew unexpectedly from " + formatBytes(inputBytes)
				+ " to " + formatBytes(outputBytes) + "."
			);
		}
		return {
			inputBytes,
			outputBytes,
			ratio: outputBytes / inputBytes
		};
	}

	function formatBytes(bytes) {
		if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KiB";
		return (bytes / (1024 * 1024)).toFixed(1) + " MiB";
	}

	function isBackupAttachment(item) {
		const filename = item?.attachmentFilename || "";
		const title = item?.getDisplayTitle?.() || "";
		return / - original(?: \d+)?\.pdf$/i.test(filename)
			|| /^Original PDF backup \(before lossless OCR\):/i.test(title);
	}

	return {
		PRESERVATION_ARGS,
		normalizeLanguage,
		stageArgs,
		parsePDFInfo,
		compareGeometry,
		countWords,
		parsePDFImages,
		assessPreflight,
		assessText,
		assessSize,
		formatBytes,
		isBackupAttachment
	};
})();

if (typeof module !== "undefined" && module.exports) {
	module.exports = LosslessOCRCore;
}
