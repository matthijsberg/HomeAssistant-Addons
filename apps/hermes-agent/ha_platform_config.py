#!/usr/bin/env python3
"""Configures external messaging platforms (Telegram, WhatsApp, etc.) for secondary profiles.

Only rewrites config.yaml when a platform actually needs disabling, preserving
comments and formatting (see ha_mcp_config.py for the shared load/write rules).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from ha_mcp_config import load_config, write_config
except ImportError:  # installed as /usr/local/bin/hermes-ha-platform-config
    import importlib.machinery
    import importlib.util

    _loader = importlib.machinery.SourceFileLoader(
        "ha_mcp_config", "/usr/local/bin/hermes-ha-mcp-config"
    )
    _spec = importlib.util.spec_from_loader("ha_mcp_config", _loader)
    _module = importlib.util.module_from_spec(_spec)
    _loader.exec_module(_module)
    load_config, write_config = _module.load_config, _module.write_config

SHARED_PLATFORMS = ["telegram", "whatsapp", "discord", "slack", "signal"]


def disable_secondary_platforms(config_path: str) -> None:
    if not os.path.exists(config_path):
        return

    data, dump = load_config(config_path)
    if not isinstance(data, dict):
        return

    changed = False
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        data["platforms"] = platforms
        changed = True

    for plat in SHARED_PLATFORMS:
        plat_cfg = platforms.get(plat)
        if not isinstance(plat_cfg, dict):
            platforms[plat] = {"enabled": False}
            changed = True
        elif plat_cfg.get("enabled") is not False:
            plat_cfg["enabled"] = False
            changed = True

    display = data.get("display")
    if isinstance(display, dict) and isinstance(display.get("platforms"), dict):
        for plat in SHARED_PLATFORMS:
            plat_cfg = display["platforms"].get(plat)
            if isinstance(plat_cfg, dict) and plat_cfg.get("enabled") is not False:
                plat_cfg["enabled"] = False
                changed = True

    if changed:
        mcp = data.get("mcp_servers")
        has_secret = isinstance(mcp, dict) and "homeassistant" in mcp
        write_config(config_path, data, dump, contains_secret=has_secret)


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        disable_secondary_platforms(sys.argv[1])
