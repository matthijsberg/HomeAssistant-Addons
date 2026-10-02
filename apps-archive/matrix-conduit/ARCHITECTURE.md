# System Architecture — Home Assistant Matrix Add-on

## Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Home Assistant OS / Supervisor                  │
│                                                                        │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │                 ha-addon-matrix Container                    │     │
│   │                                                              │     │
│   │   ┌────────────────────────┐    ┌────────────────────────┐   │     │
│   │   │  run.sh / Entrypoint   │ ──►│    conduit.toml        │   │     │
│   │   │  (renders HA options)  │    │ (generated config)     │   │     │
│   │   └────────────────────────┘    └────────────────────────┘   │     │
│   │                                              │               │     │
│   │                                              ▼               │     │
│   │                                 ┌────────────────────────┐   │     │
│   │                                 │ Conduit Server (Rust)  │   │     │
│   │                                 │ Port: 6167 / 8008      │   │     │
│   │                                 └────────────────────────┘   │     │
│   │                                              │               │     │
│   └──────────────────────────────────────────────┼───────────────┘     │
│                                                  │                     │
│                                                  ▼                     │
│                                     ┌────────────────────────┐         │
│                                     │ Persistent /data Volume│         │
│                                     │ (SQLite DB & Media)    │         │
│                                     └────────────────────────┘         │
└────────────────────────────────────────────────────────────────────────┘
```

## Data Flow & File Layout

1. **HA Supervisor** reads `config.yaml` and presents user options in the HA Add-on UI.
2. User options are saved to `/data/options.json` inside the container when started.
3. `run.sh` parses `/data/options.json` and generates `/etc/conduit/conduit.toml`.
4. `conduit` binary launches, mounting persistent database and media store in `/data/conduit_db` and `/data/media`.
5. Clients (Element, FluffyChat, HA Matrix bot integration) connect via standard Matrix Client-Server API on port 6167/8008.
