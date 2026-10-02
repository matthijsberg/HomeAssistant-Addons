#!/usr/bin/env bash
set -e

CONFIG_PATH=/data/options.json

# Extract config values using jq
export LOG_LEVEL=$(jq --raw-output '.log_level // "info"' $CONFIG_PATH)
# Use internal WebSocket URL by default
# If HA_TOKEN is not provided in config, use Supervisor Token and INTERNAL URL
if [ -z "$HA_TOKEN" ]; then
    echo "Auto-configuring Home Assistant Connection..."
    export HA_TOKEN=$SUPERVISOR_TOKEN
    # Force internal WebSocket URL when using Supervisor Token
    export HA_URL="ws://supervisor/core/websocket"
else
    # Manual mode: Use configured URL (or default if null)
    export HA_URL=$(jq --raw-output '.ha_url // "ws://supervisor/core/websocket"' $CONFIG_PATH)
fi

export BLUEBUBBLES_URL=$(jq --raw-output '.bluebubbles_url // empty' $CONFIG_PATH)
export BLUEBUBBLES_TOKEN=$(jq --raw-output '.bluebubbles_token // empty' $CONFIG_PATH)

export WHATSAPP_PROVIDER=$(jq --raw-output '.whatsapp_provider // "twilio"' $CONFIG_PATH)
export WHATSAPP_SID=$(jq --raw-output '.whatsapp_sid // empty' $CONFIG_PATH)
export WHATSAPP_TOKEN=$(jq --raw-output '.whatsapp_token // empty' $CONFIG_PATH)
export WHATSAPP_FROM=$(jq --raw-output '.whatsapp_from // empty' $CONFIG_PATH)

# --- Moltbot Setup ---

echo "Setting up Moltbot..."

# 1. Get Config
LLM_PROVIDER=$(jq -r '.llm_provider // "google-genai"' $CONFIG_PATH)
MODEL_NAME=$(jq -r '.model_name // "gemini-3.0-flash"' $CONFIG_PATH)

GEMINI_KEY=$(jq -r '.gemini_api_key // empty' $CONFIG_PATH)
OPENAI_KEY=$(jq -r '.openai_api_key // empty' $CONFIG_PATH)
ANTHROPIC_KEY=$(jq -r '.anthropic_api_key // empty' $CONFIG_PATH)
OLLAMA_URL=$(jq -r '.ollama_url // "http://localhost:11434"' $CONFIG_PATH)

# 2. Install Moltbot (global)
echo "Installing/Updating Moltbot..."
npm install -g clawdbot@latest

# 3. Configure Moltbot
MOLTBOT_DIR="/root/.clawdbot"
mkdir -p "$MOLTBOT_DIR"
CONFIG_FILE="$MOLTBOT_DIR/clawdbot.json"

# Always create or update the config file to prevent "Missing config" error
echo "Updating Moltbot configuration for provider: $LLM_PROVIDER..."
# Minimal config only
cat > "$CONFIG_FILE" <<EOF
{
  "gateway": {
    "mode": "local",
    "port": 18789
  },
  "logging": {
    "level": "info"
  }
}
EOF

# Set Environment Variables and Config based on Provider
case "$LLM_PROVIDER" in
  "google-genai")
    if [ -n "$GEMINI_KEY" ] && [ "$GEMINI_KEY" != "null" ]; then
        echo "Configuring for Google Gemini..."
        export GOOGLE_API_KEY="$GEMINI_KEY"
    else
        echo "WARNING: Google Provider selected but No Gemini API Key provided."
    fi
    ;;
  "openai")
    if [ -n "$OPENAI_KEY" ] && [ "$OPENAI_KEY" != "null" ]; then
        echo "Configuring for OpenAI..."
        export OPENAI_API_KEY="$OPENAI_KEY"
    else
        echo "WARNING: OpenAI Provider selected but No OpenAI API Key provided."
    fi
    ;;
  "anthropic")
    if [ -n "$ANTHROPIC_KEY" ] && [ "$ANTHROPIC_KEY" != "null" ]; then
        echo "Configuring for Anthropic..."
        export ANTHROPIC_API_KEY="$ANTHROPIC_KEY"
    else
        echo "WARNING: Anthropic Provider selected but No Anthropic API Key provided."
    fi
    ;;
  "ollama")
    echo "Configuring for Local Ollama..."
    # Standard Ollama env var, Moltbot should respect this
    export OLLAMA_BASE_URL="$OLLAMA_URL"
    ;;
  *)
    echo "Unknown provider: $LLM_PROVIDER. Defaulting to Google GenAI setup..."
    export GOOGLE_API_KEY="$GEMINI_KEY"
    ;;
esac

# 4. CLI Configuration (Safest way)
echo "Applying settings via CLI..."

# Construct the full model string (e.g., google-genai/gemini-3.0-flash)
# This appears to be the standard format based on logs (anthropic/claude-opus...)
FULL_MODEL="$LLM_PROVIDER/$MODEL_NAME"

echo "Setting agent model to: $FULL_MODEL"

# Set agent.model (Confirmed valid key)
clawdbot config set agent.model "$FULL_MODEL" || true

# 5. Start Moltbot in background
echo "Starting Moltbot Gateway..."
# Use --allow-unconfigured to ensure it starts even if some keys are missing initially
clawdbot gateway --port 18789 --allow-unconfigured &
MOLTBOT_PID=$!

# Wait for Moltbot to initialize
sleep 5

# --- Python Bridge Setup ---

echo "Starting Moltbot-HA Bridge Add-on with Log Level: $LOG_LEVEL"
python3 -u /app/bridge.py &
BRIDGE_PID=$!

# Wait for both
wait $MOLTBOT_PID $BRIDGE_PID
