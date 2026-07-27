var LosslessOCRForZotero;

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
}

function onMainWindowLoad({ window }) {
	LosslessOCRForZotero.addToWindow(window);
}

function onMainWindowUnload({ window }) {
	LosslessOCRForZotero.removeFromWindow(window);
}

function shutdown() {
	log("Shutting down");
	LosslessOCRForZotero.removeFromAllWindows();
	LosslessOCRForZotero = undefined;
}

function uninstall() {
	log("Uninstalled");
}
