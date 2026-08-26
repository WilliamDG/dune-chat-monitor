#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
SERVICE_NAME="dune-chat-monitor.service"
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
  ok "Configuration: $CONFIG_FILE"
else
  bad "Configuration not found; run ./install.sh first"
fi

docker info >/dev/null 2>&1 \
  && ok "Docker accessible" \
  || bad "Docker not accessible"

if [[ -n "${DUNE_ROOT:-}" && -f "$DUNE_ROOT/VERSION" ]]; then
  ok "Dune install: $DUNE_ROOT ($(cat "$DUNE_ROOT/VERSION" 2>/dev/null))"
else
  bad "Dune installation not found from configuration"
fi

if [[ -n "${TEXT_ROUTER_CONTAINER:-}" ]] \
  && docker inspect "$TEXT_ROUTER_CONTAINER" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "$TEXT_ROUTER_CONTAINER" 2>/dev/null)" == "true" ]]; then
    ok "Text Router running: $TEXT_ROUTER_CONTAINER"
  else
    bad "Text Router exists but is not running"
  fi
else
  bad "Text Router container not found"
fi

if [[ -n "${TEXT_ROUTER_CONTAINER:-}" ]] \
  && docker logs --tail 1 "$TEXT_ROUTER_CONTAINER" >/dev/null 2>&1; then
  ok "Text Router logs readable by current user"
else
  bad "Cannot read Text Router logs"
fi

if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  ok "Collector service active"
else
  bad "Collector service not active"
fi

if [[ -n "${DUNE_ROOT:-}" \
   && -f "$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/addon.json" ]]; then
  ok "Addon UI installed"
else
  bad "Addon UI not installed"
fi

if [[ -n "${EXPORT_DIR:-}" && -f "$EXPORT_DIR/status.json" ]]; then
  ok "Collector status export exists"

  python3 - "$EXPORT_DIR/status.json" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
    print("     source:", data.get("source"))
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

if [[ -n "${DB_PATH:-}" && -f "$DB_PATH" ]]; then
  ok "Private SQLite database: $DB_PATH"
else
  warn "SQLite database not created yet"
fi

echo "----------------------------------------"

if [[ "$FAIL" -eq 0 ]]; then
  echo "Doctor completed: no critical problems found."
else
  echo "Doctor completed: one or more checks failed."
fi

exit "$FAIL"
