# Project instructions

## Publish plugin updates

When completing a requested plugin change, commit and push the changes and publish a GitHub release so Zotero can update the plugin automatically. Do not stop at a local XPI or ask the user to install it manually. This is the user’s standing authorization for this project, unless they explicitly request local-only work or say not to publish.

Before publishing:

- Bump the version consistently in `src/manifest.json`, `package.json`, and the README installation filename.
- Run `npm test` and build the versioned XPI with `./build.sh`.
- Update `updates.json` with the new version, the GitHub release asset URL, and the SHA-256 of the exact built XPI.
- Commit and push the changes to the project’s release branch (currently `main`). Create the matching `v<version>` tag and GitHub release, and upload the XPI.
- Verify that the published asset matches the update manifest’s checksum and that the remote update manifest points to the release. Report the release URL and any unresolved publication or CI failures.

Keep PDF attachments unchanged unless the user asks to apply watermark removal or OCR to them.
