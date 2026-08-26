#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
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

compose() {
  if [[ -f "$CONFIG_FILE" ]]; then
    docker compose \
      --env-file "$CONFIG_FILE" \
      -f "$PROJECT_DIR/compose.yml" \
      "$@"
  else
    docker compose \
      -f "$PROJECT_DIR/compose.yml" \
      "$@"
  fi
}

echo "Uninstalling Dune Chat Monitor..."

cd "$PROJECT_DIR"
compose down --remove-orphans >/dev/null 2>&1 || true

if [[ -n "${RMQ_CONTAINER:-}" ]] \
  && docker inspect "$RMQ_CONTAINER" >/dev/null 2>&1; then

  if [[ -n "${RMQ_QUEUE:-}" ]]; then
    docker exec "$RMQ_CONTAINER" rabbitmqadmin \
      --host=127.0.0.1 \
      --port=15672 \
      --username=guest \
      --password=guest \
      -V / \
      delete queue \
      name="$RMQ_QUEUE" \
      >/dev/null 2>&1 || true
  fi

  if [[ -n "${RMQ_USER:-}" ]]; then
    docker exec "$RMQ_CONTAINER" \
      rabbitmqctl delete_user "$RMQ_USER" \
      >/dev/null 2>&1 || true
  fi
fi

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
  echo "[OK] Private configuration removed."
else
  echo "[OK] Private configuration preserved in $CONFIG_FILE"
fi

echo "[OK] Dune Chat Monitor uninstalled."
