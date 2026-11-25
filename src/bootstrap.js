var LosslessOCRForZotero;
var LosslessOCRBookmarkAPI;

function log(message) {
	Zotero.debug("Lossless OCR for Zotero: " + message);
}

function install() {
	log("Installed");
}

async function startup({ id, version, rootURI }) {
	log("Starting");

	Zotero.PreferencePanes.register({
		pluginID: "ocrmypdf-for-zotero@juandodyk.local",
		src: rootURI + "prefs.xhtml"
	});

	Services.scriptloader.loadSubScript(rootURI + "core.js");
	Services.scriptloader.loadSubScript(rootURI + "lossless-ocr-for-zotero.js");
	LosslessOCRForZotero.init({ id, version, rootURI });
	LosslessOCRForZotero.addToAllWindows();
	const extension = LosslessOCRForZotero;
	LosslessOCRBookmarkAPI = {
		supportsRefresh: true,
		run: (items, options) => extension.bookmarkItems(items, options)
	};
	Zotero.LosslessOCRBookmarks = LosslessOCRBookmarkAPI;
}

function onMainWindowLoad({ window }) {
	LosslessOCRForZotero.addToWindow(window);
}

function onMainWindowUnload({ window }) {
	LosslessOCRForZotero.removeFromWindow(window);
}

function shutdown() {
	log("Shutting down");
	if (Zotero.LosslessOCRBookmarks === LosslessOCRBookmarkAPI) delete Zotero.LosslessOCRBookmarks;
	LosslessOCRBookmarkAPI = undefined;
	LosslessOCRForZotero.removeFromAllWindows();
	LosslessOCRForZotero = undefined;
}

function uninstall() {
	log("Uninstalled");
}
