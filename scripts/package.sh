#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

if ! command -v node >/dev/null 2>&1; then
  echo "node is required to validate addon.json." >&2
  exit 1
fi

if ! command -v zip >/dev/null 2>&1; then
  echo "zip is required to package the addon." >&2
  exit 1
fi

node scripts/validate.js

ADDON_ID="$(node -e "process.stdout.write(require('./addon.json').id)")"
ADDON_VERSION="$(node -e "process.stdout.write(require('./addon.json').version)")"
PACKAGE_NAME="${ADDON_ID}-${ADDON_VERSION}.zip"

rm -rf dist
mkdir -p dist

zip -r "dist/${PACKAGE_NAME}" addon.json web -x "*.DS_Store" >/dev/null

echo "Created: dist/${PACKAGE_NAME}"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "dist/${PACKAGE_NAME}" | tee "dist/${PACKAGE_NAME}.sha256"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "dist/${PACKAGE_NAME}" | tee "dist/${PACKAGE_NAME}.sha256"
else
  echo "Install sha256sum or shasum to calculate the release hash." >&2
  exit 1
fi
