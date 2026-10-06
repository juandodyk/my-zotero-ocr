"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const elements = new Map();
const menu = {
	id: "zotero-itemmenu",
	children: [],
	appendChild(element) {
		this.children.push(element);
		elements.set(element.id, element);
	}
};
elements.set(menu.id, menu);

const document = {
	documentElement: { appendChild() {} },
	getElementById(id) {
		return elements.get(id) || null;
	},
	createXULElement() {
		return {
			attributes: {},
			listeners: {},
			setAttribute(name, value) {
				this.attributes[name] = value;
			},
			addEventListener(name, listener) {
				this.listeners[name] = listener;
			},
			remove() {
				this.removed = true;
				elements.delete(this.id);
			}
		};
	},
	querySelector() {
		return null;
	}
};

const context = vm.createContext({
	console,
	setTimeout,
	clearTimeout,
	ChromeUtils: {
		defineESModuleGetters(target) {
			target.Subprocess = {};
		}
	},
	Zotero: {
		debug() {},
		getMainWindows() {
			return [];
		}
	},
	LosslessOCRCore: {
		describeWatermarkCandidate() {
			return "candidate";
		}
	}
});
vm.runInContext(
	fs.readFileSync("src/lossless-ocr-for-zotero.js", "utf8")
	+ "\n;globalThis.__extension = LosslessOCRForZotero;",
	context
);
const extension = context.__extension;
const window = {
	document,
	MozXULElement: { insertFTLIfNeeded() {} }
};

extension.init({ id: "test", version: "1", rootURI: "test://" });
extension.addToWindow(window);
assert.deepEqual(
	menu.children.map(item => item.id),
	[
		"lossless-ocr-for-zotero-item-menu",
		"lossless-ocr-for-zotero-watermark-menu"
	]
);
assert.equal(
	menu.children[1].attributes["data-l10n-id"],
	"lossless-ocr-for-zotero-watermark-menu-label"
);
assert.equal(typeof menu.children[0].listeners.command, "function");
assert.equal(typeof menu.children[1].listeners.command, "function");

// Adding the same window again must not duplicate either command.
extension.addToWindow(window);
assert.equal(menu.children.length, 2);

assert.equal(
	JSON.stringify(extension.parseHelperJSON('{"pages":2,"candidates":[]}', "test")),
	'{"pages":2,"candidates":[]}'
);
assert.throws(
	() => extension.parseHelperJSON("not json", "test"),
	/Could not parse the test report/
);

extension.removeFromWindow(window);
assert.equal(menu.children.every(item => item.removed), true);

async function testProcessExitCodes() {
	let exitCode = 0;
	const warning = "WARNING: incorrect offset in outlines table\nqpdf: operation succeeded with warnings\n";
	const logs = [];
	context.Zotero.debug = message => logs.push(message);
	context.PathUtils = {
		filename: path => path.split("/").pop(),
		parent: () => "/usr/bin"
	};
	context.Subprocess.getEnvironment = () => ({ PATH: "/usr/bin" });
	context.Subprocess.call = async () => {
		let read = false;
		return {
			stdout: {
				async readString() {
					if (read) return "";
					read = true;
					return warning;
				}
			},
			async wait() { return { exitCode }; }
		};
	};
	const check = {
		command: "/usr/bin/qpdf", arguments: ["--check", "stripped.pdf"],
		workDir: "/tmp", acceptedExitCodes: [0, 3]
	};
	assert.equal(await extension.runProcess(check), warning);
	exitCode = 3;
	assert.equal(await extension.runProcess(check), warning);
	assert.ok(logs.some(message => message.includes(warning)), "warnings remain logged");
	exitCode = 2;
	await assert.rejects(extension.runProcess(check), /qpdf exited with code 2/);
	exitCode = 1;
	await assert.rejects(extension.runProcess(check), /qpdf exited with code 1/);
	exitCode = 3;
	await assert.rejects(extension.runProcess({
		...check, acceptedExitCodes: undefined
	}), /qpdf exited with code 3/);
	await assert.rejects(extension.runProcess({
		command: "/usr/bin/ocrmypdf", arguments: [], workDir: "/tmp"
	}), /ocrmypdf exited with code 3/);
}

async function testBundledResourceLoading() {
	const loaded = [];
	const written = [];
	const rootURI = "jar:file:///profile/extensions/ocrmypdf-for-zotero@juandodyk.local.xpi!/";
	extension.rootURI = rootURI;
	context.Zotero.File = {
		async getContentsFromURLAsync() {
			throw new Error("NS_ERROR_FAILURE [nsIURI.username]");
		},
		async getContentsAsync(channel, charset) {
			assert.equal(charset, "UTF-8");
			return fs.readFileSync("src/" + channel.uri.slice(rootURI.length), "utf8");
		}
	};
	context.Ci = {
		nsILoadInfo: { SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL: 1 },
		nsIContentPolicy: { TYPE_OTHER: 2 }
	};
	context.NetUtil = {
		newChannel(options) {
			assert.equal(options.loadUsingSystemPrincipal, true);
			assert.equal(options.securityFlags, 1);
			assert.equal(options.contentPolicyType, 2);
			loaded.push(options.uri);
			return options;
		}
	};
	context.IOUtils = {
		async writeUTF8(path, source) { written.push({ path, source }); }
	};
	await extension.installProgressPlugin("/tmp/progress.py");
	await extension.installBundledScript("watermark_surgeon.py", "/tmp/watermark.py");
	assert.deepEqual(loaded, [
		rootURI + "ocrmypdf_progress_plugin.py",
		rootURI + "watermark_surgeon.py"
	]);
	assert.deepEqual(written, [
		{ path: "/tmp/progress.py", source: fs.readFileSync("src/ocrmypdf_progress_plugin.py", "utf8") },
		{ path: "/tmp/watermark.py", source: fs.readFileSync("src/watermark_surgeon.py", "utf8") }
	]);
	context.Zotero.File.getContentsAsync = async () => { throw new Error("Missing resource"); };
	await assert.rejects(extension.installProgressPlugin("/tmp/missing.py"), /Missing resource/);
	assert.equal(written.length, 2, "failed resource reads must not write a helper");
}

Promise.resolve().then(testBundledResourceLoading).then(testProcessExitCodes).then(() => {
	console.log("extension wiring and process exit tests passed");
}).catch(error => {
	console.error(error);
	process.exitCode = 1;
});
