# 🔍 Langfuse v4 — Home Assistant Add-on

[![Langfuse v4](https://img.shields.io/badge/Langfuse-v4%20GA-blue.svg)](https://langfuse.com)
[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io/addons/)
[![OKF v0.2](https://img.shields.io/badge/OKF-v0.2%20verified-success.svg)](knowledge/index.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A native Home Assistant Add-on bringing **[Langfuse v4](https://langfuse.com)** directly into your smart home. It provides self-hosted LLM observability, prompt engineering, evaluation metrics, and real-time execution tracing for **Hermes Agent** and local AI applications.

---

## 🌟 Key Features

- **Enterprise v4 Architecture:** Supervised multi-service stack with **PostgreSQL 16** (OLTP), **ClickHouse 26.4** (OLAP analytics), **Redis 7.2** (queues), and **SeaweedFS** (S3 blob storage).
- **Home Assistant Ingress:** Native 1-click access directly from your Home Assistant sidebar.
- **Dynamic LAN Port:** Leave `3000/tcp` empty for zero-port Ingress isolation, or specify a port to expose the API and UI to your home network.
- **Headless Zero-Touch Init:** Automatically creates the `Default` organization, `Hermes` project, and API keys on first boot.
- **30-Day Automated Retention:** Built-in nightly pruning of historical ClickHouse traces and SeaweedFS events to prevent disk exhaustion on home servers.
- **Cold, Consistent Backups:** Fully integrates with Home Assistant's `backup: cold` snapshots.

---

## 🚀 Installation

1. Add this repository to your Home Assistant Add-on Store:
   ```
   https://github.com/matthijsberg/HomeAssistant-Addons
   ```
2. Navigate to **Settings ➔ Add-ons ➔ Add-on Store** and search for **Langfuse**.
3. In the add-on **Configuration** tab, provide your initial credentials:
   - `admin_email`: your email address
   - `admin_password`: your initial password (minimum 8 characters)
4. Click **Install**, then **Start**.
5. Click **Open Web UI** or access Langfuse via the sidebar.

---

## 🤖 Connecting to Hermes Agent

Langfuse integrates natively with Hermes Agent's bundled `observability/langfuse` plugin.

### 1. Configure Hermes (`~/.hermes/.env`)
```bash
HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
# Direct add-on container network address:
HERMES_LANGFUSE_BASE_URL=http://local-langfuse:3000
# Or via LAN if port 3000 is mapped:
# HERMES_LANGFUSE_BASE_URL=http://<home-assistant-ip>:3000
HERMES_LANGFUSE_CAPTURE=sanitized
```

### 2. Enable in Hermes
```bash
pip install "langfuse>=4.7.0"
hermes plugins enable observability/langfuse
```

Every prompt, turn, tool invocation, token count, and cost will now stream directly to your local Langfuse instance.

---

## 🎙️ Tracing Home Assistant Assist (Voice & Chat)

You can automatically route and trace all Home Assistant native voice and chat assistant requests directly in Langfuse using **Extended OpenAI Conversation** (HACS) or the native **OpenAI Conversation** integration.

### Setup Instructions

1. **Obtain API Keys:**
   - In Langfuse (via the HA sidebar), open the **Hermes** project (or create a dedicated `Home Assistant` project).
   - Go to **Settings ➔ API Keys** and copy the **Secret Key** (`sk-lf-...`) and **Public Key** (`pk-lf-...`).
2. **Configure Integration in Home Assistant:**
   - Go to **Settings ➔ Devices & Services ➔ Add Integration ➔ Extended OpenAI Conversation** (or *OpenAI Conversation*).
   - Fill in the connection parameters:
     - **API Key:** `sk-lf-...`
     - **Base URL:** `http://local-langfuse:3000/api/public/openai/v1`  
       *(Note: If you are connecting across your LAN or from external services, ensure port `3000/tcp` is exposed in the add-on configuration, and use `http://<home-assistant-ip>:3000/api/public/openai/v1`)*.
     - **Model Name:** Enter your downstream model (e.g., `gpt-4o-mini`, `gemini-2.5-flash`, etc.).
3. **Set as Default Assist Conversation Agent:**
   - Navigate to **Settings ➔ Voice Assistants ➔ Assist**.
   - Under **Conversation Agent**, select your configured Extended OpenAI agent.

Every voice command from your dashboard, mobile app, or ESP32 voice satellite will now generate a complete trace in Langfuse, recording:
- Input user prompt and system context
- Exposed Home Assistant entity state and tools
- Model token usage, response latency, and execution cost

---

## 📋 Quality & Architecture Standards

This add-on is governed by the **[Open Knowledge Format (OKF v0.2)](knowledge/index.md)** architectural bundle and automated quality checks:

```bash
# Run local verification & tests
bash scripts/setup.sh
```

---

## 📄 License

MIT License © 2026 Matthijs van den Berg.
