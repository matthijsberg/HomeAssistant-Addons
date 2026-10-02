#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Intel Arrow Lake Ollama Add-on..."

# --- 0. Environment Setup ---
export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu:/usr/lib:${LD_LIBRARY_PATH}"
export OLLAMA_HOST="0.0.0.0:11434"
export OLLAMA_MODELS="/share/ollama_models"

# Load Config
KEEP_ALIVE=$(bashio::config 'keep_alive')
export OLLAMA_KEEP_ALIVE="$KEEP_ALIVE"

# --- 1. Hardware Check ---
if clinfo -l | grep -i "Intel" | grep -q "GPU"; then
    GPU_NAME=$(clinfo | grep "Device Name" | head -n 1 | awk -F: '{print $2}' | xargs)
    bashio::log.info "Intel GPU detected: $GPU_NAME"
    
    # Force driver visibility
    export ZES_ENABLE_SYSMAN=1
    export OLLAMA_INTEL_GPU=1
    
    # Arrow Lake optimization
    export SYCL_CACHE_PERSISTENT=1
else
    bashio::log.warning "ATTENTION: No Intel GPU detected. Falling back to CPU (slow)."
fi

# --- 2. Start Ollama (Background) ---
bashio::log.info "Starting Ollama server..."
ollama serve &
PID=$!

# Wait for API to be ready
bashio::log.info "Waiting for Ollama API..."
until curl -s -f "http://localhost:11434/" > /dev/null; do
    sleep 1
done
bashio::log.info "Ollama is online!"

# --- 3. Model Management ---
for model in $(bashio::config 'models'); do
    bashio::log.info "Checking model: $model"
    
    if ollama list | grep -q "$model"; then
        bashio::log.info "Model '$model' already present."
    else
        bashio::log.info "Downloading '$model'. This may take a while..."
        ollama pull "$model"
        bashio::log.info "Download of '$model' complete."
    fi
done

# --- 4. Keep Running ---
bashio::log.info "Ready to serve. Keep-alive set to: $KEEP_ALIVE"
wait "$PID"