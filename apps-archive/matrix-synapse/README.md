# Matrix Synapse + Sliding Sync Home Assistant Add-on

[![Version](https://img.shields.io/badge/version-v1.0.0--dev-blue.svg)](config.yaml)
[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue)](https://www.home-assistant.io/)

A state-of-the-art **Matrix Synapse Homeserver**, **PostgreSQL database**, and **Sliding Sync Proxy** packaged as a high-performance Home Assistant Add-on.

---

## Features

- 🚀 **Full Matrix Synapse Homeserver:** Complete support for Matrix protocol, E2EE, Spaces, and Threads.
- ⚡ **Sliding Sync Proxy (MSC3575):** Enables instant syncing for next-gen **Element X** clients.
- 🐘 **Embedded PostgreSQL 16:** High-performance local database engine persisted in `/data/postgres`.
- 🔒 **SSL Certificate Integration (`/ssl`):** Reads Let's Encrypt / custom certs directly from Home Assistant's `/ssl` mount (`fullchain.pem` & `privkey.pem`).
- 💾 **Automated Backup Mapping (`/backup`):** Dumps PostgreSQL databases (`pg_dump`) and Synapse configuration directly into Home Assistant's `/backup` directory.
- ⚙️ **Config Directives (`/config`):** Maps `/config` for HA integration snippets and bot token sharing.

---

## Installation in Home Assistant

1. Copy or clone this directory to your Home Assistant `/config/addons/matrix-synapse/` folder (or local Add-on repository).
2. Go to **Settings -> Add-ons -> Add-on Store** in Home Assistant.
3. Click **Menu (top-right) -> Check for updates / Repositories** and reload.
4. Select **Matrix Synapse** from the local add-ons list.
5. Configure your domain name and SSL preferences in the Add-on Configuration tab.
6. Click **Start**.

---

## Configuration Options (`config.yaml`)

| Option | Type | Default | Description |
|---|---|---|---|
| `server_name` | string | `matrix.local` | Your public Matrix domain name |
| `enable_sliding_sync` | boolean | `true` | Enable Sliding Sync Proxy for Element X |
| `enable_ssl` | boolean | `true` | Use SSL certificates from `/ssl` directory |
| `ssl_cert` | string | `fullchain.pem` | Certificate filename in `/ssl/` |
| `ssl_key` | string | `privkey.pem` | Private key filename in `/ssl/` |
| `backup_interval_hours` | integer | `24` | Automated Postgres dump interval to `/backup` |

---

## Ports Exposed

- **`8008`**: Matrix Client API (HTTP)
- **`8448`**: Matrix Client & Federation API (HTTPS)
- **`8009`**: Sliding Sync Proxy (Element X)

---

## License & Credits
Licensed under MIT. Created for Home Assistant OS & Supervised environments.
