#!/usr/bin/env python3
"""Configures external messaging platforms (Telegram, WhatsApp, etc.) for secondary profiles."""

import os
import sys
import yaml


def disable_secondary_platforms(config_path: str) -> None:
    if not os.path.exists(config_path):
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return

    if not isinstance(data, dict):
        return

    # Disable top-level platforms
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}

    for plat in ["telegram", "whatsapp", "discord", "slack", "signal"]:
        plat_cfg = platforms.get(plat)
        if not isinstance(plat_cfg, dict):
            plat_cfg = {}
        plat_cfg["enabled"] = False
        platforms[plat] = plat_cfg

    data["platforms"] = platforms

    # Disable nested display.platforms if present
    display = data.get("display")
    if isinstance(display, dict):
        disp_platforms = display.get("platforms")
        if isinstance(disp_platforms, dict):
            for plat in ["telegram", "whatsapp", "discord", "slack", "signal"]:
                plat_cfg = disp_platforms.get(plat)
                if isinstance(plat_cfg, dict):
                    plat_cfg["enabled"] = False

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        disable_secondary_platforms(sys.argv[1])
