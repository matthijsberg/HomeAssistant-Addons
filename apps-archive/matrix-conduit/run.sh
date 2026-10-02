#!/bin/sh
set -e

CONFIG_FILE="/etc/conduit/conduit.toml"
OPTIONS_FILE="/data/options.json"

mkdir -p /etc/conduit /data/conduit_db /data/media

# Default fallback options if not running inside HA Supervisor
SERVER_NAME="matrix.local"
ALLOW_REGISTRATION="false"
PORT="6167"
LOG_LEVEL="warn"
MAX_REQUEST_SIZE="20971520"

if [ -f "$OPTIONS_FILE" ]; then
    echo "[INFO] Loading Home Assistant Add-on options from $OPTIONS_FILE..."
    SERVER_NAME=$(jq -r '.server_name // "matrix.local"' "$OPTIONS_FILE")
    ALLOW_REGISTRATION=$(jq -r '.allow_registration // false' "$OPTIONS_FILE")
    PORT=$(jq -r '.port // 6167' "$OPTIONS_FILE")
    LOG_LEVEL=$(jq -r '.log_level // "warn"' "$OPTIONS_FILE")
    MAX_REQUEST_SIZE=$(jq -r '.max_request_size // 20971520' "$OPTIONS_FILE")
else
    echo "[WARN] $OPTIONS_FILE not found. Using default environment configuration."
fi

echo "[INFO] Rendering Conduit configuration file at $CONFIG_FILE..."

cat <<EOF > "$CONFIG_FILE"
[global]
server_name = "$SERVER_NAME"
database_backend = "sqlite"
database_path = "/data/conduit_db"
media_path = "/data/media"
port = $PORT
max_request_size = $MAX_REQUEST_SIZE
allow_registration = $ALLOW_REGISTRATION
allow_federation = false
allow_check_for_updates = false
log = "$LOG_LEVEL"
address = "0.0.0.0"
EOF

echo "[INFO] Starting Matrix Conduit Homeserver on port $PORT ($SERVER_NAME)..."
exec /usr/local/bin/conduit --config "$CONFIG_FILE"
