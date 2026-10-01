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


def log_msg(msg: str, is_err: bool = False, level: str = "info", version: str = "unknown") -> None:
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


def gateway_running(home: str) -> bool:
    """True when this profile's gateway slot is alive.

    Every slot runs `... gateway-launcher.py gateway run` with its profile home
    as working directory, which identifies the profile without the API server.
    """
    if not home:
        return False
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().split(b"\0")
            if not any(arg.endswith(b"gateway-launcher.py") for arg in cmdline):
                continue
            if os.path.realpath(os.readlink(f"/proc/{pid}/cwd")) == os.path.realpath(home):
                return True
        except OSError:
            continue
    return False


def api_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def read_api_enabled(options_file: str) -> bool:
    try:
        with open(options_file, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("enable_api", False))
    except Exception:
        return False


# Re-post unchanged states periodically: REST-created entities vanish when
# Home Assistant Core restarts, and this restores them within that interval.
REFRESH_SECONDS = 300
POLL_SECONDS = 15


def run_reporter_loop(options_file: str, profile_names: list, api_ports: list, version: str,
                      profile_homes: list) -> None:
    hass_url = os.environ.get("HASS_URL") or "http://supervisor/core"
    token = os.environ.get("HASS_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""
    if not token:
        log_msg("[ha-sensor-reporter] Warning: No Home Assistant token available; sensor reporting disabled.", is_err=True, version=version)
        return
    api_enabled = read_api_enabled(options_file)
    log_msg("[ha-sensor-reporter] Starting Home Assistant status sensor reporter...", level="info", version=version)
    last_sent: dict = {}

    def report(entity_id: str, state: str, attributes: dict) -> None:
        key = json.dumps([state, attributes], sort_keys=True)
        previous = last_sent.get(entity_id)
        if previous and previous[0] == key and time.monotonic() - previous[1] < REFRESH_SECONDS:
            return
        if post_ha_state(hass_url, token, entity_id, state, attributes):
            last_sent[entity_id] = (key, time.monotonic())

    while True:
        try:
            report(
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
            for idx, (name, port) in enumerate(zip(profile_names, api_ports)):
                home = profile_homes[idx] if idx < len(profile_homes) else ""
                running = gateway_running(home)
                healthy = api_healthy(port) if api_enabled else None
                # Without the API server, a live gateway slot is the best signal.
                online = healthy if api_enabled else running
                status = "online" if online else "offline"
                report(
                    f"sensor.hermes_agent_{name}",
                    status,
                    {
                        "friendly_name": f"Hermes Agent ({name})",
                        "profile_name": name,
                        "api_port": port,
                        "api_enabled": api_enabled,
                        "api_healthy": healthy,
                        "gateway_running": running,
                        "status": status,
                        "icon": "mdi:robot-happy" if status == "online" else "mdi:robot-off",
                    },
                )
        except Exception as err:
            log_msg(f"[ha-sensor-reporter] Loop exception: {err}", is_err=True, version=version)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        opts_file = sys.argv[1]
        version_str = sys.argv[2]
        names = sys.argv[3].split(",") if sys.argv[3] else []
        ports = [int(p) for p in sys.argv[4].split(",")] if sys.argv[4] else []
        homes = sys.argv[5].split(",") if len(sys.argv) >= 6 and sys.argv[5] else []
        run_reporter_loop(opts_file, names, ports, version_str, homes)
