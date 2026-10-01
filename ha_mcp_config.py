#!/usr/bin/env python3
"""Helper script to inject Home Assistant MCP server configuration into profile config.yaml."""

import os
import sys
import yaml


def configure_ha_mcp(config_path: str, hass_url: str, hass_token: str, venv_bin: str) -> None:
    if not hass_url or not hass_token:
        return

    data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}

    mcp_servers = data.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    mcp_servers["homeassistant"] = {
        "command": "/usr/bin/npx",
        "args": [
            "-y",
            "@orellbuehler/homeassistant-mcp",
        ],
        "env": {
            "HASS_URL": hass_url,
            "HASS_TOKEN": hass_token,
            "NODE_TLS_REJECT_UNAUTHORIZED": "0" if hass_url.startswith("https://") else "1",
        },
    }
    data["mcp_servers"] = mcp_servers

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        configure_ha_mcp(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
