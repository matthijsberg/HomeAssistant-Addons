#!/usr/bin/env python3
"""Periodically reports Hermes Agent system and profile status to Home Assistant Core API."""

import json
import os
import sys
import time
import urllib.error
import urllib.request

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").lower()
LEVEL_MAP = {
    "trace": 0, "debug": 1, "info": 2, "notice": 3,
    "warning": 4, "warn": 4, "error": 5, "fatal": 6
}
CURRENT_LOG_LEVEL_NUM = LEVEL_MAP.get(LOG_LEVEL, 2)


def log_msg(msg: str, is_err: bool = False, level: str = "info", version: str = "2.4.0") -> None:
    msg_level_num = LEVEL_MAP.get(level.lower(), 2)
    if is_err:
        msg_level_num = max(msg_level_num, 5)
    if msg_level_num >= CURRENT_LOG_LEVEL_NUM:
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        stream = sys.stderr if is_err else sys.stdout
        stream.write(f"[{now}] [v{version}] {msg}\n")
        stream.flush()


def post_ha_state(hass_url: str, token: str, entity_id: str, state: str, attributes: dict) -> bool:
    if not hass_url or not token:
        return False

    url = f"{hass_url.rstrip('/')}/api/states/{entity_id}"
    payload = json.dumps({"state": state, "attributes": attributes}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        log_msg(f"[ha-sensor-reporter] Error updating {entity_id}: {e}", is_err=True)
        return False


def run_reporter_loop(options_file: str, profile_names: list, api_ports: list, version: str) -> None:
    hass_url = os.environ.get("HASS_URL") or "http://supervisor/core"
    token = os.environ.get("HASS_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""

    if not token:
        log_msg("[ha-sensor-reporter] Warning: No Home Assistant token available; sensor reporting disabled.", is_err=True, version=version)
        return

    log_msg("[ha-sensor-reporter] Starting Home Assistant status sensor reporter...", level="info", version=version)

    while True:
        try:
            # Report main system sensor
            post_ha_state(
                hass_url,
                token,
                "sensor.hermes_agent",
                "online",
                {
                    "friendly_name": "Hermes Agent",
                    "version": version,
                    "profile_count": len(profile_names),
                    "profiles": profile_names,
                    "icon": "mdi:robot",
                },
            )

            # Report individual profile sensors
            for name, port in zip(profile_names, api_ports):
                entity_id = f"sensor.hermes_agent_{name}"
                health_url = f"http://127.0.0.1:{port}/v1/health"
                status = "offline"
                try:
                    with urllib.request.urlopen(health_url, timeout=2) as resp:
                        if resp.status == 200:
                            status = "online"
                except Exception:
                    status = "offline"

                post_ha_state(
                    hass_url,
                    token,
                    entity_id,
                    status,
                    {
                        "friendly_name": f"Hermes Agent ({name})",
                        "profile_name": name,
                        "api_port": port,
                        "status": status,
                        "icon": "mdi:robot-happy" if status == "online" else "mdi:robot-off",
                    },
                )
        except Exception as err:
            log_msg(f"[ha-sensor-reporter] Loop exception: {err}", is_err=True, version=version)

        time.sleep(15)


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        opts_file = sys.argv[1]
        version_str = sys.argv[2]
        names = sys.argv[3].split(",") if sys.argv[3] else []
        ports = [int(p) for p in sys.argv[4].split(",")] if sys.argv[4] else []
        run_reporter_loop(opts_file, names, ports, version_str)
