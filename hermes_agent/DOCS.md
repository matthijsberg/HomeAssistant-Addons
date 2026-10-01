# Hermes Agent Home Assistant App Documentation

Hermes Agent is an autonomous AI agent developed by Nous Research, packaged as a multi-user Home Assistant App. It provides localized AI assistance with long-term memory, smart home device control through the Model Context Protocol (MCP), voice pipeline integration, and automatic occupant profile isolation.

---

## Quick Start

### 1. Configure Model Provider API Key
1. Navigate to **Settings > Apps > Hermes Agent > Configuration**.
2. Under **Hermes .env Variables**, add your LLM provider key:
   - Name: `OPENROUTER_API_KEY` (or `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`).
   - Value: your API secret key.
3. Click **Save**.

### 2. Start the App
1. Go to the **Info** tab and click **Start**.
2. On initial startup, the container sets up its runtime dependencies and automatically provisions profiles for your Home Assistant occupants. Allow 30–60 seconds for initialization.

### 3. Open in the Sidebar
1. Click **Hermes Agent** in the Home Assistant sidebar.
2. The App detects your logged-in Home Assistant account and opens your dedicated profile directly.
3. Select your model and begin chatting.

### 4. (Optional) Connect to Voice Assist
1. In the App **Configuration** tab, set an `access_password` (minimum 16 characters).
2. In Home Assistant, navigate to **Settings > Devices & Services > Add Integration > OpenAI Conversation**.
3. Set the Server URL to `http://local-hermes-agent:8080/v1/` and enter your `access_password` as the API Key.
4. Go to **Settings > Voice Assistants > Assist**, and set the conversation agent to your new OpenAI Conversation entry.

---

## Complete Settings Reference

Configuration options are managed under **Settings > Apps > Hermes Agent > Configuration**:

### Home Assistant Integration

| Option | Type | Default | Description |
|---|---|---|---|
| `auto_sync_ha_users` | `bool` | `true` | Automatically discover active Home Assistant users (`person.*` entities linked to accounts) and generate isolated profile folders. |
| `hass_url` | `url` | *(empty)* | Home Assistant Core base URL. Defaults to the internal `http://supervisor/core` proxy when left empty. A custom URL is only used together with `homeassistant_token`; without a token the App always uses `SUPERVISOR_TOKEN` through `http://supervisor/core`. |
| `homeassistant_token` | `password` | `""` | Optional long-lived access token. When left empty, the App automatically uses the internal `SUPERVISOR_TOKEN` provided by Home Assistant. |

### Backups & Maintenance

| Option | Type | Default | Description |
|---|---------|---------|---|
| `enable_periodic_backups` | `bool` | `true` | Automatically archive each active profile to `/backup/hermes/<profile>/` on a schedule. |
| `periodic_backup_interval_hours` | `int` | `24` | Interval in hours between automated profile backups (1–168 hours). |
| `periodic_backup_keep_count` | `int` | `7` | Number of recent archives to keep per profile before deleting older files. |

### Logging & SSL Certificates

| Option | Type | Default | Description |
|---|---|---|---|
| `log_level` | `select` | `"info"` | Logging threshold (`trace`, `debug`, `info`, `notice`, `warning`, `error`, `fatal`). At `warning` and higher, HTTP request logging in Nginx is suppressed. |
| `use_ha_ssl_cert` | `bool` | `false` | When `true`, reuses Home Assistant's SSL certificates from `/ssl` instead of generating self-signed certificates. |
| `ha_ssl_certfile` | `string` | `"fullchain.pem"` | Certificate chain filename inside `/ssl`. |
| `ha_ssl_keyfile` | `string` | `"privkey.pem"` | Private key filename inside `/ssl`. |

### Web Access & Direct Ports

| Option | Type | Default | Description |
|---|---|---|---|
| `access_password` | `password` | `""` | Required password for direct HTTP/HTTPS web, API, and terminal access (username: `hermes`). Direct terminal routes reject connections if this is unset. |
| `enable_dashboard` | `bool` | `false` | Enable web dashboard on direct LAN ports (`8080` / `8443`). |
| `enable_terminal` | `bool` | `false` | Enable web terminal on direct LAN ports (`8080` / `8443`). |
| `enable_api` | `bool` | `false` | Enable OpenAI `/v1/` and Sessions `/api/` endpoints on direct LAN ports. |
| `enable_desktop_backend` | `bool` | `false` | Run the official Hermes Desktop remote backend on container port `9119`. |

### Multi-Profile & Environment

| Option | Type | Default | Description |
|---|---|---|---|
| `profiles` | `list(str)` | `[]` | Explicit list of profiles to run concurrently. When empty, active Home Assistant human users are populated automatically. |
| `profiles_base` | `string` | `".hermes/profiles"` | Directory under `/config/` where secondary profile directories are placed. |
| `env_vars` | `list(dict)` | `[OPENROUTER_API_KEY]` | Environment variables written to every profile's `.env` file on startup. |
| `profile_env_vars` | `list(dict)` | `[]` | Specific per-profile environment overrides (e.g. assigning a specific `HASS_TOKEN` to one user). |

---

## Multi-User Profiles & Ingress Routing

### Occupant Auto-Discovery
On container startup, the App queries Home Assistant Core for active `person.*` entities with associated user accounts:
1. A dedicated directory is prepared at `/config/.hermes/profiles/<username>/`.
2. Each profile receives isolated data stores:
   - **`USER.md`:** User identity details (name and username).
   - **`SOUL.md`:** Customized persona template for that user.
   - **`memories/`:** Isolated SQLite database for long-term memory storage.
   - **`sessions/`:** Independent chat conversation histories.
3. The first discovered occupant is assigned as the Primary Profile (Profile 0), owning root endpoints and shared gateway integrations (such as Telegram and WhatsApp).

### Smart Ingress Routing
When opening **Hermes Agent** from the Home Assistant sidebar, the internal Nginx proxy evaluates the incoming Home Assistant user session headers (`X-Ingress-User`, `X-Hass-User-Name`). The browser client receives this context and switches the active interface to that user's personal profile without requiring manual account switching.

---

## Home Assistant Integration Methods: Core API vs. MCP Server

Hermes Agent communicates with Home Assistant using two distinct layers:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Hermes Agent                               │
├────────────────────────────────────┬────────────────────────────────────┤
│     Core API Platform Adapter      │         MCP Tooling Server         │
│      (Event-Driven Listener)       │       (LLM Function Calling)       │
├────────────────────────────────────┼────────────────────────────────────┤
│  • Subscribes to state_changed     │  • Provides structured tool calls  │
│  • Monitors real-time entity bus   │  • Reads states & entity history   │
│  • Dispatches event automations    │  • Executes services & commands    │
└──────────────────┬─────────────────┴──────────────────┬─────────────────┘
                   │                                    │
                   ▼                                    ▼
       WebSocket & REST Endpoint              Node.js Stdio Bridge
     (http://supervisor/core/api/)      (@orellbuehler/homeassistant-mcp)
                   │                                    │
                   └─────────────────┬──────────────────┘
                                     ▼
                        Home Assistant Core Engine
```

### Automated Zero-Configuration Authentication
In previous releases, connecting to Home Assistant required generating a Long-Lived Access Token in your user profile and pasting it into `homeassistant_token`.

**This is no longer necessary.** The App now declares `homeassistant_api: true` in its manifest. Home Assistant Supervisor automatically provisions an authenticated internal token (`SUPERVISOR_TOKEN`) and routes traffic over the internal network to `http://supervisor/core`. Both the Core API platform and the MCP server configure themselves on startup with zero manual credentials needed.

*(Note: If you wish to restrict entity permissions to a specific non-admin user account, you can still provide a manual token in `profile_env_vars` as detailed under Per-User Access Rights below).*

---

### 1. Core API Platform (`homeassistant`)
The Core API platform connects Hermes to Home Assistant's WebSocket and REST endpoints:
- **Role:** Background event monitoring and reactive triggers.
- **Functionality:** Subscribes to state changes across your installation. When an entity changes state (for example, motion detected or a door opened), the event is delivered to the agent's event loop.
- **Event Filtering:** To keep logs focused, specify entity or domain watchers in your profile's `config.yaml`:
  ```yaml
  platforms:
    homeassistant:
      watch_domains:
        - climate
        - light
        - switch
      watch_entities:
        - binary_sensor.front_door
  ```

---

### 2. MCP Server (`@orellbuehler/homeassistant-mcp`)
The Model Context Protocol (MCP) server provides tool calling capabilities for the LLM:
- **Role:** Interactive device introspection and action execution.
- **Functionality:** Exposes standardized functions to the LLM—such as querying device status, listing available entities, inspecting state history, and calling Home Assistant services (turning on lights, setting thermostat temperatures).
- **Automatic Configuration:** The App automatically writes the MCP configuration into each profile's `config.yaml`:
  ```yaml
  mcp_servers:
    homeassistant:
      command: /usr/bin/npx
      args:
        - -y
        - '@orellbuehler/homeassistant-mcp@0.8.0'
      env:
        HASS_URL: http://supervisor/core
        HASS_TOKEN: <automatically provided>
  ```
  The package version is pinned in the App (bumped deliberately per release), the file is set to `chmod 600` because it holds the token, and only this block is updated: your own comments and formatting in `config.yaml` are preserved, and the file is not rewritten when nothing changed.
- **Controlling Exposed Devices:**
  You can manage which devices and entities the AI agent is allowed to see and control:
  1. Open Home Assistant and navigate to **Settings > Voice Assistants > Expose**.
  2. Toggle on only the entities, areas, and devices you want exposed to Assist and AI assistants.

---

### Comparison: Core API Platform vs. MCP Server

| Feature | Core API Platform (`homeassistant`) | MCP Server (`@orellbuehler/homeassistant-mcp`) |
|---|---|---|
| **Protocol** | Home Assistant WebSocket / REST | Stdio Model Context Protocol (MCP) |
| **Primary Function** | Event ingestion and trigger monitoring | Structured tool calling by language models |
| **Communication Direction** | Inbound push (`state_changed` events) | Outbound query/command (tool calls) |
| **Authentication** | Automatic via `SUPERVISOR_TOKEN` | Automatic via `SUPERVISOR_TOKEN` |
| **Filtering Mechanism** | `watch_domains` / `watch_entities` | Home Assistant **Expose to Assist** settings |

---

## Home Assistant Assist Voice Integration

Hermes can be registered as an Assist conversation agent, allowing voice satellites (e.g. ESP32-S3 Box, Atom Echo, or Home Assistant mobile apps) to converse with the agent.

1. **Prerequisite:** Set an `access_password` in the App's **Configuration** tab.
2. In Home Assistant, go to **Settings > Devices & Services > Add Integration**.
3. Search for **OpenAI Conversation** (or Extended OpenAI Conversation).
4. Enter the connection settings:
   - **Server URL:** `http://local-hermes-agent:8080/v1/`
   - **API Key:** your configured `access_password`
   - **Model:** your profile identifier (e.g. `alice` or `default`)
5. Navigate to **Settings > Voice Assistants > Assist**, click your voice pipeline, and select the Hermes entry under **Conversation Agent**.

---

## Per-User Entity Access Control

If you want a profile to inherit the permission scope of a specific Home Assistant user rather than the administrator token:

1. Log into Home Assistant as that specific user.
2. Open **User Profile** (bottom-left) > **Security** > **Long-Lived Access Tokens** > **Create Token**.
3. In Hermes App Configuration under **Per-profile .env Overrides**, add an override for that user:
   ```yaml
   profile_env_vars:
     - profile: bob
       name: HASS_TOKEN
       value: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
   ```
4. Restart the App. Any actions initiated by Bob will now execute through Bob's personal token, respecting entity visibility rules and permissions set in Home Assistant.

---

## Operational Health Sensors

The App publishes operational status to Home Assistant Core:

| Entity ID | Description | Values |
|---|---|---|
| `sensor.hermes_agent` | Global App state | `online`, `offline` (includes version and active profile count) |
| `sensor.hermes_agent_<profile>` | Per-profile state | `online`, `offline` (includes profile name, API port, `gateway_running`, `api_healthy`) |

With `enable_api` on, a profile is `online` when its `/v1/health` answers; with the API off, when its gateway process is running. States are re-sent every 5 minutes, so they reappear shortly after a Home Assistant restart.

These sensors appear under **Developer Tools > States** and can be placed on Lovelace dashboards or used in automations to alert if an agent instance goes offline.

---

## Storage Architecture & Backups

### File Structure on Disk
Persistent data is located in `/config` (mapped to `addon_configs/local_hermes_agent` on host storage):

```text
/config/
├── .certs/                          # TLS certificates
├── .hermes/                         # Base Hermes directory
│   ├── hermes-agent/                # Core repository & venv
│   └── profiles/                    # Isolated occupant profiles
│       ├── alice/                   # User profile
│       │   ├── .env                 # API credentials
│       │   ├── config.yaml          # Hermes configuration & MCP tools
│       │   ├── SOUL.md              # Personal AI persona template
│       │   ├── USER.md              # User metadata
│       │   ├── memories/            # SQLite long-term memory
│       │   └── sessions/            # Chat history
│       └── bob/
├── .hermes_port_slots               # Stable port slot per profile name
└── .hermes_profile                  # Active environment variables (chmod 600)
```

Each profile name keeps its internal ports (`8642 + slot` for the API, and so on) even when profiles are reordered, removed, or added by HA user sync. Delete `.hermes_port_slots` to reassign slots in list order.

### Automated Periodic Backups
- The App includes an automated backup scheduler configured via `enable_periodic_backups` (default `true`).
- Every `periodic_backup_interval_hours` (default `24`), an archive of each profile's persistent state (excluding temporary logs, sockets, and caches) is written to Home Assistant's shared backup directory:
  `/backup/hermes/<profile>/hermes-backup-<profile>-<timestamp>.tar.gz`
- SQLite databases (`state.db`, `kanban.db`, …) are captured with SQLite's online backup API, so an archive taken while agents are active still contains a consistent copy including recent WAL writes.
- Backups older than `periodic_backup_keep_count` (default `7`) are pruned automatically.
- These archives are stored on the shared `/backup` mount and can be synced off-site by Home Assistant backup integrations (such as Google Drive Backup).
