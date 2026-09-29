# Home Assistant Add-on: Langfuse v4 LLM Observability

Self-hosted **[Langfuse v4](https://langfuse.com)** observability and prompt management platform inside Home Assistant OS. It provides full execution tracing, token cost accounting, model performance metrics, and evaluation tools for **Hermes Agent** and local AI voice/chat applications.

---

## 🚀 First Login & Access

1. Open Langfuse via the Home Assistant **Sidebar** or click **Open Web UI**.
2. Log in using the credentials you configured in the **Configuration** tab:
   - **Email:** The `admin_email` you set (e.g. `matthijs@b3rg.nl`).
   - **Password:** The `admin_password` you set.
3. Upon first login, you will find the **Hermes** project already created and ready to receive traces.

---

## 🤖 Connecting to Hermes Agent

Hermes Agent natively bundles the `observability/langfuse` plugin.

### 1. Retrieve Generated API Keys
On first boot, the add-on automatically generated your keys into `/share/langfuse/hermes.env`:

```bash
HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
HERMES_LANGFUSE_BASE_URL=http://local-langfuse:3000
HERMES_LANGFUSE_CAPTURE=sanitized
```

### 2. Enable in Hermes
Add the environment variables to your Hermes profile configuration (`~/.hermes/.env`), then run:

```bash
pip install "langfuse>=4.7.0"
hermes plugins enable observability/langfuse
```

Every prompt, tool invocation, token count, and latency will now stream directly to Langfuse.

---

## 🎙️ Tracing Home Assistant Assist (Voice & Chat)

You can route and trace all Home Assistant native voice and chat assistant interactions in Langfuse:

1. **Obtain API Keys:**
   - In Langfuse (via the HA sidebar), open the **Hermes** project.
   - Go to **Project Settings ➔ API Keys** and copy the **Secret Key** (`sk-lf-...`).
2. **Configure in Home Assistant:**
   - Go to **Settings ➔ Devices & Services ➔ Add Integration ➔ Extended OpenAI Conversation** (or *OpenAI Conversation*).
   - Enter:
     - **API Key:** `sk-lf-...`
     - **Base URL:** `http://local-langfuse:3000/api/public/openai/v1`  
       *(Or `https://hass.b3rg.nl:3000/api/public/openai/v1` if accessed via external FQDN)*.
     - **Model Name:** Your model (e.g., `gpt-4o-mini`, `gemini-2.5-flash`).
3. **Set Default Voice Assistant:**
   - Go to **Settings ➔ Voice Assistants ➔ Assist** and select your configured agent.

Every voice command from your dashboard, phone, or ESP32 voice satellite will now record:
- User prompts and system context
- Home Assistant tool calls (turning lights on/off, climate adjustments)
- Token counts, latency, and costs

---

## ⚙️ Configuration Reference

In the Add-on **Configuration** tab:

- **`admin_email`** *(required)*: Administrator email for the initial account.
- **`admin_password`** *(required)*: Administrator password (minimum 8 characters).
- **`ports ➔ 3000/tcp`** *(optional)*: 
  - *Leave empty (default):* Pure Ingress isolation. Accessible via Home Assistant sidebar.
  - *Enter `3000`:* Exposes the Langfuse Web UI and API directly on your local network / external domain. Automatically uses your SSL certificates from `/ssl` (`https://hass.b3rg.nl:3000`).
- **`retention_days`** *(default: 30)*: Automatic nightly pruning of historical trace events in ClickHouse and SeaweedFS (0 = keep forever).
- **`clickhouse_memory_limit_mb`** *(default: 4096)*: RAM cap for ClickHouse OLAP queries.
- **`node_max_old_space_mb`** *(default: 2048)*: Node.js memory allocation for Web and Worker.
- **`export_hermes_env`** *(default: true)*: Automatically exports keys to `/share/langfuse/hermes.env`.

---

## 📊 Home Assistant KPI Sensors

The add-on continuously monitors analytical metrics in ClickHouse and automatically publishes key performance indicators as native Home Assistant sensors:

- **`sensor.langfuse_tokens_today`**: Total tokens consumed today (attributes: `input_tokens`, `output_tokens`, `reasoning_tokens`).
- **`sensor.langfuse_cost_today`**: Total estimated cost today in USD ($).
- **`sensor.langfuse_llm_calls_today`**: Total LLM calls / generations today.
- **`sensor.langfuse_avg_latency`**: Average generation latency in seconds.

These sensors update every 15–30 seconds and can be directly used in Lovelace dashboards, automation thresholds, or alerts.

---

## 🔒 Storage, Backups & Maintenance

- **Persistent Data:** All databases are stored safely under `/data`:
  - `/data/postgres` (PostgreSQL 16 relational data)
  - `/data/clickhouse` (ClickHouse analytical traces)
  - `/data/seaweedfs` (SeaweedFS S3 event blobs)
  - `/data/redis` (Redis AOF queues)
- **Consistent Backups:** This add-on uses `backup: cold`. Home Assistant temporarily pauses the databases to guarantee zero transaction corruption during snapshot creation.
