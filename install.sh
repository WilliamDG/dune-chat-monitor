#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"

CONFIG_DIR="$PROJECT_DIR/config"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
CONFIG_FILE="$CONFIG_DIR/dune-chat-monitor.env"

DEFAULT_QUEUE="dune.chat.monitor"
DEFAULT_USER="dune_chat_monitor"
DEFAULT_EXCHANGE="chat.intercept"

say() { printf '%s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

env_quote() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//\$/\\$}"
  v="${v//\`/\\\`}"
  printf '"%s"' "$v"
}

compose() {
  docker compose \
    --env-file "$CONFIG_FILE" \
    -f "$PROJECT_DIR/compose.yml" \
    "$@"
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
  done < <(
    find /home /opt \
      -maxdepth 3 \
      -type d \
      -name dune-awakening-selfhost-docker \
      2>/dev/null \
      | head -20
  )

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
  local networks
  networks="$(
    docker inspect "$container" \
      --format '{{range $name,$cfg := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
  )"

  if grep -qx 'dune-net' <<<"$networks"; then
    printf '%s\n' 'dune-net'
    return
  fi

  grep -Ei 'dune|game' <<<"$networks" | head -1
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

docker info >/dev/null 2>&1 \
  || die "Docker is not accessible. Run with sudo or grant this user Docker access."

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR"
chmod 700 "$CONFIG_DIR"
chmod 750 "$DATA_DIR" "$LOG_DIR"

# Preserve existing installation values on reinstall.
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

DUNE_ROOT="$(detect_dune_root || true)"
[[ -n "$DUNE_ROOT" ]] \
  || die "Could not locate dune-awakening-selfhost-docker. Set DUNE_ROOT=/path/to/install."

RMQ_CONTAINER="${RMQ_CONTAINER:-$(detect_rmq_container || true)}"
[[ -n "$RMQ_CONTAINER" ]] \
  || die "Could not locate the game RabbitMQ container."

docker inspect "$RMQ_CONTAINER" >/dev/null 2>&1 \
  || die "RabbitMQ container '$RMQ_CONTAINER' does not exist."

DUNE_NETWORK="${DUNE_NETWORK:-$(detect_network "$RMQ_CONTAINER")}"
[[ -n "$DUNE_NETWORK" ]] \
  || die "Could not detect the Docker network used by $RMQ_CONTAINER."

RMQ_EXCHANGE="${RMQ_EXCHANGE:-$DEFAULT_EXCHANGE}"

docker exec "$RMQ_CONTAINER" rabbitmqctl list_exchanges -p / name \
  | grep -qx "$RMQ_EXCHANGE" \
  || die "RabbitMQ exchange '$RMQ_EXCHANGE' was not found."

docker exec "$RMQ_CONTAINER" sh -lc 'command -v rabbitmqadmin >/dev/null' \
  || die "rabbitmqadmin is not available in $RMQ_CONTAINER."

DUNE_VERSION="$(cat "$DUNE_ROOT/VERSION" 2>/dev/null || printf 'unknown')"

say
say "Dune Chat Monitor Installer"
say "----------------------------------------"
ok "Project: $PROJECT_DIR"
ok "Dune installation: $DUNE_ROOT"
ok "Dune version: $DUNE_VERSION"
ok "RabbitMQ container: $RMQ_CONTAINER"
ok "Docker network: $DUNE_NETWORK"
ok "Chat exchange: $RMQ_EXCHANGE"
say

SERVER_NAME="${SERVER_NAME:-$(prompt_value "Server name" "Dune Server")}"
TIMEZONE="${TIMEZONE:-$(prompt_value "Timezone" "UTC")}"
CHAT_RETENTION_DAYS="${CHAT_RETENTION_DAYS:-$(prompt_value "Retention days" "30")}"
CHAT_EXPORT_LIMIT="${CHAT_EXPORT_LIMIT:-250}"

[[ "$CHAT_RETENTION_DAYS" =~ ^[0-9]+$ ]] \
  || die "Retention days must be an integer."
(( CHAT_RETENTION_DAYS >= 1 )) \
  || die "Retention days must be at least 1."

RMQ_QUEUE="${RMQ_QUEUE:-$DEFAULT_QUEUE}"
RMQ_USER="${RMQ_USER:-$DEFAULT_USER}"
RMQ_PASSWORD="${RMQ_PASSWORD:-$(random_password)}"

ADDON_LIVE_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID/web/live"

{
  printf 'SERVER_NAME=%s\n' "$(env_quote "$SERVER_NAME")"
  printf 'TIMEZONE=%s\n' "$(env_quote "$TIMEZONE")"
  printf 'CHAT_RETENTION_DAYS=%s\n' "$(env_quote "$CHAT_RETENTION_DAYS")"
  printf 'CHAT_EXPORT_LIMIT=%s\n' "$(env_quote "$CHAT_EXPORT_LIMIT")"
  printf '\n'
  printf 'RMQ_CONTAINER=%s\n' "$(env_quote "$RMQ_CONTAINER")"
  printf 'DUNE_NETWORK=%s\n' "$(env_quote "$DUNE_NETWORK")"
  printf 'RMQ_QUEUE=%s\n' "$(env_quote "$RMQ_QUEUE")"
  printf 'RMQ_USER=%s\n' "$(env_quote "$RMQ_USER")"
  printf 'RMQ_PASSWORD=%s\n' "$(env_quote "$RMQ_PASSWORD")"
  printf 'RMQ_EXCHANGE=%s\n' "$(env_quote "$RMQ_EXCHANGE")"
  printf '\n'
  printf 'DUNE_ROOT=%s\n' "$(env_quote "$DUNE_ROOT")"
  printf 'ADDON_LIVE_DIR=%s\n' "$(env_quote "$ADDON_LIVE_DIR")"
} > "$CONFIG_FILE"

chmod 600 "$CONFIG_FILE"
ok "Private configuration: $CONFIG_FILE"

say
say "Configuring isolated RabbitMQ resources..."

if docker exec "$RMQ_CONTAINER" rabbitmqctl list_users -q \
  | awk '{print $1}' \
  | grep -qx "$RMQ_USER"; then
  docker exec "$RMQ_CONTAINER" \
    rabbitmqctl change_password "$RMQ_USER" "$RMQ_PASSWORD" \
    >/dev/null
else
  docker exec "$RMQ_CONTAINER" \
    rabbitmqctl add_user "$RMQ_USER" "$RMQ_PASSWORD" \
    >/dev/null
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
  auto_delete=false \
  >/dev/null

docker exec "$RMQ_CONTAINER" rabbitmqadmin \
  --host=127.0.0.1 \
  --port=15672 \
  --username=guest \
  --password=guest \
  -V / \
  declare binding \
  source="$RMQ_EXCHANGE" \
  destination_type=queue \
  destination="$RMQ_QUEUE" \
  routing_key='#' \
  >/dev/null

READ_REGEX="^${RMQ_QUEUE//./\\.}$"

docker exec "$RMQ_CONTAINER" rabbitmqctl \
  set_permissions -p / \
  "$RMQ_USER" \
  '^$' \
  '^$' \
  "$READ_REGEX" \
  >/dev/null

ok "Dedicated RabbitMQ queue: $RMQ_QUEUE"
ok "Collector account: configure=none, write=none, read=own queue only"

INSTALL_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"

say
say "Installing addon UI..."

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -a "$PROJECT_DIR/addon.json" "$INSTALL_DIR/"
cp -a "$PROJECT_DIR/web" "$INSTALL_DIR/"
mkdir -p "$ADDON_LIVE_DIR"

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
    "enabled": True,
    "approvedPermissions": []
}

state_path.write_text(
    json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8"
)
PY

ok "Addon UI installed and enabled"

say
say "Building and starting collector..."

cd "$PROJECT_DIR"
compose up -d --build

sleep 2

if docker inspect -f '{{.State.Running}}' "$ADDON_ID" 2>/dev/null \
  | grep -qx true; then
  ok "Collector container is running"
else
  compose ps
  die "Collector failed to start. Run ./doctor.sh and docker logs dune-chat-monitor."
fi

say
say "Installation completed."
say "Refresh Dune Docker Console and open Addons -> Dune Chat Monitor."
