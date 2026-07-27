ChromeUtils.defineESModuleGetters(globalThis, {
	Subprocess: "resource://gre/modules/Subprocess.sys.mjs"
});

function losslessOCRLog(message) {
	Zotero.debug("Lossless OCR for Zotero: " + message);
}

function makeProgressWindow(message) {
	try {
		const progressWindow = new Zotero.ProgressWindow({ closeOnClick: false });
		progressWindow.changeHeadline("Lossless OCR");
		progressWindow.show();
		const itemProgress = new progressWindow.ItemProgress(
			"chrome://zotero/skin/attachment-pdf.svg",
			message
		);
		return {
			update(text) {
				if (this.lastText === text) return;
				this.lastText = text;
				itemProgress.setText(text);
			},
			finish(text) {
				itemProgress.setText(text);
				itemProgress.setProgress(100);
			},
			close() {
				progressWindow.close();
			}
		};
	}
	catch (error) {
		losslessOCRLog("Could not create progress window: " + error);
		return { update() {}, finish() {}, close() {} };
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
		const id = "lossless-ocr-for-zotero-item-menu";
		if (doc.getElementById(id)) return;

		window.MozXULElement.insertFTLIfNeeded("lossless-ocr-for-zotero.ftl");
		const menuitem = doc.createXULElement("menuitem");
		menuitem.id = id;
		menuitem.className = "menuitem-iconic";
		menuitem.setAttribute("data-l10n-id", "lossless-ocr-for-zotero-menu-label");
		menuitem.addEventListener("command", () => this.run(window));
		doc.getElementById("zotero-itemmenu")?.appendChild(menuitem);
		this.addedElementIDs.add(id);
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
		doc.querySelector('[href="lossless-ocr-for-zotero.ftl"]')?.remove();
	},

	removeFromAllWindows() {
		for (const window of Zotero.getMainWindows()) {
			if (window.ZoteroPane) this.removeFromWindow(window);
		}
		this.addedElementIDs.clear();
	},

	async run(window) {
		const progress = makeProgressWindow("Checking tools");
		const failures = [];
		let completed = 0;

		try {
			const tools = await this.findRequiredTools();
			const selected = Zotero.getActiveZoteroPane().getSelectedItems();
			const jobs = await this.resolveJobs(selected, window);
			if (!jobs.length) return;

			for (const job of jobs) {
				if (this.processingItemIDs.has(job.pdfItem.id)) {
					failures.push(job.pdfItem.getDisplayTitle() + ": already being processed");
					continue;
				}

				this.processingItemIDs.add(job.pdfItem.id);
				try {
					progress.update("Preparing " + job.pdfItem.getDisplayTitle());
					await this.processPDF({ ...job, tools, progress, window });
					completed++;
				}
				catch (error) {
					losslessOCRLog(error.stack || error);
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
				completed + " PDF" + (completed === 1 ? "" : "s") + " completed"
			);
			if (failures.length) {
				window.alert(
					"Lossless OCR completed " + completed + " PDF(s), with "
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

	async resolveJobs(selected, window) {
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
				if (item.isTopLevelItem()) {
					await Zotero.getActiveZoteroPane().createEmptyParent(item);
				}
				parentItem = Zotero.Items.get(item.parentItemID);
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
		for (const name of ["qpdf", "pdfinfo", "pdftotext"]) {
			tools[name] = await this.findExecutable(name, directory);
			if (!tools[name]) {
				throw new Error(
					name + " is required to validate PDFs before replacement but was not found."
				);
			}
		}
		return tools;
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

	async processPDF({ parentItem, pdfItem, tools, progress, window }) {
		const sourcePath = await pdfItem.getFilePathAsync();
		if (!sourcePath || !await IOUtils.exists(sourcePath)) {
			throw new Error("The attachment file could not be found on disk.");
		}

		const language = LosslessOCRCore.normalizeLanguage(
			this.getStringPref("language", "eng")
		);
		const workDir = PathUtils.join(
			PathUtils.tempDir,
			"lossless-ocr-zotero-" + Date.now() + "-" + Math.random().toString(36).slice(2)
		);
		const strippedPath = PathUtils.join(workDir, "stripped.pdf");
		const outputPath = PathUtils.join(workDir, "output-ocr.pdf");
		const strippedTextPath = PathUtils.join(workDir, "stripped.txt");
		const outputTextPath = PathUtils.join(workDir, "output.txt");
		const filename = pdfItem.attachmentFilename || PathUtils.filename(sourcePath);
		const baseName = filename.replace(/\.pdf$/i, "");
		const backupPath = PathUtils.join(workDir, baseName + " - original.pdf");
		const replacementPath = PathUtils.join(
			PathUtils.parent(sourcePath),
			"." + filename + ".lossless-ocr-" + Date.now() + ".tmp"
		);
		let completed = false;

		await IOUtils.makeDirectory(workDir);
		try {
			const args = LosslessOCRCore.stageArgs(language);

			progress.update("Stripping old invisible OCR");
			await this.runProcess({
				command: tools.ocrmypdf,
				arguments: [...args.strip, sourcePath, strippedPath],
				workDir,
				progress
			});

			progress.update("Checking stripped PDF");
			await this.runProcess({
				command: tools.qpdf,
				arguments: ["--check", strippedPath],
				workDir
			});

			progress.update("Running replacement OCR");
			await this.runProcess({
				command: tools.ocrmypdf,
				arguments: [...args.redo, strippedPath, outputPath],
				workDir,
				progress
			});

			progress.update("Validating OCR output");
			const validation = await this.validateOutput({
				sourcePath,
				strippedPath,
				outputPath,
				strippedTextPath,
				outputTextPath,
				tools,
				workDir
			});

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
				progress.update("Saving original PDF backup");
				await IOUtils.copy(sourcePath, backupPath);
				await Zotero.Attachments.importFromFile({
					file: backupPath,
					libraryID: parentItem.libraryID,
					parentItemID: parentItem.id,
					title: "Original PDF backup (before lossless OCR): " + baseName,
					contentType: "application/pdf"
				});
			}

			progress.update("Replacing attachment");
			await IOUtils.copy(outputPath, replacementPath);
			await IOUtils.move(replacementPath, sourcePath, { noOverwrite: false });
			await this.refreshAndReindex(pdfItem, progress);
			completed = true;

			losslessOCRLog(
				"Completed " + sourcePath + ": " + validation.outputWords
				+ " words, " + LosslessOCRCore.formatBytes(validation.outputBytes)
			);
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
		strippedPath,
		outputPath,
		strippedTextPath,
		outputTextPath,
		tools,
		workDir
	}) {
		await this.runProcess({
			command: tools.qpdf,
			arguments: ["--check", outputPath],
			workDir
		});

		const sourceInfo = await this.readPDFInfo(tools.pdfinfo, sourcePath, workDir);
		const outputInfo = await this.readPDFInfo(tools.pdfinfo, outputPath, workDir);
		LosslessOCRCore.compareGeometry(sourceInfo, outputInfo);

		await this.runProcess({
			command: tools.pdftotext,
			arguments: [strippedPath, strippedTextPath],
			workDir
		});
		await this.runProcess({
			command: tools.pdftotext,
			arguments: [outputPath, outputTextPath],
			workDir
		});

		const textAssessment = LosslessOCRCore.assessText({
			pages: outputInfo.pages,
			strippedText: await IOUtils.readUTF8(strippedTextPath),
			outputText: await IOUtils.readUTF8(outputTextPath)
		});
		const inputStat = await IOUtils.stat(sourcePath);
		const outputStat = await IOUtils.stat(outputPath);
		const sizeAssessment = LosslessOCRCore.assessSize(inputStat.size, outputStat.size);

		return {
			...textAssessment,
			...sizeAssessment
		};
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

	async runProcess({ command, arguments: args, workDir, progress }) {
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
		let chunk;
		while ((chunk = await proc.stdout.readString())) {
			output += chunk;
			losslessOCRLog(chunk);
			if (progress) {
				const message = this.describeProcessOutput(chunk);
				if (message) progress.update(message);
			}
		}

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
			progress.update("Updating Zotero full-text index");
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
