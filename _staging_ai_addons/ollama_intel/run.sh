#!/usr/bin/env bashio

bashio::log.info "Starting Intel Arrow Lake Ollama Add-on..."

# --- 0. Environment Setup ---
export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu:/usr/lib:${LD_LIBRARY_PATH:-}"
export OLLAMA_HOST="0.0.0.0:11434"
export OLLAMA_MODELS="/share/ollama_models"
export OLLAMA_ORIGINS="*"

# Load Config
KEEP_ALIVE=$(bashio::config 'keep_alive')
NUM_PARALLEL=$(bashio::config 'num_parallel')
MAX_LOADED_MODELS=$(bashio::config 'max_loaded_models')
NUM_CTX=$(bashio::config 'num_ctx')
GPU_OVERHEAD=$(bashio::config 'gpu_overhead')
DEBUG=$(bashio::config 'debug')

export OLLAMA_KEEP_ALIVE="$KEEP_ALIVE"
export OLLAMA_NUM_PARALLEL="$NUM_PARALLEL"
export OLLAMA_MAX_LOADED_MODELS="$MAX_LOADED_MODELS"
export OLLAMA_NUM_CTX="$NUM_CTX"
export OLLAMA_GPU_OVERHEAD="$GPU_OVERHEAD"
# Normalize DEBUG
if [ "$DEBUG" = "true" ] || [ "$DEBUG" = "True" ]; then
    export OLLAMA_DEBUG="1"
else
    export OLLAMA_DEBUG="0"
fi

# --- 1. Hardware Check ---
if [ -d "/dev/dri" ]; then
    bashio::log.info "Intel GPU device files detected."
    
    # Force driver visibility
    export ZES_ENABLE_SYSMAN=1
    export OLLAMA_INTEL_GPU=1
    export SYCL_CACHE_PERSISTENT=1

    if [ "$OLLAMA_DEBUG" = "1" ]; then
        if clinfo -l | grep -i "Intel" | grep -q "GPU"; then
            GPU_NAME=$(clinfo | grep "Device Name" | head -n 1 | awk -F: '{print $2}' | xargs)
            bashio::log.info "OpenCL detected Intel GPU: $GPU_NAME"
        else
            bashio::log.warning "OpenCL did not detect Intel GPU, but /dev/dri exists. Proceeding with Level Zero."
        fi
    fi
else
    bashio::log.warning "ATTENTION: No Intel GPU detected (/dev/dri missing). Falling back to CPU (slow)."
fi

# --- 2. Start Ollama (Background) ---
bashio::log.info "Starting Ollama server..."
if [ "$OLLAMA_DEBUG" = "1" ]; then
    ollama serve &
else
    ollama serve 2>&1 | grep -v "\[GIN\]" &
fi
PID=$!

# Wait for API to be ready
bashio::log.info "Waiting for Ollama API..."
until curl -s -f "http://localhost:11434/" > /dev/null; do
    sleep 1
done
bashio::log.info "Ollama is online!"

# --- 3. Model Management ---
MODEL=$(bashio::config 'model')
bashio::log.info "Checking model: $MODEL"

if ollama list | grep -q "$MODEL"; then
    bashio::log.info "Model '$MODEL' already present."
else
    bashio::log.info "Downloading '$MODEL'. This may take a while..."
    ollama pull "$MODEL"
    bashio::log.info "Download of '$MODEL' complete."
fi

# --- 4. Keep Running ---
bashio::log.info "Ready to serve. Keep-alive set to: $KEEP_ALIVE"

# --- 5. Start Web UI ---
bashio::log.info "Starting Web UI..."
python3 -u /web_server.py 2>&1 &
WEB_PID=$!

wait "$PID" "$WEB_PID"