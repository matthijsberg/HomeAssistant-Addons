# Home Assistant Apps

A curated collection of production-grade Home Assistant Apps (formerly add-ons) maintained by Matthijs van den Berg.

## Adding this Repository to Home Assistant

In Home Assistant:
1. Navigate to **Settings** ➔ **Apps** (or **Add-ons**) ➔ **App Store**.
2. Click the three dots (top right) ➔ **Repositories**.
3. Add: `https://github.com/matthijsberg/HomeAssistant-Apps`

---

## 🚀 Active Apps (`apps/`)

| App | Slug | Description |
| :--- | :--- | :--- |
| **[Open HEMS](apps/open-hems)** | `open_hems` | Intelligent Home Energy Management: heat pump optimization, solar forecasts, dynamic tariffs & battery dispatch. |
| **[Hermes Agent](apps/hermes-agent)** | `hermes_agent` | Self-improving AI agent runtime with multi-user HA auto-sync, MCP server integration & status reporting. |
| **[Laya Router](apps/laya)** | `laya` | Local sub-100ms System 1 router & decision engine across Gemini, LiteLLM, OpenRouter and custom gateways. |
| **[Langfuse](apps/langfuse)** | `langfuse` | Self-hosted LLM observability, prompt management & evaluation (Langfuse v4 + ClickHouse). |
| **[Mantis Security Agent](apps/mantis-security-agent)** | `mantis_security_agent` | Sandboxed AI code and configuration security auditing service with MCP interface. |
| **[Ollama](apps/ollama)** | `ollama` | Local LLM inference engine with hardware acceleration. |

---

## 📦 Archived Apps (`apps-archive/`)

These add-ons are kept for historical reference and code preservation, but are no longer actively maintained. Their configurations are disabled so they do not clutter the Home Assistant App Store:

- `matrix-conduit` & `matrix-synapse` (Migrated to external / standalone infrastructure)
- `moltbot-bridge`
- `ollama-intel` & `ollama-universal-xpu`
- `roon-server` & `roon-spotify`

---

## 🛠️ Development & Standards

- **Folder layout:** Active apps reside in `apps/<slug>/`, archived in `apps-archive/<slug>/`.
- **Naming:** Strict kebab-case directory names and slugs.
- **Security:** Pre-commit secret scanning required on all commits.
