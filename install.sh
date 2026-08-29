#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_ID="dune-chat-monitor"
SERVICE_NAME="dune-chat-monitor.service"

CONFIG_DIR="$PROJECT_DIR/config"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
CONFIG_FILE="$CONFIG_DIR/dune-chat-monitor.env"

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

YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

require_cmd docker
require_cmd python3
require_cmd sudo
require_cmd systemctl

docker info >/dev/null 2>&1 \
  || die "Docker is not accessible for this user."

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR"
chmod 700 "$CONFIG_DIR"
chmod 750 "$DATA_DIR" "$LOG_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
  # Load existing non-secret values from earlier installs.
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

DUNE_ROOT="$(detect_dune_root || true)"
[[ -n "$DUNE_ROOT" ]] \
  || die "Could not locate dune-awakening-selfhost-docker. Set DUNE_ROOT=/path/to/install."

TEXT_ROUTER_CONTAINER="${TEXT_ROUTER_CONTAINER:-dune-text-router}"

docker inspect "$TEXT_ROUTER_CONTAINER" >/dev/null 2>&1 \
  || die "Text Router container '$TEXT_ROUTER_CONTAINER' does not exist."

DUNE_VERSION="$(cat "$DUNE_ROOT/VERSION" 2>/dev/null || printf 'unknown')"

say
say "Dune Chat Monitor Installer"
say "----------------------------------------"
ok "Project: $PROJECT_DIR"
ok "Dune installation: $DUNE_ROOT"
ok "Dune version: $DUNE_VERSION"
ok "Chat source: Docker logs from $TEXT_ROUTER_CONTAINER"
say

CHAT_RETENTION_DAYS="${CHAT_RETENTION_DAYS:-30}"
CHAT_PAGE_SIZE="${CHAT_PAGE_SIZE:-50}"
CHAT_BOOTSTRAP_SINCE="${CHAT_BOOTSTRAP_SINCE:-1h}"

[[ "$CHAT_RETENTION_DAYS" =~ ^[0-9]+$ ]] \
  || die "Retention days must be an integer."
(( CHAT_RETENTION_DAYS >= 1 )) \
  || die "Retention days must be at least 1."

INSTALL_DIR="$DUNE_ROOT/runtime/addons/installed/$ADDON_ID"
ADDON_LIVE_DIR="$INSTALL_DIR/web/live"

[[ -f "$INSTALL_DIR/addon.json" ]] || die "Dune Chat Monitor is not installed in Dune Docker Console. Install the Community Addon UI first, then run this companion installer."

{
  printf 'CHAT_RETENTION_DAYS=%s\n' "$(env_quote "$CHAT_RETENTION_DAYS")"
  printf 'CHAT_PAGE_SIZE=%s\n' "$(env_quote "$CHAT_PAGE_SIZE")"
  printf 'CHAT_BOOTSTRAP_SINCE=%s\n' "$(env_quote "$CHAT_BOOTSTRAP_SINCE")"
  printf '\n'
  printf 'TEXT_ROUTER_CONTAINER=%s\n' "$(env_quote "$TEXT_ROUTER_CONTAINER")"
  printf 'DOCKER_BIN=%s\n' "$(env_quote "$(command -v docker)")"
  printf '\n'
  printf 'DUNE_ROOT=%s\n' "$(env_quote "$DUNE_ROOT")"
  printf 'DB_PATH=%s\n' "$(env_quote "$PROJECT_DIR/data/chat.sqlite3")"
  printf 'EXPORT_DIR=%s\n' "$(env_quote "$ADDON_LIVE_DIR")"
} > "$CONFIG_FILE"

chmod 600 "$CONFIG_FILE"
ok "Private/local configuration: $CONFIG_FILE"
ok "No RabbitMQ credentials are stored"

say
say "Preparing companion collector..."
ok "Console addon detected: $INSTALL_DIR"
ok "Console-owned addon files and Console addon state will not be modified"
say "Enable the addon and manage players:read only from Dune Docker Console."
say

SERVICE_USER="$(stat -c '%U' "$PROJECT_DIR")"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
PYTHON_BIN="$(command -v python3)"

say
say "Installing systemd collector..."

sudo tee "/etc/systemd/system/$SERVICE_NAME" >/dev/null <<EOF
[Unit]
Description=Dune Chat Monitor
After=docker.service network-online.target
Wants=docker.service network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$CONFIG_FILE
ExecStart=$PYTHON_BIN $PROJECT_DIR/collector/collector.py
Restart=on-failure
RestartSec=2
TimeoutStopSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

sleep 3

if systemctl is-active --quiet "$SERVICE_NAME"; then
  ok "Collector service is running"
else
  sudo systemctl status "$SERVICE_NAME" --no-pager || true
  die "Collector failed to start."
fi

say
say "Companion collector installation completed."
say "The collector only follows Text Router logs while the Console addon is installed and enabled."
say "If the addon is disabled, collection pauses; if it is uninstalled, the collector exits cleanly."
say "Approve players:read in Dune Docker Console if identity enrichment is desired."
say
say "Collector source: $TEXT_ROUTER_CONTAINER Docker logs"
say "RabbitMQ changes: none"
say "Collector Dune database access: none"
say "UI player identity source: RedBlink addon permission bridge (players:read)"
