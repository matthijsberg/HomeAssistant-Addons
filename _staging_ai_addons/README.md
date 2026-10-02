# HA AI Addons

A collection of Home Assistant Add-ons focused on bringing advanced AI capabilities to your smart home.

## Included Add-ons

1.  **Moltbot-HA Bridge**: A central intelligence bridge inspired by the Moltbot framework. Connects HA, Google, and Messaging platforms.
2.  **Ollama Local**: A local LLM server based on Ollama, with hardware-aware model recommendations.
3.  **Ollama (Universal XPU)**: Ollama LLM runner with Intel (SYCL/NPU), NVIDIA/AMD (Vulkan), and CPU support.

## Installation

1.  In Home Assistant, navigate to **Settings** > **Add-ons** > **Add-on Store**.
2.  Click the three-dot menu in the upper right and select **Repositories**.
3.  Add the following URL: `https://github.com/matthijsberg/HA-AI-Addons` (Renamed from `moltbot_bridge`)
4.  Once added, you will see the add-ons available in the store.

## Configuration

Configure the add-on via the "Configuration" tab in Home Assistant.

```yaml
log_level: info
ha_url: http://supervisor/core/api  # Default
ha_token: "YOUR_LONG_LIVED_ACCESS_TOKEN" # Optional if using Supervisor API
bluebubbles_url: "http://your-bluebubbles-server:1234"
bluebubbles_token: "YOUR_BB_PASSWORD"
whatsapp_provider: "twilio"
whatsapp_sid: "AC..."
whatsapp_token: "..."
whatsapp_from: "+1234567890"
```

## OAuth Setup (Google)

To enable Google integration:
1.  Go to Google Cloud Console.
2.  Create a project and enable relevant APIs (HomeGraph, etc.).
3.  Create OAuth 2.0 Credentials.
4.  Download the JSON file and place it in `/config/moltbot/google_creds.json` (inside HA).
    *Note: Future versions will support pasting the JSON directly into the config.*

## Development

Built with Python 3.11 using `aiohttp` and `websockets`.

### Structure

- `bridge.py`: Main entry point and orchestration logic.
- `config.yaml`: Add-on metadata and configuration schema.
- `Dockerfile`: Build instructions.
