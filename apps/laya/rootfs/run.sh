#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " Starting Laya Router - System 1 HA Decision Engine      "
echo "=========================================================="

OPTIONS_FILE="/data/options.json"

# Defaults
DEVICE="cpu"
THREADS="6"
CHECKPOINTS="english,multilingual"
ROUTER_DEFAULT="multilingual"
LAYA_REVISION="main"
API_KEY=""
LOG_LEVEL="info"

if [ -f "$OPTIONS_FILE" ]; then
    echo "Loading options from ${OPTIONS_FILE}..."
    DEVICE=$(jq -r '.device // "cpu"' "$OPTIONS_FILE")
    THREADS=$(jq -r '.threads // 6' "$OPTIONS_FILE")
    CHECKPOINTS=$(jq -r '.checkpoints // "english,multilingual"' "$OPTIONS_FILE")
    ROUTER_DEFAULT=$(jq -r '.router_default // "multilingual"' "$OPTIONS_FILE")
    LAYA_REVISION=$(jq -r '.laya_revision // "main"' "$OPTIONS_FILE")
    API_KEY=$(jq -r '.api_key // ""' "$OPTIONS_FILE")
    LOG_LEVEL=$(jq -r '.log_level // "info"' "$OPTIONS_FILE")
else
    echo "No ${OPTIONS_FILE} found; using environment variables or defaults."
    DEVICE="${LAYA_DEVICE:-$DEVICE}"
    THREADS="${LAYA_THREADS:-$THREADS}"
    CHECKPOINTS="${LAYA_CHECKPOINTS:-$CHECKPOINTS}"
    ROUTER_DEFAULT="${LAYA_ROUTER_DEFAULT:-$ROUTER_DEFAULT}"
    LAYA_REVISION="${LAYA_REVISION:-$LAYA_REVISION}"
    API_KEY="${LAYA_API_KEY:-$API_KEY}"
    LOG_LEVEL="${LAYA_LOG_LEVEL:-$LOG_LEVEL}"
fi

export LAYA_DEVICE="$DEVICE"
export LAYA_THREADS="$THREADS"
export LAYA_CHECKPOINTS="$CHECKPOINTS"
export LAYA_ROUTER_DEFAULT="$ROUTER_DEFAULT"
export LAYA_REVISION="$LAYA_REVISION"
export LAYA_API_KEY="$API_KEY"
export LAYA_LOG_LEVEL="$LOG_LEVEL"
export HF_HOME="/data/hf"

mkdir -p /data/hf

echo "Inference Device   : ${LAYA_DEVICE}"
echo "PyTorch Threads    : ${LAYA_THREADS}"
echo "Preload Checkpoints: ${LAYA_CHECKPOINTS}"
echo "Default Router     : ${LAYA_ROUTER_DEFAULT}"
echo "Weights Revision   : ${LAYA_REVISION}"
echo "Weights Storage    : ${HF_HOME}"
echo "Log Level          : ${LAYA_LOG_LEVEL}"
if [ -n "$API_KEY" ]; then
    echo "Auth Gate          : Bearer token authentication ENABLED"
else
    echo "Auth Gate          : ⚠️ WARNING - No API key configured; authentication DISABLED"
fi

cd /usr/src/app
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level "${LOG_LEVEL,,}"
