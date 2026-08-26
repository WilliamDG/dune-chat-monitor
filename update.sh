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

# v0.2.0 no longer uses a configured server display name.
if grep -q '^SERVER_NAME=' "$CONFIG_FILE" 2>/dev/null; then
  sed -i '/^SERVER_NAME=/d' "$CONFIG_FILE"
  echo "[OK] Removed obsolete SERVER_NAME from local configuration."
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

# Local/manual installs explicitly approve the read-only player permission used
# to enrich Funcom chat identities with character name and Steam platform ID.
python3 - "$DUNE_ROOT" "$ADDON_ID" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
addon_id = sys.argv[2]
state_path = root / "runtime" / "addons" / "state.json"
state_path.parent.mkdir(parents=True, exist_ok=True)

try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}

previous = state.get(addon_id, {})
if not isinstance(previous, dict):
    previous = {}

state[addon_id] = {
    **previous,
    "enabled": previous.get("enabled", True),
    "approvedPermissions": ["players:read"]
}

state_path.write_text(
    json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8"
)
PY

sudo systemctl restart "$SERVICE_NAME"

echo "[OK] Updated."
echo "[OK] Preserved configuration: $CONFIG_FILE"
echo "[OK] Preserved database: ${DB_PATH:-$PROJECT_DIR/data/chat.sqlite3}"
echo "[OK] Approved addon permission: players:read"
