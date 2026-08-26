#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
FAIL=0

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
bad() { printf '[FAIL] %s\n' "$*"; FAIL=1; }

echo "Dune Chat Monitor Doctor"
echo "----------------------------------------"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  ok "Runtime configuration found"
else
  bad ".env not found; run ./install.sh first"
fi

docker info >/dev/null 2>&1 && ok "Docker accessible" || bad "Docker not accessible"

if [[ -n "${DUNE_ROOT:-}" && -f "$DUNE_ROOT/VERSION" ]]; then
  ok "Dune install: $DUNE_ROOT ($(cat "$DUNE_ROOT/VERSION" 2>/dev/null))"
else
  bad "Dune installation not found from runtime config"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] && docker inspect "$RMQ_CONTAINER" >/dev/null 2>&1; then
  ok "RabbitMQ container: $RMQ_CONTAINER"
else
  bad "RabbitMQ container missing"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] && docker exec "$RMQ_CONTAINER" rabbitmqctl list_exchanges -p / name 2>/dev/null | grep -qx "${RMQ_EXCHANGE:-chat.intercept}"; then
  ok "Chat exchange: ${RMQ_EXCHANGE:-chat.intercept}"
else
  bad "Chat exchange not found"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] && docker exec "$RMQ_CONTAINER" rabbitmqctl list_queues -p / name consumers messages_ready messages_unacknowledged 2>/dev/null | grep -E "^${RMQ_QUEUE:-dune.chat.monitor}[[:space:]]" >/dev/null; then
  ok "Collector queue exists"
else
  bad "Collector queue not found"
fi

if docker inspect "$ADDON_ID" >/dev/null 2>&1 && [[ "$(docker inspect -f '{{.State.Running}}' "$ADDON_ID" 2>/dev/null)" == "true" ]]; then
  ok "Collector container running"
else
  bad "Collector container not running"
fi

if [[ -n "${DUNE_ROOT:-}" && -f "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/addon.json" ]]; then
  ok "Addon UI installed"
else
  bad "Addon UI not installed"
fi

if [[ -n "${DUNE_ROOT:-}" && -f "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/web/live/status.json" ]]; then
  ok "Collector status export exists"
else
  warn "No status.json yet"
fi

if [[ -f "$PROJECT_DIR/data/chat.sqlite3" ]]; then
  ok "Private SQLite database exists"
else
  warn "SQLite database not created yet"
fi

echo "----------------------------------------"
[[ "$FAIL" -eq 0 ]] && echo "Doctor completed: no critical problems found." || echo "Doctor completed: one or more checks failed."
exit "$FAIL"
