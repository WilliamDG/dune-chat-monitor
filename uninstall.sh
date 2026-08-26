#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
PURGE_DATA=0

for arg in "$@"; do
  case "$arg" in
    --purge-data) PURGE_DATA=1 ;;
    *) echo "[ERROR] Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

if [[ -f "$PROJECT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
fi

cd "$PROJECT_DIR"
docker compose -f compose.yml down --remove-orphans >/dev/null 2>&1 || true

if [[ -n "${RMQ_CONTAINER:-}" ]] && docker inspect "$RMQ_CONTAINER" >/dev/null 2>&1; then
  if [[ -n "${RMQ_QUEUE:-}" ]]; then
    docker exec "$RMQ_CONTAINER" rabbitmqadmin \
      --host=127.0.0.1 --port=15672 \
      --username=guest --password=guest -V / \
      delete queue name="$RMQ_QUEUE" >/dev/null 2>&1 || true
  fi
  if [[ -n "${RMQ_USER:-}" ]]; then
    docker exec "$RMQ_CONTAINER" rabbitmqctl delete_user "$RMQ_USER" >/dev/null 2>&1 || true
  fi
fi

if [[ -n "${DUNE_ROOT:-}" ]]; then
  rm -rf "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"
fi

if [[ "$PURGE_DATA" -eq 1 ]]; then
  rm -rf "$PROJECT_DIR/data"
  mkdir -p "$PROJECT_DIR/data"
  touch "$PROJECT_DIR/data/.gitkeep"
  rm -f "$PROJECT_DIR/.env"
  echo "[OK] Runtime data and local configuration removed."
else
  echo "[OK] Chat data preserved in $PROJECT_DIR/data"
fi

echo "[OK] Dune Chat Monitor uninstalled."
