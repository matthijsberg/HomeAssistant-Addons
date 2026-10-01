#!/usr/bin/env python3
"""Helper script to inject Home Assistant MCP server configuration into profile config.yaml.

The file is only rewritten when the managed block actually changes, and is
round-tripped with ruamel.yaml (a Hermes dependency) so user comments and
formatting survive. PyYAML is the fallback for revisions without ruamel.
"""

import os
import sys

# Pinned so a compromised or breaking npm release cannot reach every profile on
# the next start. Bump deliberately after reviewing the upstream changelog.
HA_MCP_PACKAGE = "@orellbuehler/homeassistant-mcp@0.8.0"


def load_config(config_path: str):
    """Return (data, dump) where dump(data) writes back in the loader's style."""
    try:
        from ruamel.yaml import YAML

        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True

        def load(stream):
            return yaml_rt.load(stream)

        def dump(data, stream):
            yaml_rt.dump(data, stream)
    except ImportError:
        import yaml

        def load(stream):
            return yaml.safe_load(stream)

        def dump(data, stream):
            yaml.safe_dump(data, stream, default_flow_style=False, sort_keys=False)

    data = None
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = load(f)
        except Exception:
            # Never overwrite a config we cannot parse; the user may be mid-edit.
            return None, dump
    if data is None:
        data = {}
    return data, dump


def to_plain(value):
    """Compare ruamel and PyYAML structures by value."""
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    return value


def write_config(config_path: str, data, dump, contains_secret: bool) -> None:
    tmp_path = f"{config_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        dump(data, f)
    if contains_secret:
        os.chmod(tmp_path, 0o600)
    elif os.path.exists(config_path):
        os.chmod(tmp_path, os.stat(config_path).st_mode & 0o777)
    os.replace(tmp_path, config_path)


def configure_ha_mcp(config_path: str, hass_url: str, hass_token: str, venv_bin: str) -> None:
    if not hass_url or not hass_token:
        return

    data, dump = load_config(config_path)
    if not isinstance(data, dict):
        return

    mcp_servers = data.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        data["mcp_servers"] = mcp_servers

    desired = {
        "command": "/usr/bin/npx",
        "args": ["-y", HA_MCP_PACKAGE],
        "env": {"HASS_URL": hass_url, "HASS_TOKEN": hass_token},
    }
    if to_plain(mcp_servers.get("homeassistant")) == desired:
        # Still tighten permissions on files written by older versions.
        os.chmod(config_path, 0o600)
        return

    mcp_servers["homeassistant"] = desired
    write_config(config_path, data, dump, contains_secret=True)


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        configure_ha_mcp(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
