#!/bin/bash

# Enable strict mode
set -e

echo "===================================================="
echo "          Starting HA AI Addons: Ollama             "
echo "===================================================="

# Check Hardware and give recommendations
echo "Analyzing system resources..."
python3 /check_hardware.py

# Retrieve configuration
if [ -f /data/options.json ]; then
    MODEL=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('model', '') or '')")
    CUSTOM_MODEL=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('custom_model', '') or '')")
    DEVICE_TYPE=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('device_type', 'NPU') or 'NPU')")
    KEEP_ALIVE=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('keep_alive', '5m') or '5m')")
    NUM_PARALLEL=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('num_parallel', 1))")
    MAX_LOADED_MODELS=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('max_loaded_models', 1))")
    NUM_CTX=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('num_ctx', 2048))")
    DEBUG=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('debug', False))")
    UPDATE_OLLAMA=$(python3 -c "import sys, json; print(json.load(open('/data/options.json')).get('update_ollama', False))")
else
    MODEL=${MODEL:-"gemma2:2b"}
    CUSTOM_MODEL=${CUSTOM_MODEL:-""}
    DEVICE_TYPE=${DEVICE_TYPE:-"NPU"}
    KEEP_ALIVE=${KEEP_ALIVE:-"5m"}
    NUM_PARALLEL=${NUM_PARALLEL:-1}
    MAX_LOADED_MODELS=${MAX_LOADED_MODELS:-1}
    NUM_CTX=${NUM_CTX:-2048}
    DEBUG=${DEBUG:-"False"}
    UPDATE_OLLAMA="False"
fi

# Normalize DEBUG to 1/0 for Ollama
if [ "$DEBUG" = "True" ]; then
    export OLLAMA_DEBUG="1"
else
    export OLLAMA_DEBUG="0"
fi

# Smart fallback: If NPU is selected/defaulted but no hardware found, switch to CPU
if [ "$DEVICE_TYPE" = "NPU" ] && [ ! -e "/dev/accel" ]; then
    echo "Warning: NPU selected but /dev/accel not found. Falling back to CPU."
    DEVICE_TYPE="CPU"
fi

# Set DEVICE for IPEX init
DEVICE="$DEVICE_TYPE"
# Map generic GPU to iGPU for IPEX init if needed
if [ "$DEVICE" = "GPU" ]; then
    DEVICE="iGPU"
fi

if [ ! -z "$CUSTOM_MODEL" ]; then
    echo "Using Custom Model: $CUSTOM_MODEL"
    MODEL=$CUSTOM_MODEL
else
    echo "Using Selected Model: $MODEL"
fi

# IPEX and Ollama Initialization
if [ "$DEBUG" = "True" ]; then
    echo "--- IPEX Initialization ---"
fi

if [ "$DEVICE_TYPE" = "CPU" ]; then
    echo "Running in CPU mode. Skipping IPEX GPU init."
else
    # Source the IPEX environment (this sets up SYCL and oneAPI paths)
    if [ "$DEBUG" = "True" ]; then
        . ipex-llm-init --gpu --device "$DEVICE" || echo "IPEX-LLM init failed, continuing anyway..."
    else
        . ipex-llm-init --gpu --device "$DEVICE" > /dev/null 2>&1 || echo "IPEX-LLM init failed, continuing anyway..."
    fi

    # Verify if SYCL actually sees a Level Zero device (Intel GPU)
    # If not, fallback to CPU to avoid crash
    if ! sycl-ls 2>/dev/null | grep -q "level_zero"; then
        echo "Warning: No Level Zero SYCL device found (sycl-ls). Falling back to CPU."
        DEVICE_TYPE="CPU"
    fi
fi

echo "--- Ollama Initialization ---"
# The IPEX image expects ollama to be initialized in a specific way
mkdir -p /llm/ollama
cd /llm/ollama
if [ ! -f "./ollama" ]; then
    echo "Initializing Ollama binary..."
    init-ollama || echo "init-ollama failed"
fi

# Optional: Update Ollama to latest version
if [ "$UPDATE_OLLAMA" = "True" ]; then
    echo "--- Updating Ollama ---"
    echo "Downloading and installing latest Ollama version..."
    # Install to default location (/usr/local/bin/ollama)
    curl -fsSL https://ollama.com/install.sh | sh || echo "Failed to download/install Ollama"
    
    if [ -f "/usr/local/bin/ollama" ]; then
        echo "Overwriting local Ollama binary with updated version..."
        cp /usr/local/bin/ollama ./ollama
        chmod +x ./ollama
        echo "Ollama updated successfully."
        ./ollama --version
    else
        echo "Warning: Updated Ollama binary not found at /usr/local/bin/ollama"
    fi
fi

# Hardware Diagnostics
if [ "$DEBUG" = "True" ]; then
    echo "--- Hardware Diagnostics ---"
    echo "Checking /dev/dri:"
    ls -l /dev/dri 2>/dev/null || echo "No /dev/dri found"
    echo "Checking /dev/accel:"
    ls -l /dev/accel 2>/dev/null || echo "No /dev/accel found"
    echo "SYCL Devices (sycl-ls):"
    sycl-ls 2>/dev/null || echo "sycl-ls not found or failed"
    echo "Environment for Ollama:"
    env | grep -E "OLLAMA|DEVICE|ZES|ONEAPI" || true
    echo "---------------------------"
fi

# Configure Ollama environment
export OLLAMA_HOST="0.0.0.0"
export OLLAMA_MODELS="/share/ollama/models"
export OLLAMA_ORIGINS="*"
export OLLAMA_KEEP_ALIVE="$KEEP_ALIVE"
export OLLAMA_NUM_PARALLEL="$NUM_PARALLEL"
export OLLAMA_MAX_LOADED_MODELS="$MAX_LOADED_MODELS"
export OLLAMA_NUM_CTX="$NUM_CTX"
# OLLAMA_DEBUG is already exported above

echo "Ollama Configuration:"
echo "  Keep-Alive: $OLLAMA_KEEP_ALIVE"
echo "  Num Parallel: $OLLAMA_NUM_PARALLEL"
echo "  Max Loaded Models: $OLLAMA_MAX_LOADED_MODELS"
echo "  Num Context: $OLLAMA_NUM_CTX"
echo "  Debug: $OLLAMA_DEBUG"

if [ "$DEVICE_TYPE" != "CPU" ]; then
    echo "Configuring environment for Intel $DEVICE_TYPE..."
    export OLLAMA_INTEL_GPU="1"
    export ZES_ENABLE_SYSMAN=1
    export DEVICE="$DEVICE_TYPE"
    export OLLAMA_NUM_GPU=999
    export ONEAPI_DEVICE_SELECTOR="level_zero:0"
else
    echo "Configuring environment for CPU..."
    # Ensure we don't accidentally force GPU
    unset OLLAMA_INTEL_GPU
    unset ONEAPI_DEVICE_SELECTOR
    unset OLLAMA_NUM_GPU
    unset ZES_ENABLE_SYSMAN
    # Reset DEVICE to CPU just in case
    export DEVICE="CPU"
fi

mkdir -p "$OLLAMA_MODELS"

# Start Ollama in background
echo "Starting Ollama Server..."
# Use the local ollama binary created by init-ollama
if [ "$DEBUG" = "True" ]; then
    ./ollama serve &
else
    # Filter out GIN access logs in non-debug mode
    ./ollama serve 2>&1 | grep -v "\[GIN\]" &
fi
PID=$!

# Wait for Ollama to start
echo "Waiting for Ollama API to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0
until curl -s http://localhost:11434/api/tags > /dev/null || [ $RETRY_COUNT -eq $MAX_RETRIES ]; do
    sleep 2
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo "Error: Ollama server failed to start within 60 seconds."
    exit 1
fi

echo "Ollama API is active!"

# Pull the requested model if not present
MODEL_READY=false
if ./ollama list | grep -q "$MODEL"; then
    echo "Model '$MODEL' is cached and ready."
    MODEL_READY=true
else
    echo "Model '$MODEL' not found. Downloading (this may take several minutes)..."
    ./ollama pull "$MODEL"
    
    # Check if model exists in list to confirm success
    if ./ollama list | grep -q "$MODEL"; then
        echo "Model '$MODEL' downloaded successfully."
        MODEL_READY=true
    else
        echo "Error: Failed to download model '$MODEL'."
        MODEL_READY=false
    fi
fi

echo "----------------------------------------------------"
if [ "$MODEL_READY" = "true" ]; then
    echo " Ollama is running and model '$MODEL' is loaded.    "
else
    echo " Ollama is running but model '$MODEL' FAILED to load."
    echo " Please check the logs for download errors."
fi
echo " Internal URL: http://ollama:11434                  "
echo "----------------------------------------------------"

# Start Web UI
echo "Starting Web UI..."
if ! python3 -c "import requests" 2>/dev/null; then
    echo "Warning: python3-requests not found. Attempting to install..."
    apt-get update && apt-get install -y python3-requests || echo "Failed to install requests"
fi

python3 -u /web_server.py 2>&1 &
WEB_PID=$!

# Wait for process
wait $PID $WEB_PID
