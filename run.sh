#!/bin/sh
set -e

OPTIONS_FILE="/data/options.json"
CONFIG_PATH="/config/projects/energy-scheduler/config/heatpump_config.json"
AUTO_DISPATCH="true"
SYNC_INTERVAL="15"
LOG_LEVEL="info"

if [ -f "$OPTIONS_FILE" ]; then
    echo "[INFO] Loading Home Assistant Add-on options from $OPTIONS_FILE..."
    CONFIG_PATH=$(jq -r '.site_config // "/config/projects/energy-scheduler/config/heatpump_config.json"' "$OPTIONS_FILE")
    AUTO_DISPATCH=$(jq -r '.auto_dispatch // true' "$OPTIONS_FILE")
    SYNC_INTERVAL=$(jq -r '.sync_interval_minutes // 15' "$OPTIONS_FILE")
    LOG_LEVEL=$(jq -r '.log_level // "info"' "$OPTIONS_FILE")
fi

echo "[INFO] Open HEMS Add-on starting..."
echo "[INFO] Site config path: $CONFIG_PATH"
echo "[INFO] Auto dispatch: $AUTO_DISPATCH"
echo "[INFO] Sync interval: $SYNC_INTERVAL minutes"

export PYTHONPATH="/opt/open-hems:$(dirname "$0"):${PYTHONPATH}"

DAEMON_SCRIPT="/opt/open-hems/daemon.py"
if [ ! -f "$DAEMON_SCRIPT" ]; then
    DAEMON_SCRIPT="$(dirname "$0")/daemon.py"
fi

exec python3 "$DAEMON_SCRIPT" \
    --config "$CONFIG_PATH" \
    --interval "$SYNC_INTERVAL" \
    --port 8099
