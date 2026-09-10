#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

if ! command -v node >/dev/null 2>&1; then
  echo "node is required to validate addon.json." >&2
  exit 1
fi

if ! command -v zip >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  echo "zip or python3 is required to package the addon." >&2
  exit 1
fi

node scripts/validate.js

ADDON_ID="$(node -e "process.stdout.write(require('./addon.json').id)")"
ADDON_VERSION="$(node -e "process.stdout.write(require('./addon.json').version)")"
PACKAGE_NAME="${ADDON_ID}-${ADDON_VERSION}.zip"

rm -rf dist
mkdir -p dist

if command -v zip >/dev/null 2>&1; then
  zip -r "dist/${PACKAGE_NAME}" addon.json web -x "*.DS_Store" >/dev/null
else
  python3 - "dist/${PACKAGE_NAME}" <<'PY'
import sys
import zipfile
from pathlib import Path

output = Path(sys.argv[1])
inputs = [Path("addon.json"), Path("web")]

with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for source in inputs:
        if source.is_file():
            archive.write(source, source.as_posix())
            continue

        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            if path.name == ".DS_Store":
                continue
            archive.write(path, path.as_posix())
PY
fi

echo "Created: dist/${PACKAGE_NAME}"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "dist/${PACKAGE_NAME}" | tee "dist/${PACKAGE_NAME}.sha256"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "dist/${PACKAGE_NAME}" | tee "dist/${PACKAGE_NAME}.sha256"
else
  echo "Install sha256sum or shasum to calculate the release hash." >&2
  exit 1
fi
