#!/usr/bin/env bash
set -Eeuo pipefail

# Removes only this relay. It does not edit Home Assistant's OCPP integration.
# Usage: bash uninstall.sh HOME_ASSISTANT_IP [CHARGE_POINT_ID] [HA_OCPP_PORT]

if [[ $# -lt 1 || $# -gt 3 ]]; then
  printf 'Usage: %s HOME_ASSISTANT_IP [CHARGE_POINT_ID] [HA_OCPP_PORT]\n' "$0" >&2
  exit 2
fi

HOME_ASSISTANT_IP="$1"
CHARGE_POINT_ID="${2:-central}"
HA_OCPP_PORT="${3:-9000}"
SLUG="local_ocular_ocpp_compatibility_relay"
TARGET_DIR="/addons/ocular_ocpp_compatibility_relay"

if ha apps info "$SLUG" >/dev/null 2>&1; then
  ha apps stop "$SLUG" >/dev/null 2>&1 || true
  ha apps uninstall "$SLUG"
fi
rm -rf "$TARGET_DIR"
ha supervisor reload

cat <<EOF
The compatibility relay has been removed.

To return to a direct connection, set the charger to:
  Protocol: WS
  Server:   $HOME_ASSISTANT_IP:$HA_OCPP_PORT/$CHARGE_POINT_ID
  Charger ID: $CHARGE_POINT_ID
  Authentication: blank, unless you configured it yourself
  Mode: Online

Home Assistant's OCPP integration was not changed by this uninstaller.
EOF
