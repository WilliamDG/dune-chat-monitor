#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
CONFIG_FILE="$PROJECT_DIR/config/dune-chat-monitor.env"

[[ -f "$CONFIG_FILE" ]] || {
  echo "[ERROR] $CONFIG_FILE not found. Run ./install.sh first." >&2
  exit 1
}

# shellcheck disable=SC1090
source "$CONFIG_FILE"

compose() {
  docker compose \
    --env-file "$CONFIG_FILE" \
    -f "$PROJECT_DIR/compose.yml" \
    "$@"
}

INSTALL_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"
LIVE_DIR="$INSTALL_DIR/web/live"

echo "Updating Dune Chat Monitor..."

# Preserve the live directory because it is the collector's bind-mount source.
mkdir -p "$LIVE_DIR"

cp -a "$PROJECT_DIR/addon.json" "$INSTALL_DIR/addon.json"

find "$INSTALL_DIR/web" \
  -mindepth 1 \
  -maxdepth 1 \
  ! -name live \
  -exec rm -rf {} +

find "$PROJECT_DIR/web" \
  -mindepth 1 \
  -maxdepth 1 \
  -exec cp -a {} "$INSTALL_DIR/web/" \;

cd "$PROJECT_DIR"
compose up -d --build

echo "[OK] Updated."
echo "[OK] Preserved config: $CONFIG_FILE"
echo "[OK] Preserved database: $PROJECT_DIR/data/chat.sqlite3"
