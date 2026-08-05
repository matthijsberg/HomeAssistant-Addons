# Home Assistant Matrix Server Add-on 💬

An ultra-lightweight, stable, and low-maintenance **Matrix Homeserver Add-on for Home Assistant OS**, built on **Conduit** (Rust).

## Features
- ⚡ **Ultra-Fast & Efficient:** Built in Rust, consuming under 50MB RAM.
- 💾 **Embedded Database:** Uses SQLite/RocksDB — no external database server needed.
- 🔌 **Native Home Assistant Integration:** Installs directly as a local Home Assistant Add-on.
- 🔒 **Privacy First:** Self-hosted messaging server running entirely on your local home network.

## Quickstart

### 1. Installation in Home Assistant
1. Ensure this project is placed in `/config/addons/matrix`.
2. Go to **Settings > Add-ons > Add-on Store** in Home Assistant.
3. Click the top-right menu (⋮) and click **Check for updates** / **Reload**.
4. Scroll to **Local Add-ons**, select **Matrix Homeserver**, and click **Install**.

### 2. Configuration Options
Set your preferred configuration in the Add-on **Configuration** tab:
```yaml
server_name: "hass.local"
allow_registration: false
port: 6167
log_level: "warn"
```

### 3. One-Click Developer Setup & Demo Loop
From the terminal:
```bash
# Setup script
bash scripts/setup.sh

# Run live container smoke test
bash scripts/demo.sh
```

## License
MIT
