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

console.log("extension wiring tests passed");
