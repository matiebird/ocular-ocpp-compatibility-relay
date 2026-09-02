#!/usr/bin/env bash
set -Eeuo pipefail

# Install from an SSH session on the Home Assistant host.
# Usage: bash install.sh CHARGER_IP HOME_ASSISTANT_IP [CHARGE_POINT_ID] [HA_OCPP_PORT]
# Example: bash install.sh 192.168.1.50 192.168.1.10 central 9000

is_ipv4() {
  local ip="$1" part
  local -a parts
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  IFS=. read -r -a parts <<<"$ip"
  [[ ${#parts[@]} -eq 4 ]] || return 1
  for part in "${parts[@]}"; do
    (( 10#$part >= 0 && 10#$part <= 255 )) || return 1
  done
}

is_charge_point_id() {
  # URL-safe, bounded, and never a "." or ".." path segment.
  local id="$1"
  [[ "$id" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || return 1
  [[ "$id" != "." && "$id" != ".." ]] || return 1
}

is_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 ))
}

require_ipv4() {
  is_ipv4 "$1" || { printf 'Invalid %s IPv4 address: %s\n' "$2" "$1" >&2; exit 2; }
}

require_charge_point_id() {
  is_charge_point_id "$1" || {
    printf 'CHARGE_POINT_ID may contain only letters, numbers, dot, underscore and dash, and may not be "." or "..".\n' >&2
    exit 2
  }
}

require_port() {
  is_port "$1" || { printf 'HA_OCPP_PORT must be between 1 and 65535.\n' >&2; exit 2; }
}

render_installed_files() {
  # Rewrite the copied package for this installation. Every generated value is
  # written as a double-quoted YAML scalar so IDs such as 123, true, null or
  # 2026-01-01 stay strings instead of being retyped by the YAML parser.
  local target="$1" charger_ip="$2" charge_point_id="$3" ha_ocpp_port="$4"
  local expected_path="/${charge_point_id}/${charge_point_id}"
  sed -i \
    -e "s/^    - \"192.168.1.50\"$/    - \"$charger_ip\"/" \
    -e "s#\"/central/central\"#\"$expected_path\"#g" \
    -e "s/^  charge_point_id: \"central\"$/  charge_point_id: \"$charge_point_id\"/" \
    -e "s/^  upstream_port: 9000$/  upstream_port: $ha_ocpp_port/" \
    "$target/config.yaml"
  sed -i \
    -e "s/^  ocpp_device_id: \"central\"$/  ocpp_device_id: \"$charge_point_id\"/" \
    "$target/ha-timing-script.yaml"
  sed -i \
    -e "s/^\([[:space:]]*\)devid: \"central\"$/\1devid: \"$charge_point_id\"/" \
    "$target/examples/ocular-everyday-controls.yaml"
  # Fail closed if any substitution did not land, so rollback runs.
  grep -qF "    - \"$charger_ip\"" "$target/config.yaml" \
    && grep -qF "  upstream_path: \"$expected_path\"" "$target/config.yaml" \
    && grep -qF "    - \"$expected_path\"" "$target/config.yaml" \
    && grep -qF "  charge_point_id: \"$charge_point_id\"" "$target/config.yaml" \
    && grep -qF "  upstream_port: $ha_ocpp_port" "$target/config.yaml" \
    && grep -qF "  ocpp_device_id: \"$charge_point_id\"" "$target/ha-timing-script.yaml" \
    && grep -qF "devid: \"$charge_point_id\"" "$target/examples/ocular-everyday-controls.yaml"
}

# Test hook: "install.sh --render TARGET_DIR CHARGER_IP CHARGE_POINT_ID HA_OCPP_PORT"
# validates the inputs and rewrites an already-copied package exactly as an
# installation does, without touching Home Assistant.
if [[ "${1:-}" == "--render" ]]; then
  [[ $# -eq 5 ]] || {
    printf 'Usage: %s --render TARGET_DIR CHARGER_IP CHARGE_POINT_ID HA_OCPP_PORT\n' "$0" >&2
    exit 2
  }
  require_ipv4 "$3" charger
  require_charge_point_id "$4"
  require_port "$5"
  render_installed_files "$2" "$3" "$4" "$5"
  exit 0
fi

if [[ $# -lt 2 || $# -gt 4 ]]; then
  printf 'Usage: %s CHARGER_IP HOME_ASSISTANT_IP [CHARGE_POINT_ID] [HA_OCPP_PORT]\n' "$0" >&2
  exit 2
fi

CHARGER_IP="$1"
HOME_ASSISTANT_IP="$2"
CHARGE_POINT_ID="${3:-central}"
HA_OCPP_PORT="${4:-9000}"
EXPECTED_PATH="/${CHARGE_POINT_ID}/${CHARGE_POINT_ID}"
SLUG="local_ocular_ocpp_compatibility_relay"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="/addons/ocular_ocpp_compatibility_relay"
BACKUP_ROOT="/config/ocular-ocpp-relay-backups"
BACKUP_DIR=""
HAD_EXISTING_APP=0
INSTALL_PHASE="preflight"

rollback_on_error() {
  local status=$?
  trap - ERR
  set +e
  printf '\nInstallation failed. Attempting automatic rollback...\n' >&2
  if [[ "$INSTALL_PHASE" == "mutating" ]]; then
    ha apps stop "$SLUG" >/dev/null 2>&1 || true
    ha apps uninstall "$SLUG" >/dev/null 2>&1 || true
    rm -rf "$TARGET_DIR"
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
      cp -a "$BACKUP_DIR" "$TARGET_DIR"
      ha store reload >/dev/null 2>&1
      if (( HAD_EXISTING_APP )); then
        ha apps install "$SLUG" >/dev/null 2>&1 && ha apps start "$SLUG" >/dev/null 2>&1
        printf 'Previous relay restored and restart attempted.\n' >&2
      else
        printf 'Previous source restored. No previous running app existed.\n' >&2
      fi
    else
      ha store reload >/dev/null 2>&1
      printf 'Partial fresh installation removed.\n' >&2
    fi
  fi
  exit "$status"
}

trap rollback_on_error ERR

require_ipv4 "$CHARGER_IP" charger
require_ipv4 "$HOME_ASSISTANT_IP" "Home Assistant"
require_charge_point_id "$CHARGE_POINT_ID"
require_port "$HA_OCPP_PORT"
command -v ha >/dev/null || {
  printf 'Home Assistant CLI not found. Use an SSH session on a Home Assistant OS or Supervised host with the ha command available.\n' >&2
  exit 2
}
[[ -d /addons ]] || {
  printf '/addons is unavailable. This package requires Home Assistant OS or Supervised.\n' >&2
  exit 2
}
for required in config.yaml Dockerfile README.md CHARGER_CONTROL_WORKAROUNDS.md examples/README.md examples/ocular-everyday-controls.yaml examples/ocular-dashboard-card.yaml docs/images/connection-overview.svg docs/images/ocular-settings-reference.svg proxy/main.py proxy/server.py proxy/timing.py tests/test_server.py tests/test_timing.py; do
  [[ -f "$SOURCE_DIR/$required" ]] || { printf 'Package is incomplete: missing %s\n' "$required" >&2; exit 2; }
done

printf 'Checking Home Assistant OCPP listener at %s:%s...\n' "$HOME_ASSISTANT_IP" "$HA_OCPP_PORT"
if ! timeout 5 bash -c "</dev/tcp/$HOME_ASSISTANT_IP/$HA_OCPP_PORT" 2>/dev/null; then
  printf 'No OCPP listener answered at %s:%s. Start the Home Assistant OCPP integration first.\n' "$HOME_ASSISTANT_IP" "$HA_OCPP_PORT" >&2
  exit 1
fi

if [[ -e "$TARGET_DIR" ]]; then
  mkdir -p "$BACKUP_ROOT"
  BACKUP_DIR="$BACKUP_ROOT/ocular_ocpp_compatibility_relay-$(date +%Y%m%d-%H%M%S)"
  cp -a "$TARGET_DIR" "$BACKUP_DIR"
  printf 'Existing relay source backed up to %s\n' "$BACKUP_DIR"
fi

if ha apps info "$SLUG" >/dev/null 2>&1; then
  HAD_EXISTING_APP=1
fi

INSTALL_PHASE="mutating"

if (( HAD_EXISTING_APP )); then
  ha apps stop "$SLUG" >/dev/null 2>&1 || true
  ha apps uninstall "$SLUG"
fi

if timeout 3 bash -c "</dev/tcp/$HOME_ASSISTANT_IP/19000" 2>/dev/null; then
  printf 'Port 19000 is already in use. Stop the service using it, then run this installer again.\n' >&2
  exit 1
fi

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp -a \
  "$SOURCE_DIR/config.yaml" \
  "$SOURCE_DIR/Dockerfile" \
  "$SOURCE_DIR/install.sh" \
  "$SOURCE_DIR/uninstall.sh" \
  "$SOURCE_DIR/ha-timing-script.yaml" \
  "$SOURCE_DIR/README.md" \
  "$SOURCE_DIR/CHARGER_CONTROL_WORKAROUNDS.md" \
  "$SOURCE_DIR/docs" \
  "$SOURCE_DIR/examples" \
  "$SOURCE_DIR/proxy" \
  "$SOURCE_DIR/tests" \
  "$TARGET_DIR/"

render_installed_files "$TARGET_DIR" "$CHARGER_IP" "$CHARGE_POINT_ID" "$HA_OCPP_PORT"

ha store reload
ha apps install "$SLUG"
ha apps start "$SLUG"

READY=0
APP_INFO=""
APP_LOGS=""
for _ in $(seq 1 15); do
  APP_INFO="$(ha apps info "$SLUG" --raw-json 2>/dev/null || true)"
  APP_LOGS="$(ha apps logs "$SLUG" 2>&1 || true)"
  if grep -Eq '"state"[[:space:]]*:[[:space:]]*"started"' <<<"$APP_INFO" \
    && grep -q 'listening port=9000' <<<"$APP_LOGS"; then
    READY=1
    break
  fi
  sleep 1
done

if (( ! READY )); then
  printf 'Relay did not reach a verified started/listening state. Recent logs:\n%s\n' "$APP_LOGS" >&2
  false
fi

INSTALL_PHASE="complete"
trap - ERR

printf '\nInstalled relay status:\n'
ha apps info "$SLUG"

cat <<EOF

The relay is installed and starts automatically.

Now set the charger in OCPPSetTool:
  Protocol: WS
  Server:   $HOME_ASSISTANT_IP:19000/$CHARGE_POINT_ID
  Charger ID: $CHARGE_POINT_ID
  Authentication: blank, unless you configured it yourself
  Mode: Online (set this last)

The resulting charger path is $EXPECTED_PATH.
Home Assistant OCPP stays on port $HA_OCPP_PORT; it was not moved or edited.

Verify after the charger connects:
  ha apps logs $SLUG

Look for "connection_open" without a repeating "upstream_failure".

Automatic charger timing:
  After each charger connection, the relay applies HeartbeatInterval=60,
  WebSocketPingInterval=60 and MeterValueSampleInterval=10 through Home
  Assistant OCPP, then reads all three back. Look for "timing_verified".
  OCPP device ID: $CHARGE_POINT_ID
  The manual fallback $TARGET_DIR/ha-timing-script.yaml and the example
  package in $TARGET_DIR/examples already use this ID.
Any previous source backup is outside /addons at $BACKUP_ROOT so Supervisor cannot mistake it for another local app.
EOF
