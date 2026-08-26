#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
SERVICE_NAME="dune-chat-monitor.service"
CONFIG_FILE="$PROJECT_DIR/config/dune-chat-monitor.env"
PURGE_DATA=0
PURGE_CONFIG=0

for arg in "$@"; do
  case "$arg" in
    --purge-data)
      PURGE_DATA=1
      ;;
    --purge-config)
      PURGE_CONFIG=1
      ;;
    --purge-all)
      PURGE_DATA=1
      PURGE_CONFIG=1
      ;;
    *)
      echo "[ERROR] Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

echo "Uninstalling Dune Chat Monitor..."

sudo systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
sudo rm -f "/etc/systemd/system/$SERVICE_NAME"
sudo systemctl daemon-reload

if [[ -n "${DUNE_ROOT:-}" ]]; then
  rm -rf "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"

  python3 - "$DUNE_ROOT" "$ADDON_ID" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
addon_id = sys.argv[2]
state_path = root / "runtime" / "addons" / "state.json"

if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    if isinstance(state, dict) and addon_id in state:
        state.pop(addon_id, None)
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
PY
fi

if [[ "$PURGE_DATA" -eq 1 ]]; then
  rm -f \
    "$PROJECT_DIR/data/chat.sqlite3" \
    "$PROJECT_DIR/data/chat.sqlite3-wal" \
    "$PROJECT_DIR/data/chat.sqlite3-shm"
  echo "[OK] Chat database removed."
else
  echo "[OK] Chat data preserved in $PROJECT_DIR/data"
fi

if [[ "$PURGE_CONFIG" -eq 1 ]]; then
  rm -f "$CONFIG_FILE"
  echo "[OK] Configuration removed."
else
  echo "[OK] Configuration preserved in $CONFIG_FILE"
fi

echo "[OK] Dune Chat Monitor uninstalled."
echo "[OK] RabbitMQ was not modified."
