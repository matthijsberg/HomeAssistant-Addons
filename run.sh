#!/usr/bin/env bash
set -e

export PYTHONUNBUFFERED=1
export TZ="${TZ:-Europe/Amsterdam}"

echo "=========================================================="
echo " Starting Mantis Security Agent (Home Assistant Add-on)   "
echo "=========================================================="

mkdir -p /data/cache /data/reports /data/workspaces

OPTIONS_FILE="/data/options.json"
TOKEN_FILE="/data/bearer_token.txt"

CONFIGURED_TOKEN=""
if [ -f "$OPTIONS_FILE" ]; then
    CONFIGURED_TOKEN=$(jq -r '.bearer_token // empty' "$OPTIONS_FILE")
fi

if [ -n "$CONFIGURED_TOKEN" ]; then
    export MANTIS_BEARER_TOKEN="$CONFIGURED_TOKEN"
    echo "[Mantis] Using configured bearer token from options."
else
    if [ ! -f "$TOKEN_FILE" ]; then
        python3 -c "import secrets; print(secrets.token_hex(32))" > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        echo "[Mantis] Generated fresh persistent bearer token."
        echo "[Mantis] =========================================================="
        echo "[Mantis] Bearer Token: $(cat "$TOKEN_FILE")"
        echo "[Mantis] =========================================================="
    fi
    export MANTIS_BEARER_TOKEN="$(cat "$TOKEN_FILE")"
fi

# Pre-flight bytecode compilation
python3 -m py_compile server.py

echo "[Mantis] Launching FastAPI MCP & Ingress daemon on 0.0.0.0:8000..."
exec python3 -u server.py
