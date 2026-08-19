ChromeUtils.defineESModuleGetters(globalThis, {
	Subprocess: "resource://gre/modules/Subprocess.sys.mjs"
});

function losslessOCRLog(message) {
	Zotero.debug("Lossless OCR for Zotero: " + message);
}

function makeProgressWindow(window, message, title = "Lossless OCR") {
	const id = "lossless-ocr-for-zotero-progress";
	const xhtml = "http://www.w3.org/1999/xhtml";
	let controller;

	try {
		const doc = window.document;
		doc.getElementById(id)?.remove();

		const card = doc.createElementNS(xhtml, "div");
		card.id = id;
		card.setAttribute("role", "status");
		card.setAttribute("aria-live", "polite");
		card.style.cssText = [
			"position: fixed",
			"right: 24px",
			"bottom: 24px",
			"z-index: 2147483647",
			"width: 360px",
			"max-width: calc(100vw - 48px)",
			"box-sizing: border-box",
			"padding: 14px 16px",
			"border: 1px solid color-mix(in srgb, CanvasText 18%, transparent)",
			"border-radius: 10px",
			"background: Canvas",
			"color: CanvasText",
			"box-shadow: 0 8px 28px rgba(0, 0, 0, 0.24)",
			"font: menu",
			"pointer-events: none",
			"color-scheme: light dark"
		].join(";");

		const headline = doc.createElementNS(xhtml, "div");
		headline.textContent = title;
		headline.style.cssText = "font-size: 14px; font-weight: 600; margin-bottom: 10px";

		const bar = doc.createElementNS(xhtml, "progress");
		bar.max = 100;
		bar.value = 0;
		bar.setAttribute("aria-label", "Lossless OCR progress");
		bar.style.cssText = [
			"display: block",
			"width: 100%",
			"height: 10px",
			"margin: 0 0 9px",
			"accent-color: AccentColor"
		].join(";");

		const details = doc.createElementNS(xhtml, "div");
		details.style.cssText = "display: flex; align-items: center; gap: 12px; font-size: 12px";

		const status = doc.createElementNS(xhtml, "span");
		status.textContent = message;
		status.style.cssText = [
			"min-width: 0",
			"flex: 1",
			"overflow: hidden",
			"text-overflow: ellipsis",
			"white-space: nowrap"
		].join(";");

		const percentage = doc.createElementNS(xhtml, "span");
		percentage.textContent = "0%";
		percentage.style.cssText = "flex: none; font-variant-numeric: tabular-nums; opacity: 0.72";

		details.append(status, percentage);
		card.append(headline, bar, details);
		doc.documentElement.appendChild(card);

		controller = {
			lastText: message,
			lastPercent: 0,
			update(text, percent) {
				if (text && text !== this.lastText) {
					this.lastText = text;
					status.textContent = text;
				}
				if (Number.isFinite(percent)) {
					const next = Math.min(100, Math.max(this.lastPercent, percent));
					this.lastPercent = next;
					bar.value = next;
					percentage.textContent = Math.round(next) + "%";
				}
			},
			finish(text) {
				this.update(text, 100);
			},
			close() {
				card.remove();
			},
			scope(itemIndex, itemCount) {
				return {
					update(text, itemFraction) {
						const percent = Number.isFinite(itemFraction)
							? LosslessOCRCore.mapBatchProgress(
								itemIndex,
								itemCount,
								itemFraction
							)
							: undefined;
						controller.update(text, percent);
					},
					finish(text) {
						this.update(text, 1);
					}
				};
			}
		};
		return controller;
	}
	catch (error) {
		losslessOCRLog("Could not create progress bar: " + (error.stack || error));
		const dummy = {
			update() {},
			finish() {},
			close() {},
			scope() {
				return dummy;
			}
		};
		return dummy;
	}
}

LosslessOCRForZotero = {
	id: null,
	version: null,
	rootURI: null,
	addedElementIDs: new Set(),
	processingItemIDs: new Set(),

	init({ id, version, rootURI }) {
		this.id = id;
		this.version = version;
		this.rootURI = rootURI;
	},

	addToWindow(window) {
		const doc = window.document;
		window.MozXULElement.insertFTLIfNeeded("lossless-ocr-for-zotero.ftl");
		const menu = doc.getElementById("zotero-itemmenu");
		const entries = [
			{
				id: "lossless-ocr-for-zotero-item-menu",
				l10n: "lossless-ocr-for-zotero-menu-label",
				run: () => this.run(window)
			},
			{
				id: "lossless-ocr-for-zotero-watermark-menu",
				l10n: "lossless-ocr-for-zotero-watermark-menu-label",
				run: () => this.runWatermarkRemoval(window)
			}
		];
		for (const entry of entries) {
			if (doc.getElementById(entry.id)) continue;
			const menuitem = doc.createXULElement("menuitem");
			menuitem.id = entry.id;
			menuitem.className = "menuitem-iconic";
			menuitem.setAttribute("data-l10n-id", entry.l10n);
			menuitem.addEventListener("command", entry.run);
			menu?.appendChild(menuitem);
			this.addedElementIDs.add(entry.id);
		}
	},

	addToAllWindows() {
		for (const window of Zotero.getMainWindows()) {
			if (window.ZoteroPane) this.addToWindow(window);
		}
	},

	removeFromWindow(window) {
		const doc = window.document;
		for (const id of this.addedElementIDs) {
			doc.getElementById(id)?.remove();
		}
		doc.getElementById("lossless-ocr-for-zotero-progress")?.remove();
		doc.querySelector('[href="lossless-ocr-for-zotero.ftl"]')?.remove();
	},

	removeFromAllWindows() {
		for (const window of Zotero.getMainWindows()) {
			if (window.ZoteroPane) this.removeFromWindow(window);
		}
		this.addedElementIDs.clear();
	},

	async run(window) {
		const progress = makeProgressWindow(window, "Checking tools");
		const failures = [];
		let completed = 0;
		let skipped = 0;

		try {
			progress.update("Checking tools", 1);
			const tools = await this.findRequiredTools();
			progress.update("Reading selection", 3);
			const selected = Zotero.getActiveZoteroPane().getSelectedItems();
			const jobs = await this.resolveJobs(selected, window);
			if (!jobs.length) return;
			progress.update("Starting OCR", 5);

			for (let index = 0; index < jobs.length; index++) {
				const job = jobs[index];
				const jobProgress = progress.scope(index, jobs.length);
				if (this.processingItemIDs.has(job.pdfItem.id)) {
					failures.push(job.pdfItem.getDisplayTitle() + ": already being processed");
					jobProgress.finish("Skipped PDF already being processed");
					continue;
				}

				this.processingItemIDs.add(job.pdfItem.id);
				try {
					jobProgress.update("Preparing " + job.pdfItem.getDisplayTitle(), 0.02);
					const result = await this.processPDF({
						...job,
						tools,
						progress: jobProgress,
						window
					});
					if (result.status === "skipped") skipped++;
					else completed++;
				}
				catch (error) {
					losslessOCRLog(error.stack || error);
					jobProgress.finish("OCR failed for " + job.pdfItem.getDisplayTitle());
					let message = job.pdfItem.getDisplayTitle() + ": " + error.message;
					if (error.workDir) {
						message += "\nWork files kept at: " + error.workDir;
					}
					failures.push(message);
				}
				finally {
					this.processingItemIDs.delete(job.pdfItem.id);
				}
			}

			progress.finish(
				completed + " OCRed, " + skipped + " skipped"
			);
			if (failures.length) {
				window.alert(
					"Lossless OCR completed " + completed + " PDF(s), skipped "
					+ skipped + ", with "
					+ failures.length + " failure(s):\n\n" + failures.join("\n\n")
				);
			}
		}
		catch (error) {
			losslessOCRLog(error.stack || error);
			window.alert("Lossless OCR could not start:\n\n" + error.message);
		}
		finally {
			setTimeout(() => progress.close(), failures.length ? 5000 : 1800);
		}
	},

	async runWatermarkRemoval(window) {
		const progress = makeProgressWindow(
			window,
			"Checking PDF tools",
			"PDF watermark removal"
		);
		const failures = [];
		let completed = 0;
		let skipped = 0;

		try {
			progress.update("Checking PDF tools", 1);
			const tools = await this.findWatermarkTools();
			progress.update("Reading selection", 3);
			const selected = Zotero.getActiveZoteroPane().getSelectedItems();
			const jobs = await this.resolveJobs(selected, window, { createParent: false });
			if (!jobs.length) return;

			for (let index = 0; index < jobs.length; index++) {
				const job = jobs[index];
				const jobProgress = progress.scope(index, jobs.length);
				if (this.processingItemIDs.has(job.pdfItem.id)) {
					failures.push(job.pdfItem.getDisplayTitle() + ": already being processed");
					jobProgress.finish("Skipped PDF already being processed");
					continue;
				}

				this.processingItemIDs.add(job.pdfItem.id);
				try {
					const result = await this.processWatermarkPDF({
						...job,
						tools,
						progress: jobProgress,
						window
					});
					if (result.status === "completed") completed++;
					else skipped++;
				}
				catch (error) {
					losslessOCRLog(error.stack || error);
					jobProgress.finish("Watermark removal failed");
					let message = job.pdfItem.getDisplayTitle() + ": " + error.message;
					if (error.workDir) message += "\nWork files kept at: " + error.workDir;
					failures.push(message);
				}
				finally {
					this.processingItemIDs.delete(job.pdfItem.id);
				}
			}

			progress.finish(completed + " cleaned, " + skipped + " unchanged");
			if (failures.length) {
				window.alert(
					"Watermark removal completed " + completed + " PDF(s), left "
					+ skipped + " unchanged, with " + failures.length + " failure(s):\n\n"
					+ failures.join("\n\n")
				);
			}
		}
		catch (error) {
			losslessOCRLog(error.stack || error);
			window.alert("PDF watermark removal could not start:\n\n" + error.message);
		}
		finally {
			setTimeout(() => progress.close(), failures.length ? 5000 : 1800);
		}
	},

	async resolveJobs(selected, window, { createParent = true } = {}) {
		if (!selected.length) {
			window.alert("Select at least one Zotero item or PDF attachment.");
			return [];
		}

		const jobs = [];
		const seen = new Set();
		for (let item of selected) {
			let parentItem;
			let pdfItem;

			if (item.isAttachment()) {
				if (!this.isPDFAttachment(item) || LosslessOCRCore.isBackupAttachment(item)) {
					continue;
				}
				if (item.isTopLevelItem() && createParent) {
					await Zotero.getActiveZoteroPane().createEmptyParent(item);
				}
				parentItem = item.parentItemID ? Zotero.Items.get(item.parentItemID) : null;
				pdfItem = item;
			}
			else {
				const attachments = item.getAttachments(false)
					.map(id => Zotero.Items.get(id))
					.filter(attachment =>
						this.isPDFAttachment(attachment)
						&& !LosslessOCRCore.isBackupAttachment(attachment)
					);
				if (!attachments.length) continue;
				parentItem = item;
				pdfItem = attachments[0];
				if (attachments.length > 1) {
					losslessOCRLog(
						"Multiple PDFs found for " + item.getDisplayTitle()
						+ "; using " + pdfItem.getDisplayTitle()
					);
				}
			}

			if (!seen.has(pdfItem.id)) {
				seen.add(pdfItem.id);
				jobs.push({ parentItem, pdfItem });
			}
		}

		if (!jobs.length) {
			window.alert("No eligible PDF attachments were found in the selection.");
		}
		return jobs;
	},

	isPDFAttachment(item) {
		return Boolean(
			item?.isFileAttachment()
			&& item.attachmentContentType === "application/pdf"
			&& item.getFilePath()
		);
	},

	async findRequiredTools() {
		const configured = this.getStringPref("path", "").replace(/^"(.*)"$/, "$1");
		const ocrmypdf = configured || await this.findExecutable("ocrmypdf");
		if (!ocrmypdf || !await IOUtils.exists(ocrmypdf)) {
			throw new Error(
				"No ocrmypdf executable was found. Set its full path in the plugin preferences."
			);
		}
		if (!configured) Zotero.Prefs.set("ocrmypdf.path", ocrmypdf);

		const directory = PathUtils.parent(ocrmypdf);
		const tools = { ocrmypdf };
		for (const name of ["qpdf", "pdfinfo", "pdftotext", "pdfimages"]) {
			tools[name] = await this.findExecutable(name, directory);
			if (!tools[name]) {
				throw new Error(
					name + " is required to validate PDFs before replacement but was not found."
				);
			}
		}
		const versionOutput = await this.runProcess({
			command: ocrmypdf,
			arguments: ["--version"],
			workDir: PathUtils.tempDir
		});
		const versionMatch = versionOutput.match(/^(\d+)\.(\d+)(?:\.\d+)*\s*$/m);
		if (
			!versionMatch
			|| Number(versionMatch[1]) < 17
			|| (
				Number(versionMatch[1]) === 17
				&& Number(versionMatch[2]) < 6
			)
		) {
			throw new Error(
				"Lossless OCR requires OCRmyPDF 17.6 or newer; found "
				+ versionOutput.trim() + "."
			);
		}
		return tools;
	},

	async findWatermarkTools() {
		const tools = await this.findRequiredTools();
		tools.pdftoppm = await this.findExecutable(
			"pdftoppm",
			PathUtils.parent(tools.ocrmypdf)
		);
		if (!tools.pdftoppm) {
			throw new Error("pdftoppm is required to render-check cleaned PDFs but was not found.");
		}
		tools.python = await this.findOCRmyPDFPython(tools.ocrmypdf);
		return tools;
	},

	async findOCRmyPDFPython(ocrmypdf) {
		const candidates = [];
		try {
			const firstLine = (await IOUtils.readUTF8(ocrmypdf)).split(/\r?\n/, 1)[0];
			if (firstLine.startsWith("#!")) {
				const parts = firstLine.slice(2).trim().split(/\s+/);
				if (parts[0] === "/usr/bin/env" && parts[1]) {
					const resolved = await this.findExecutable(parts[1], PathUtils.parent(ocrmypdf));
					if (resolved) candidates.push(resolved);
				}
				else if (parts[0]) {
					candidates.push(parts[0]);
				}
			}
		}
		catch (error) {
			losslessOCRLog("Could not inspect OCRmyPDF's Python shebang: " + error);
		}

		for (const name of ["python3", "python"]) {
			const candidate = await this.findExecutable(name, PathUtils.parent(ocrmypdf));
			if (candidate) candidates.push(candidate);
		}
		for (const candidate of [...new Set(candidates)]) {
			if (!await IOUtils.exists(candidate)) continue;
			try {
				await this.runProcess({
					command: candidate,
					arguments: ["-c", "import pikepdf; print(pikepdf.__version__)"],
					workDir: PathUtils.tempDir
				});
				return candidate;
			}
			catch (error) {
				losslessOCRLog("Python candidate lacks pikepdf: " + candidate);
			}
		}
		throw new Error(
			"Could not find OCRmyPDF's Python interpreter with pikepdf installed. "
			+ "Reinstall OCRmyPDF or set a valid OCRmyPDF path in the extension preferences."
		);
	},

	async findExecutable(name, preferredDirectory = "") {
		const executable = Zotero.isWin ? name + ".exe" : name;
		const candidates = [
			preferredDirectory && PathUtils.join(preferredDirectory, executable),
			"/opt/homebrew/bin/" + executable,
			"/usr/local/bin/" + executable,
			"/usr/bin/" + executable,
			"/run/current-system/sw/bin/" + executable
		].filter(Boolean);

		for (const candidate of candidates) {
			if (await IOUtils.exists(candidate)) return candidate;
		}
		return "";
	},

	async processWatermarkPDF({ parentItem, pdfItem, tools, progress, window }) {
		const sourcePath = await pdfItem.getFilePathAsync();
		if (!sourcePath || !await IOUtils.exists(sourcePath)) {
			throw new Error("The attachment file could not be found on disk.");
		}

		const workDir = PathUtils.join(
			PathUtils.tempDir,
			"watermark-zotero-" + Date.now() + "-" + Math.random().toString(36).slice(2)
		);
		const helperPath = PathUtils.join(workDir, "watermark_surgeon.py");
		const outputPath = PathUtils.join(workDir, "cleaned.pdf");
		const sourceTextPath = PathUtils.join(workDir, "source.txt");
		const outputTextPath = PathUtils.join(workDir, "cleaned.txt");
		const filename = pdfItem.attachmentFilename || PathUtils.filename(sourcePath);
		const baseName = filename.replace(/\.pdf$/i, "");
		const replacementPath = PathUtils.join(
			PathUtils.parent(sourcePath),
			"." + filename + ".watermark-removal-" + Date.now() + ".tmp"
		);
		let safeToClean = false;

		await IOUtils.makeDirectory(workDir);
		try {
			progress.update("Installing watermark detector", 0.03);
			await this.installBundledScript("watermark_surgeon.py", helperPath);
			progress.update("Scanning PDF structures", 0.08);
			const scan = await this.scanWatermarks({
				python: tools.python,
				helperPath,
				pdfPath: sourcePath,
				workDir
			});
			if (scan.encrypted) {
				throw new Error(
					"This PDF is encrypted. The extension does not alter encrypted PDFs, "
					+ "even when they can be opened without a password."
				);
			}
			if (scan.signed) {
				throw new Error(
					"This PDF contains a digital signature. Removing anything would invalidate it, "
					+ "so the file was left unchanged."
				);
			}
			if (!scan.candidates.length) {
				progress.finish("No high-confidence watermark found");
				window.alert(
					"No high-confidence removable watermark was found in:\n\n"
					+ pdfItem.getDisplayTitle()
					+ "\n\nThe detector intentionally leaves ambiguous repeated content unchanged."
				);
				safeToClean = true;
				return { status: "skipped", scan };
			}

			const details = scan.candidates
				.map((candidate, index) =>
					(index + 1) + ". " + LosslessOCRCore.describeWatermarkCandidate(candidate)
				)
				.join("\n");
			const proceed = window.confirm(
				"The detector found the following high-confidence watermark content in:\n\n"
				+ pdfItem.getDisplayTitle() + "\n\n" + details
				+ "\n\nOnly those exact occurrences will be removed. The extension will first "
				+ "validate a new PDF, then save the original as a backup attachment and "
				+ "replace this attachment. Continue?"
			);
			if (!proceed) {
				progress.finish("Watermark removal cancelled");
				safeToClean = true;
				return { status: "skipped", scan };
			}

			progress.update("Removing confirmed watermark", 0.20);
			const candidateArguments = scan.candidates.flatMap(candidate => [
				"--candidate", candidate.id
			]);
			const applyOutput = await this.runProcess({
				command: tools.python,
				arguments: [
					helperPath,
					"apply",
					sourcePath,
					outputPath,
					...candidateArguments
				],
				workDir,
				progress
			});
			const applied = this.parseHelperJSON(applyOutput, "watermark removal");
			const expectedRemovals = scan.candidates.reduce(
				(total, candidate) => total + (Number(candidate.occurrences) || 0),
				0
			);
			if (applied.removed !== expectedRemovals) {
				throw new Error(
					"The helper removed " + applied.removed + " occurrence(s), but the confirmed "
					+ "scan expected " + expectedRemovals + ". The source was left unchanged."
				);
			}

			progress.update("Validating cleaned PDF", 0.45);
			const validation = await this.validateWatermarkOutput({
				sourcePath,
				outputPath,
				sourceTextPath,
				outputTextPath,
				tools,
				helperPath,
				candidates: scan.candidates,
				workDir,
				progress
			});
			progress.update("Saving original PDF backup", 0.88);
			const backupOptions = {
				file: sourcePath,
				libraryID: pdfItem.libraryID,
				title: "Original PDF backup (before watermark removal): " + baseName,
				fileBaseName: baseName + " - watermarked original",
				contentType: "application/pdf"
			};
			if (parentItem) backupOptions.parentItemID = parentItem.id;
			await Zotero.Attachments.importFromFile(backupOptions);

			progress.update("Replacing attachment", 0.94);
			await IOUtils.copy(outputPath, replacementPath);
			await IOUtils.move(replacementPath, sourcePath, { noOverwrite: false });
			await this.refreshAndReindex(pdfItem, progress);
			safeToClean = true;
			progress.finish("Watermark removal complete");
			losslessOCRLog(
				"Removed " + applied.removed + " watermark occurrence(s) from " + sourcePath
				+ "; " + validation.outputWords + " words remain"
			);
			return { status: "completed", scan, applied, validation };
		}
		catch (error) {
			error.workDir = workDir;
			throw error;
		}
		finally {
			await Zotero.File.removeIfExists(replacementPath);
			if (safeToClean) {
				try {
					await IOUtils.remove(workDir, { recursive: true });
				}
				catch (cleanupError) {
					losslessOCRLog("Could not remove work directory " + workDir + ": " + cleanupError);
				}
			}
		}
	},

	async scanWatermarks({ python, helperPath, pdfPath, workDir }) {
		const output = await this.runProcess({
			command: python,
			arguments: [helperPath, "scan", pdfPath],
			workDir
		});
		const report = this.parseHelperJSON(output, "watermark scan");
		if (!Array.isArray(report.candidates) || !Number.isInteger(report.pages)) {
			throw new Error("The watermark scanner returned an incomplete report.");
		}
		return report;
	},

	parseHelperJSON(output, operation) {
		try {
			return JSON.parse(String(output).trim());
		}
		catch (error) {
			throw new Error("Could not parse the " + operation + " report.");
		}
	},

	async validateWatermarkOutput({
		sourcePath,
		outputPath,
		sourceTextPath,
		outputTextPath,
		tools,
		helperPath,
		candidates,
		workDir,
		progress
	}) {
		const renderDirectory = PathUtils.join(workDir, "rendered-cleaned-pages");
		await IOUtils.makeDirectory(renderDirectory);
		const [
			,
			sourceInfo,
			outputInfo,
			sourceText,
			outputText,
			sourceStat,
			outputStat,
			postScan
		] = await this.awaitAll([
			this.runProcess({
				command: tools.qpdf,
				arguments: ["--check", outputPath],
				workDir
			}),
			this.readPDFInfo(tools.pdfinfo, sourcePath, workDir),
			this.readPDFInfo(tools.pdfinfo, outputPath, workDir),
			this.extractText(tools.pdftotext, sourcePath, sourceTextPath, workDir),
			this.extractText(tools.pdftotext, outputPath, outputTextPath, workDir),
			IOUtils.stat(sourcePath),
			IOUtils.stat(outputPath),
			this.scanWatermarks({
				python: tools.python,
				helperPath,
				pdfPath: outputPath,
				workDir
			})
		]);
		LosslessOCRCore.compareGeometry(sourceInfo, outputInfo);
		const textAssessment = LosslessOCRCore.assessWatermarkText({
			inputText: sourceText,
			outputText,
			candidates,
			pages: outputInfo.pages
		});
		const sizeAssessment = LosslessOCRCore.assessSize(sourceStat.size, outputStat.size);
		const selectedIDs = new Set(candidates.map(candidate => candidate.id));
		const surviving = postScan.candidates.filter(candidate => selectedIDs.has(candidate.id));
		if (surviving.length) {
			throw new Error("The cleaned PDF still contains a selected watermark candidate.");
		}

		progress.update("Rendering every cleaned page", 0.72);
		await this.runProcess({
			command: tools.pdftoppm,
			arguments: [
				"-r", "48", "-png", outputPath,
				PathUtils.join(renderDirectory, "page")
			],
			workDir,
			progress
		});
		const rendered = (await IOUtils.getChildren(renderDirectory))
			.filter(path => /^page-\d+\.png$/i.test(PathUtils.filename(path)));
		if (rendered.length !== outputInfo.pages) {
			throw new Error(
				"Rendered " + rendered.length + " of " + outputInfo.pages + " cleaned pages."
			);
		}
		return {
			...textAssessment,
			...sizeAssessment,
			renderedPages: rendered.length
		};
	},

	async installBundledScript(filename, destinationPath) {
		const source = await Zotero.File.getContentsFromURLAsync(this.rootURI + filename);
		await IOUtils.writeUTF8(destinationPath, source);
	},

	async processPDF({ parentItem, pdfItem, tools, progress, window }) {
		const sourcePath = await pdfItem.getFilePathAsync();
		if (!sourcePath || !await IOUtils.exists(sourcePath)) {
			throw new Error("The attachment file could not be found on disk.");
		}

		const language = LosslessOCRCore.normalizeLanguage(
			this.getStringPref("language", "eng")
		);
		const configuredRenderer = this.getStringPref("pdfRenderer", "fpdf2");
		const pdfRenderer = LosslessOCRCore.normalizePDFRenderer(configuredRenderer);
		if (pdfRenderer !== configuredRenderer) {
			Zotero.Prefs.set("ocrmypdf.pdfRenderer", pdfRenderer);
		}
		losslessOCRLog("Selected PDF renderer: " + pdfRenderer);
		const workDir = PathUtils.join(
			PathUtils.tempDir,
			"lossless-ocr-zotero-" + Date.now() + "-" + Math.random().toString(36).slice(2)
		);
		const stripInputPath = PathUtils.join(workDir, "strip-input.pdf");
		const strippedPath = PathUtils.join(workDir, "stripped.pdf");
		const outputPath = PathUtils.join(workDir, "output-ocr.pdf");
		const progressPluginPath = PathUtils.join(workDir, "ocrmypdf_progress_plugin.py");
		const strippedTextPath = PathUtils.join(workDir, "stripped.txt");
		const outputTextPath = PathUtils.join(workDir, "output.txt");
		const filename = pdfItem.attachmentFilename || PathUtils.filename(sourcePath);
		const baseName = filename.replace(/\.pdf$/i, "");
		const replacementPath = PathUtils.join(
			PathUtils.parent(sourcePath),
			"." + filename + ".lossless-ocr-" + Date.now() + ".tmp"
		);
		let completed = false;

		await IOUtils.makeDirectory(workDir);
		try {
			const args = LosslessOCRCore.stageArgs(language, pdfRenderer);
			await this.installProgressPlugin(progressPluginPath);

			progress.update("Preparing temporary PDF copy", 0.03);
			await IOUtils.copy(sourcePath, stripInputPath);

			progress.update("Stripping old invisible OCR", 0.05);
			await this.runProcess({
				command: tools.ocrmypdf,
				arguments: [
					"--plugin", progressPluginPath,
					"--lossless-clean-invisible-layers",
					...args.strip,
					stripInputPath,
					strippedPath
				],
				workDir,
				progress
			});

			progress.update("Checking whether OCR is needed", 0.20);
			const preflight = await this.runPreflight({
				sourcePath,
				strippedPath,
				strippedTextPath,
				tools,
				workDir
			});
			if (preflight.shouldSkip) {
				progress.finish("Skipped born-digital PDF");
				losslessOCRLog(
					"Skipped " + sourcePath + ": " + preflight.reason
					+ " (" + preflight.words + " words)"
				);
				completed = true;
				return { status: "skipped", preflight };
			}

			progress.update(
				"Running replacement OCR (" + pdfRenderer + ")",
				0.30
			);
			await this.runProcess({
				command: tools.ocrmypdf,
				arguments: [
					"--plugin", progressPluginPath,
					...args.redo,
					strippedPath,
					outputPath
				],
				workDir,
				progress,
				onProgressEvent(event) {
					const update = LosslessOCRCore.describeOCRProgress(event);
					if (update) progress.update(update.text, update.fraction);
				}
			});
			progress.update("OCR finished", 0.72);

			progress.update("Validating OCR output", 0.75);
			const validation = await this.validateOutput({
				sourcePath,
				outputPath,
				outputTextPath,
				tools,
				workDir,
				preflight
			});
			progress.update("Validation passed", 0.86);

			if (validation.warnings.length) {
				const proceed = window.confirm(
					"Lossless OCR validation produced a warning:\n\n"
					+ validation.warnings.join("\n")
					+ "\n\nThe source has not been changed. Replace it anyway?"
				);
				if (!proceed) {
					throw new Error("Replacement was cancelled after validation warnings.");
				}
			}
			if (this.getBoolPref("keepBackup", true)) {
				progress.update("Saving original PDF backup", 0.89);
				await Zotero.Attachments.importFromFile({
					file: sourcePath,
					libraryID: parentItem.libraryID,
					parentItemID: parentItem.id,
					title: "Original PDF backup (before lossless OCR): " + baseName,
					fileBaseName: baseName + " - original",
					contentType: "application/pdf"
				});
			}

			progress.update("Replacing attachment", 0.94);
			await IOUtils.copy(outputPath, replacementPath);
			await IOUtils.move(replacementPath, sourcePath, { noOverwrite: false });
			await this.refreshAndReindex(pdfItem, progress);
			completed = true;
			progress.finish("Lossless OCR complete");

			losslessOCRLog(
				"Completed " + sourcePath + ": " + validation.outputWords
				+ " words, " + LosslessOCRCore.formatBytes(validation.outputBytes)
			);
			return { status: "completed", validation };
		}
		catch (error) {
			error.workDir = workDir;
			throw error;
		}
		finally {
			await Zotero.File.removeIfExists(replacementPath);
			if (completed && await IOUtils.exists(sourcePath)) {
				try {
					await IOUtils.remove(workDir, { recursive: true });
				}
				catch (cleanupError) {
					losslessOCRLog("Could not remove work directory " + workDir + ": " + cleanupError);
				}
			}
		}
	},

	async validateOutput({
		sourcePath,
		outputPath,
		outputTextPath,
		tools,
		workDir,
		preflight
	}) {
		const [, outputInfo, outputText, inputStat, outputStat] = await this.awaitAll([
			this.runProcess({
				command: tools.qpdf,
				arguments: ["--check", outputPath],
				workDir
			}),
			this.readPDFInfo(tools.pdfinfo, outputPath, workDir),
			this.extractText(tools.pdftotext, outputPath, outputTextPath, workDir),
			IOUtils.stat(sourcePath),
			IOUtils.stat(outputPath)
		]);
		LosslessOCRCore.compareGeometry(preflight.sourceInfo, outputInfo);

		const textAssessment = LosslessOCRCore.assessText({
			pages: outputInfo.pages,
			strippedText: preflight.strippedText,
			outputText
		});
		const sizeAssessment = LosslessOCRCore.assessSize(inputStat.size, outputStat.size);

		return {
			...textAssessment,
			...sizeAssessment
		};
	},

	async runPreflight({ sourcePath, strippedPath, strippedTextPath, tools, workDir }) {
		const [, sourceInfo, strippedInfo, strippedText, pdfImages] = await this.awaitAll([
			this.runProcess({
				command: tools.qpdf,
				arguments: ["--check", strippedPath],
				workDir
			}),
			this.readPDFInfo(tools.pdfinfo, sourcePath, workDir),
			this.readPDFInfo(tools.pdfinfo, strippedPath, workDir),
			this.extractText(tools.pdftotext, strippedPath, strippedTextPath, workDir),
			this.runProcess({
				command: tools.pdfimages,
				arguments: ["-list", strippedPath],
				workDir
			})
		]);
		LosslessOCRCore.compareGeometry(sourceInfo, strippedInfo);

		return {
			...LosslessOCRCore.assessPreflight({
				pdfInfo: strippedInfo,
				text: strippedText,
				pdfImages
			}),
			sourceInfo,
			strippedInfo,
			strippedText
		};
	},

	async installProgressPlugin(destinationPath) {
		const source = await Zotero.File.getContentsFromURLAsync(
			this.rootURI + "ocrmypdf_progress_plugin.py"
		);
		await IOUtils.writeUTF8(destinationPath, source);
	},

	async extractText(pdftotext, inputPath, outputPath, workDir) {
		await this.runProcess({
			command: pdftotext,
			arguments: [inputPath, outputPath],
			workDir
		});
		return IOUtils.readUTF8(outputPath);
	},

	async awaitAll(promises) {
		const results = await Promise.allSettled(promises);
		const failure = results.find(result => result.status === "rejected");
		if (failure) throw failure.reason;
		return results.map(result => result.value);
	},

	async readPDFInfo(pdfinfo, path, workDir) {
		const summary = await this.runProcess({
			command: pdfinfo,
			arguments: [path],
			workDir
		});
		const pagesMatch = summary.match(/^Pages:\s+(\d+)\s*$/m);
		if (!pagesMatch) throw new Error("pdfinfo did not report a page count for " + path);

		const detailed = await this.runProcess({
			command: pdfinfo,
			arguments: ["-f", "1", "-l", pagesMatch[1], "-box", path],
			workDir
		});
		return LosslessOCRCore.parsePDFInfo(detailed);
	},

	async runProcess({ command, arguments: args, workDir, progress, onProgressEvent }) {
		losslessOCRLog("Running " + command + " " + args.map(this.quoteArgument).join(" "));
		const proc = await Subprocess.call({
			command,
			workdir: workDir,
			arguments: args,
			environment: this.getSubprocessEnvironment(command),
			environmentAppend: true,
			stderr: "stdout"
		});

		let output = "";
		let progressBuffer = "";
		const consumeProgressLine = line => {
			if (!onProgressEvent) return;
			const event = LosslessOCRCore.parseOCRProgressEvent(line);
			if (event) onProgressEvent(event);
		};
		let chunk;
		while ((chunk = await proc.stdout.readString())) {
			output += chunk;
			losslessOCRLog(chunk);
			progressBuffer += chunk;
			const lines = progressBuffer.split(/\r?\n/);
			progressBuffer = lines.pop();
			for (const line of lines) consumeProgressLine(line);
			if (progress) {
				const message = this.describeProcessOutput(chunk);
				if (message) progress.update(message);
			}
		}
		if (progressBuffer) consumeProgressLine(progressBuffer);

		const { exitCode } = await proc.wait();
		if (exitCode !== 0) {
			throw new Error(
				PathUtils.filename(command) + " exited with code " + exitCode
				+ ":\n" + this.trimProcessOutput(output)
			);
		}
		return output;
	},

	getSubprocessEnvironment(command) {
		const existingPath = Subprocess.getEnvironment().PATH || "";
		const separator = Zotero.isWin ? ";" : ":";
		const path = [
			PathUtils.parent(command),
			"/opt/homebrew/bin",
			"/opt/homebrew/sbin",
			"/usr/local/bin",
			"/usr/local/sbin",
			"/usr/bin",
			"/bin",
			"/usr/sbin",
			"/sbin",
			existingPath
		].filter(Boolean).join(separator);
		return { PATH: path, LC_ALL: "C", LANG: "C" };
	},

	async refreshAndReindex(pdfItem, progress) {
		try {
			await pdfItem.saveTx();
			await pdfItem.reload(null, true);
			await Zotero.Notifier.trigger("refresh", "item", [pdfItem.id]);
			await Zotero.Notifier.trigger("modify", "item", [pdfItem.id]);
		}
		catch (error) {
			losslessOCRLog("Attachment refresh skipped: " + (error.stack || error));
		}

		if (!Zotero.Fulltext?.indexItems) return;
		try {
			progress.update("Updating Zotero full-text index", 0.97);
			await Zotero.Fulltext.indexItems([pdfItem.id], {
				complete: true,
				ignoreErrors: true
			});
		}
		catch (error) {
			losslessOCRLog("Full-text indexing skipped: " + (error.stack || error));
		}
	},

	describeProcessOutput(chunk) {
		const lower = chunk.toLowerCase();
		if (lower.includes("scanning contents")) return "Analyzing PDF";
		if (lower.includes("start processing")) return "Processing pages";
		if (lower.includes("tesseract")) return "Recognizing text";
		if (lower.includes("lineariz")) return "Writing PDF structure";
		if (lower.includes("output file")) return "Writing OCR PDF";
		return "";
	},

	getStringPref(name, fallback) {
		const value = Zotero.Prefs.get("ocrmypdf." + name);
		if (value === undefined || value === null || String(value).trim() === "") {
			return fallback;
		}
		return String(value).trim();
	},

	getBoolPref(name, fallback) {
		const value = Zotero.Prefs.get("ocrmypdf." + name);
		if (value === undefined || value === null) return fallback;
		if (typeof value === "boolean") return value;
		if (typeof value === "string") return value.toLowerCase() === "true";
		return fallback;
	},

	quoteArgument(value) {
		const string = String(value);
		return /\s/.test(string) ? JSON.stringify(string) : string;
	},

	trimProcessOutput(output) {
		const lines = output.trim().split(/\r?\n/).filter(Boolean);
		if (lines.length <= 20) return lines.join("\n");
		return [...lines.slice(0, 10), "...", ...lines.slice(-10)].join("\n");
	}
};
