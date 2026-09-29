# HomeAssistant-Addons
My take at building some HA addons that I miss. 

Add this URL into HA as a custom repository: https://github.com/matthijsberg/HomeAssistant-Addons/

## Available Add-ons

- **[Langfuse](langfuse/)** — Self-hosted LLM Observability & Prompt Management (Langfuse v4 full-stack with ClickHouse, PostgreSQL, Redis, and Ingress).
- **[Matrix Synapse](matrix-synapse/)** — Matrix Homeserver with Sliding Sync and PostgreSQL.
- **[Roon Server](RoonServer/)** — Roon Core server for music playback.
- **[Roon Spotify](roon-spotify-addon/)** — Spotify Connect endpoint for Roon.

## Roon Server
Based on the work of Steef; https://github.com/steefdebruijn/docker-roonserver

Roon server will be installed during container start since i'm officially not allowed to distribute the software. So a add-on rebuild will upgrade. Perhaps talk to Roon folks one day. 

The Add-on maps /backup and /media into the container to use. 
