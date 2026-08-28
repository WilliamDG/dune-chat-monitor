#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
SERVICE_NAME="dune-chat-monitor.service"
CONFIG_FILE="$PROJECT_DIR/config/dune-chat-monitor.env"

[[ -f "$CONFIG_FILE" ]] || {
  echo "[ERROR] $CONFIG_FILE not found. Run ./install.sh first." >&2
  exit 1
}

# shellcheck disable=SC1090
source "$CONFIG_FILE"

# v0.2.5 uses UTC storage and the browser local timezone for display.
if grep -q '^TIMEZONE=' "$CONFIG_FILE" 2>/dev/null; then
  sed -i '/^TIMEZONE=/d' "$CONFIG_FILE"
  echo "[OK] Removed obsolete TIMEZONE from local configuration."
fi

# v0.2.0 no longer uses a configured server display name.
if grep -q '^SERVER_NAME=' "$CONFIG_FILE" 2>/dev/null; then
  sed -i '/^SERVER_NAME=/d' "$CONFIG_FILE"
  echo "[OK] Removed obsolete SERVER_NAME from local configuration."
fi

# v0.2.2 replaces the old fixed export limit with lazy history pages.
if grep -q '^CHAT_EXPORT_LIMIT=' "$CONFIG_FILE" 2>/dev/null; then
  sed -i '/^CHAT_EXPORT_LIMIT=/d' "$CONFIG_FILE"
  echo "[OK] Removed obsolete CHAT_EXPORT_LIMIT from local configuration."
fi
if ! grep -q '^CHAT_PAGE_SIZE=' "$CONFIG_FILE" 2>/dev/null; then
  printf '\nCHAT_PAGE_SIZE="50"\n' >> "$CONFIG_FILE"
  echo "[OK] Added CHAT_PAGE_SIZE=50 for lazy history loading."
fi

INSTALL_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"
LIVE_DIR="$INSTALL_DIR/web/live"

echo "Updating Dune Chat Monitor..."

mkdir -p "$INSTALL_DIR/web" "$LIVE_DIR"

cp -a "$PROJECT_DIR/addon.json" "$INSTALL_DIR/addon.json"

find "$INSTALL_DIR/web" \
  -mindepth 1 \
  -maxdepth 1 \
  ! -name live \
  -exec rm -rf {} +

find "$PROJECT_DIR/web" \
  -mindepth 1 \
  -maxdepth 1 \
  ! -name live \
  -exec cp -a {} "$INSTALL_DIR/web/" \;

# The Console/browser can keep static addon assets cached even when files on
# disk have changed. Rewrite the deployed asset query strings on every update
# so UI fixes are loaded immediately after the addon page is reloaded.
UI_CACHE_BUST="$(date +%s)"
DEPLOYED_INDEX="$INSTALL_DIR/web/index.html"
if [[ -f "$DEPLOYED_INDEX" ]]; then
  python3 - "$DEPLOYED_INDEX" "$UI_CACHE_BUST" <<'PY_CACHE_BUST'
import re
import sys
from pathlib import Path

index_path = Path(sys.argv[1])
token = sys.argv[2]
text = index_path.read_text(encoding="utf-8")

for asset in ("style.css", "dune-addon-bridge.js", "app.js"):
    pattern = rf'(\./{re.escape(asset)})(?:\?v=[^"\']*)?'
    text, count = re.subn(pattern, lambda match: f"{match.group(1)}?v={token}", text)
    if count != 1:
        raise SystemExit(f"[ERROR] Expected exactly one {asset} reference in {index_path}, found {count}")

index_path.write_text(text, encoding="utf-8")
PY_CACHE_BUST
  echo "[OK] UI cache-bust token: $UI_CACHE_BUST"
fi

# Permission approval is intentionally left to Dune Docker Console.
# Do not edit RedBlink addon state from the addon updater.

sudo systemctl restart "$SERVICE_NAME"

echo "[OK] Updated."
echo "[OK] Preserved configuration: $CONFIG_FILE"
echo "[OK] Preserved database: ${DB_PATH:-$PROJECT_DIR/data/chat.sqlite3}"
echo "[OK] Addon permission state left unchanged; manage players:read in Dune Docker Console."
