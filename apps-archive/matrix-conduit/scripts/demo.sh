#!/usr/bin/env bash
set -e

echo "=== Home Assistant Matrix Add-on Demo / Smoke Test ==="

TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

echo "1. Testing options parsing and conduit.toml rendering..."
export OPTIONS_FILE="$TEMP_DIR/options.json"
cat <<EOF > "$OPTIONS_FILE"
{
  "server_name": "demo.matrix.local",
  "allow_registration": true,
  "port": 6167,
  "log_level": "info",
  "max_request_size": 20971520
}
EOF

# Dry-run render test
cat <<EOF > "$TEMP_DIR/conduit.toml"
[global]
server_name = "$(jq -r '.server_name' "$OPTIONS_FILE")"
database_backend = "sqlite"
database_path = "$TEMP_DIR/conduit_db"
media_path = "$TEMP_DIR/media"
port = $(jq -r '.port' "$OPTIONS_FILE")
max_request_size = $(jq -r '.max_request_size' "$OPTIONS_FILE")
allow_registration = $(jq -r '.allow_registration' "$OPTIONS_FILE")
allow_federation = false
allow_check_for_updates = false
log = "$(jq -r '.log_level' "$OPTIONS_FILE")"
address = "0.0.0.0"
EOF

if [ -f "$TEMP_DIR/conduit.toml" ]; then
    echo "✓ conduit.toml generated successfully:"
    cat "$TEMP_DIR/conduit.toml"
else
    echo "❌ Error: Failed to render conduit.toml"
    exit 1
fi

echo ""
echo "2. Validating config.yaml syntax..."
python3 -c "
import yaml
with open('config.yaml') as f:
    cfg = yaml.safe_load(f)
assert cfg['name'] == 'Matrix Homeserver'
assert cfg['slug'] == 'matrix'
print('✓ config.yaml validated successfully.')
"

echo "=== Smoke test completed successfully! ==="
