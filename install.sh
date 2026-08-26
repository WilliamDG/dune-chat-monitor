#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
DEFAULT_QUEUE="dune.chat.monitor"
DEFAULT_USER="dune_chat_monitor"
DEFAULT_EXCHANGE="chat.intercept"

say() { printf '%s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

detect_invoker_home() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    getent passwd "$SUDO_USER" | cut -d: -f6
  else
    printf '%s\n' "$HOME"
  fi
}

detect_dune_root() {
  if [[ -n "${DUNE_ROOT:-}" && -d "${DUNE_ROOT}" ]]; then
    printf '%s\n' "$DUNE_ROOT"
    return
  fi

  local invoker_home
  invoker_home="$(detect_invoker_home)"

  local candidates=(
    "$invoker_home/dune-awakening-selfhost-docker"
    "/home/ubuntu/dune-awakening-selfhost-docker"
  )

  local c
  for c in "${candidates[@]}"; do
    if [[ -f "$c/VERSION" && -d "$c/console" ]]; then
      printf '%s\n' "$c"
      return
    fi
  done

  while IFS= read -r c; do
    if [[ -f "$c/VERSION" && -d "$c/console" ]]; then
      printf '%s\n' "$c"
      return
    fi
  done < <(find /home -maxdepth 2 -type d -name dune-awakening-selfhost-docker 2>/dev/null | head -20)

  return 1
}

detect_rmq_container() {
  if docker inspect dune-rmq-game >/dev/null 2>&1; then
    printf '%s\n' "dune-rmq-game"
    return
  fi

  docker ps -a --format '{{.Names}}' \
    | grep -Ei 'rmq.*game|game.*rmq|rabbit.*game' \
    | head -1
}

detect_network() {
  local container="$1"
  docker inspect "$container" \
    --format '{{range $name,$cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
    | grep -v '^$' \
    | grep -E '^dune-net$' \
    | head -1 \
    || true
}

random_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
  fi
}

prompt_value() {
  local prompt="$1"
  local default="$2"
  local value=""
  if [[ "${YES:-0}" == "1" ]]; then
    printf '%s\n' "$default"
    return
  fi
  read -r -p "$prompt [$default]: " value
  printf '%s\n' "${value:-$default}"
}

YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

require_cmd docker
require_cmd python3

docker info >/dev/null 2>&1 || die "Docker is not accessible. Run with sudo or grant this user Docker access."

DUNE_ROOT="$(detect_dune_root || true)"
[[ -n "$DUNE_ROOT" ]] || die "Could not locate dune-awakening-selfhost-docker. Set DUNE_ROOT=/path/to/install."

RMQ_CONTAINER="$(detect_rmq_container || true)"
[[ -n "$RMQ_CONTAINER" ]] || die "Could not locate the game RabbitMQ container."

docker inspect "$RMQ_CONTAINER" >/dev/null 2>&1 || die "RabbitMQ container '$RMQ_CONTAINER' does not exist."

DUNE_NETWORK="$(detect_network "$RMQ_CONTAINER")"
[[ -n "$DUNE_NETWORK" ]] || die "Could not detect dune-net on $RMQ_CONTAINER."

docker exec "$RMQ_CONTAINER" rabbitmqctl list_exchanges -p / name \
  | grep -qx "$DEFAULT_EXCHANGE" \
  || die "RabbitMQ exchange '$DEFAULT_EXCHANGE' was not found."

docker exec "$RMQ_CONTAINER" sh -lc 'command -v rabbitmqadmin >/dev/null' \
  || die "rabbitmqadmin is not available in $RMQ_CONTAINER."

DUNE_VERSION="$(cat "$DUNE_ROOT/VERSION" 2>/dev/null || printf 'unknown')"

say
say "Dune Chat Monitor Installer"
say "----------------------------------------"
ok "Dune installation: $DUNE_ROOT"
ok "Dune version: $DUNE_VERSION"
ok "RabbitMQ container: $RMQ_CONTAINER"
ok "Docker network: $DUNE_NETWORK"
ok "Chat exchange: $DEFAULT_EXCHANGE"
say

SERVER_NAME="${SERVER_NAME:-$(prompt_value "Server name" "Dune Server")}"
TIMEZONE="${TIMEZONE:-$(prompt_value "Timezone" "UTC")}"
CHAT_RETENTION_DAYS="${CHAT_RETENTION_DAYS:-$(prompt_value "Retention days" "30")}"

[[ "$CHAT_RETENTION_DAYS" =~ ^[0-9]+$ ]] || die "Retention days must be an integer."
(( CHAT_RETENTION_DAYS >= 1 )) || die "Retention days must be at least 1."

RMQ_QUEUE="${RMQ_QUEUE:-$DEFAULT_QUEUE}"
RMQ_USER="${RMQ_USER:-$DEFAULT_USER}"

ENV_FILE="$PROJECT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

RMQ_PASSWORD="${RMQ_PASSWORD:-$(random_password)}"

cat > "$ENV_FILE" <<EOF
SERVER_NAME=$SERVER_NAME
TIMEZONE=$TIMEZONE
CHAT_RETENTION_DAYS=$CHAT_RETENTION_DAYS
CHAT_EXPORT_LIMIT=250

RMQ_CONTAINER=$RMQ_CONTAINER
DUNE_NETWORK=$DUNE_NETWORK
RMQ_QUEUE=$RMQ_QUEUE
RMQ_USER=$RMQ_USER
RMQ_PASSWORD=$RMQ_PASSWORD
RMQ_EXCHANGE=$DEFAULT_EXCHANGE

DUNE_ROOT=$DUNE_ROOT
ADDON_LIVE_DIR=$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/web/live
EOF
chmod 600 "$ENV_FILE"

say
say "Configuring isolated RabbitMQ resources..."

if docker exec "$RMQ_CONTAINER" rabbitmqctl list_users -q \
  | awk '{print $1}' \
  | grep -qx "$RMQ_USER"; then
  docker exec "$RMQ_CONTAINER" rabbitmqctl change_password "$RMQ_USER" "$RMQ_PASSWORD" >/dev/null
else
  docker exec "$RMQ_CONTAINER" rabbitmqctl add_user "$RMQ_USER" "$RMQ_PASSWORD" >/dev/null
fi

docker exec "$RMQ_CONTAINER" rabbitmqadmin \
  --host=127.0.0.1 \
  --port=15672 \
  --username=guest \
  --password=guest \
  -V / \
  declare queue \
  name="$RMQ_QUEUE" \
  durable=true \
  auto_delete=false >/dev/null

docker exec "$RMQ_CONTAINER" rabbitmqadmin \
  --host=127.0.0.1 \
  --port=15672 \
  --username=guest \
  --password=guest \
  -V / \
  declare binding \
  source="$DEFAULT_EXCHANGE" \
  destination_type=queue \
  destination="$RMQ_QUEUE" \
  routing_key='#' >/dev/null

docker exec "$RMQ_CONTAINER" rabbitmqctl \
  set_permissions -p / \
  "$RMQ_USER" \
  '^$' \
  '^$' \
  "^${RMQ_QUEUE//./\\.}$" >/dev/null

ok "Dedicated RabbitMQ queue created: $RMQ_QUEUE"
ok "Collector account has read-only access to its own queue"

INSTALL_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"

say
say "Installing addon UI..."

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -a "$PROJECT_DIR/addon.json" "$INSTALL_DIR/"
cp -a "$PROJECT_DIR/web" "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/web/live"

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

state[addon_id] = {
    "enabled": True,
    "approvedPermissions": []
}

state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

ok "Addon installed and enabled"

say
say "Building and starting collector..."
cd "$PROJECT_DIR"
docker compose -f compose.yml up -d --build

sleep 2

if docker inspect -f '{{.State.Running}}' "$ADDON_ID" 2>/dev/null | grep -qx true; then
  ok "Collector container is running"
else
  docker compose -f compose.yml ps
  die "Collector failed to start. Run ./doctor.sh and docker logs dune-chat-monitor."
fi

say
say "Installation completed."
say "Refresh Dune Docker Console and open Addons -> Dune Chat Monitor."
