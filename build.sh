#!/bin/sh
set -eu

version="$(node -p "require('./src/manifest.json').version")"

mkdir -p build
xpi="build/lossless-ocr-for-zotero-${version}.xpi"
rm -f "$xpi"
(cd src && zip -DX -r "../$xpi" * -x "**/.*")
printf '%s\n' "$xpi"
