#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
CONFIG_FILE="$PROJECT_DIR/config/dune-chat-monitor.env"
FAIL=0

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
bad() { printf '[FAIL] %s\n' "$*"; FAIL=1; }

echo "Dune Chat Monitor Doctor"
echo "----------------------------------------"

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  ok "Runtime configuration: $CONFIG_FILE"
else
  bad "Runtime configuration not found; run ./install.sh first"
fi

docker info >/dev/null 2>&1 \
  && ok "Docker accessible" \
  || bad "Docker not accessible"

if [[ -n "${DUNE_ROOT:-}" && -f "$DUNE_ROOT/VERSION" ]]; then
  ok "Dune install: $DUNE_ROOT ($(cat "$DUNE_ROOT/VERSION" 2>/dev/null))"
else
  bad "Dune installation not found from runtime configuration"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] \
  && docker inspect "$RMQ_CONTAINER" >/dev/null 2>&1; then
  ok "RabbitMQ container: $RMQ_CONTAINER"
else
  bad "RabbitMQ container missing"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] \
  && docker exec "$RMQ_CONTAINER" rabbitmqctl list_exchanges -p / name 2>/dev/null \
    | grep -qx "${RMQ_EXCHANGE:-chat.intercept}"; then
  ok "Chat exchange: ${RMQ_EXCHANGE:-chat.intercept}"
else
  bad "Chat exchange not found"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] \
  && docker exec "$RMQ_CONTAINER" \
       rabbitmqctl list_queues -p / name consumers messages_ready messages_unacknowledged \
       2>/dev/null \
    | grep -E "^${RMQ_QUEUE:-dune.chat.monitor}[[:space:]]" >/dev/null; then

  QUEUE_LINE="$(
    docker exec "$RMQ_CONTAINER" \
      rabbitmqctl list_queues -p / name consumers messages_ready messages_unacknowledged \
      2>/dev/null \
      | grep -E "^${RMQ_QUEUE:-dune.chat.monitor}[[:space:]]" \
      | head -1
  )"

  ok "Collector queue: $QUEUE_LINE"
else
  bad "Collector queue not found"
fi

if [[ -n "${RMQ_CONTAINER:-}" ]] \
  && docker exec "$RMQ_CONTAINER" \
       rabbitmqctl list_user_permissions "${RMQ_USER:-dune_chat_monitor}" \
       2>/dev/null \
    | grep -F "${RMQ_USER:-dune_chat_monitor}" >/dev/null; then

  PERMS="$(
    docker exec "$RMQ_CONTAINER" \
      rabbitmqctl list_user_permissions "${RMQ_USER:-dune_chat_monitor}" \
      2>/dev/null \
      | tail -n +2 \
      | head -1
  )"

  ok "RabbitMQ permissions: $PERMS"
else
  bad "Collector RabbitMQ user/permissions missing"
fi

if docker inspect "$ADDON_ID" >/dev/null 2>&1 \
  && [[ "$(docker inspect -f '{{.State.Running}}' "$ADDON_ID" 2>/dev/null)" == "true" ]]; then
  ok "Collector container running"
else
  bad "Collector container not running"
fi

if [[ -n "${DUNE_ROOT:-}" \
   && -f "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/addon.json" ]]; then
  ok "Addon UI installed"
else
  bad "Addon UI not installed"
fi

if [[ -n "${DUNE_ROOT:-}" \
   && -f "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/web/live/status.json" ]]; then
  ok "Collector status export exists"

  python3 - \
    "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/web/live/status.json" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
    print("     connected:", data.get("collectorConnected"))
    print("     messages:", data.get("messageCount"))
    print("     last:", data.get("lastReceivedAt"))
    if data.get("error"):
        print("     error:", data.get("error"))
except Exception as exc:
    print("     could not parse status:", exc)
PY
else
  warn "No status.json yet"
fi

if [[ -f "$PROJECT_DIR/data/chat.sqlite3" ]]; then
  ok "Private SQLite database: $PROJECT_DIR/data/chat.sqlite3"
else
  warn "SQLite database not created yet"
fi

if [[ -d "$PROJECT_DIR/logs" ]]; then
  ok "Runtime log directory reserved: $PROJECT_DIR/logs"
fi

echo "----------------------------------------"

if [[ "$FAIL" -eq 0 ]]; then
  echo "Doctor completed: no critical problems found."
else
  echo "Doctor completed: one or more checks failed."
fi

exit "$FAIL"
