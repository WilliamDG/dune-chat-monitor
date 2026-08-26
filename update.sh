#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"

[[ -f "$PROJECT_DIR/.env" ]] || { echo "[ERROR] .env not found. Run ./install.sh first." >&2; exit 1; }
# shellcheck disable=SC1091
source "$PROJECT_DIR/.env"

INSTALL_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"

mkdir -p "$INSTALL_DIR"
rm -rf "$INSTALL_DIR/web"
cp -a "$PROJECT_DIR/addon.json" "$INSTALL_DIR/"
cp -a "$PROJECT_DIR/web" "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/web/live"

cd "$PROJECT_DIR"
docker compose -f compose.yml up -d --build

echo "[OK] Updated. Chat database and .env were preserved."
