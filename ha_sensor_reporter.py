#!/usr/bin/env python3
"""Periodically reports Hermes Agent system and profile status to Home Assistant.

Preferred transport is MQTT discovery (stable entities with unique_id, grouped
under a "Hermes Agent" device, unavailable when the App stops). Without an MQTT
broker the reporter falls back to posting states through the Core REST API.
"""

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
            # Substring match: a bootstrap re-exec runs the launcher via runpy.
            if not any(b"gateway-launcher.py" in arg for arg in cmdline):
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
DISCOVERY_PREFIX = "homeassistant"
BASE_TOPIC = "hermes_agent"
LEGACY_REST_ENTITIES_PREFIX = "sensor.hermes_agent"


def profile_status(name: str, port: int, home: str, api_enabled: bool) -> dict:
    running = gateway_running(home)
    healthy = api_healthy(port) if api_enabled else None
    # Without the API server, a live gateway slot is the best signal.
    online = healthy if api_enabled else running
    return {
        "status": "online" if online else "offline",
        "online": bool(online),
        "gateway_running": running,
        "api_enabled": api_enabled,
        "api_healthy": healthy,
        "api_port": port,
    }


def supervisor_mqtt_config() -> dict | None:
    """Broker credentials published by the Mosquitto App (config.yaml: services: mqtt:want)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        "http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp).get("data") or {}
    except Exception:
        return None
    return data if data.get("host") else None


def discovery_messages(profile_names: list, version: str, api_enabled: bool) -> list:
    """(topic, payload) pairs announcing every entity to Home Assistant."""
    availability = [{"topic": f"{BASE_TOPIC}/status"}]
    app_device = {
        "identifiers": ["hermes_agent_app"],
        "name": "Hermes Agent",
        "manufacturer": "Nous Research",
        "model": "Home Assistant App",
        "sw_version": version,
    }
    messages = [(
        f"{DISCOVERY_PREFIX}/sensor/hermes_agent/version/config",
        {
            "name": "Version",
            "unique_id": "hermes_agent_version",
            "default_entity_id": "sensor.hermes_agent_app_version",
            "state_topic": f"{BASE_TOPIC}/app",
            "value_template": "{{ value_json.version }}",
            "json_attributes_topic": f"{BASE_TOPIC}/app",
            "entity_category": "diagnostic",
            "icon": "mdi:robot",
            "availability": availability,
            "device": app_device,
        },
    )]
    for name in profile_names:
        device = {
            "identifiers": [f"hermes_agent_profile_{name}"],
            "name": f"Hermes {name}",
            "manufacturer": "Nous Research",
            "model": "Hermes profile",
            "via_device": "hermes_agent_app",
        }
        state_topic = f"{BASE_TOPIC}/profile/{name}"
        entities = [
            ("binary_sensor", "status", {
                "name": "Status",
                "device_class": "connectivity",
                "value_template": "{{ 'ON' if value_json.online else 'OFF' }}",
                "json_attributes_topic": state_topic,
            }),
            ("binary_sensor", "gateway", {
                "name": "Gateway",
                "device_class": "running",
                "entity_category": "diagnostic",
                "value_template": "{{ 'ON' if value_json.gateway_running else 'OFF' }}",
            }),
        ]
        if api_enabled:
            entities.append(("binary_sensor", "api", {
                "name": "API",
                "device_class": "connectivity",
                "entity_category": "diagnostic",
                "value_template": "{{ 'ON' if value_json.api_healthy else 'OFF' }}",
            }))
        for component, key, config in entities:
            config.update({
                "unique_id": f"hermes_agent_{name}_{key}",
                "default_entity_id": f"{component}.hermes_agent_{name}" + ("" if key == "status" else f"_{key}"),
                "state_topic": state_topic,
                "availability": availability,
                "device": device,
            })
            messages.append((f"{DISCOVERY_PREFIX}/{component}/hermes_agent_{name}/{key}/config", config))
    return messages


def remove_legacy_rest_states(hass_url: str, token: str, profile_names: list, version: str) -> None:
    """Drop the pre-MQTT REST entities so MQTT discovery can own the names."""
    for entity_id in [LEGACY_REST_ENTITIES_PREFIX] + [f"{LEGACY_REST_ENTITIES_PREFIX}_{n}" for n in profile_names]:
        req = urllib.request.Request(
            f"{hass_url.rstrip('/')}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {token}"}, method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=5).close()
            log_msg(f"[ha-sensor-reporter] Removed legacy REST entity {entity_id}", level="info", version=version)
        except Exception:
            pass  # 404: already gone


def run_mqtt_loop(broker: dict, profile_names: list, api_ports: list, profile_homes: list,
                  api_enabled: bool, version: str) -> None:
    import paho.mqtt.client as mqtt  # system package python3-paho-mqtt

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hermes_agent_reporter")
    except AttributeError:  # paho-mqtt 1.x (Debian bookworm)
        client = mqtt.Client(client_id="hermes_agent_reporter")
    if broker.get("username"):
        client.username_pw_set(broker["username"], broker.get("password"))
    if broker.get("ssl"):
        client.tls_set()
    # Broker marks every entity unavailable when the App (and this process) dies.
    client.will_set(f"{BASE_TOPIC}/status", "offline", qos=1, retain=True)

    def on_connect(client, _userdata, _flags, *args):
        client.publish(f"{BASE_TOPIC}/status", "online", qos=1, retain=True)
        for topic, payload in discovery_messages(profile_names, version, api_enabled):
            client.publish(topic, json.dumps(payload), qos=1, retain=True)
        last_payloads.clear()  # re-send states after every (re)connect

    last_payloads: dict = {}
    client.on_connect = on_connect
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    client.connect_async(broker["host"], int(broker.get("port", 1883)), keepalive=60)
    client.loop_start()
    log_msg(f"[ha-sensor-reporter] Publishing via MQTT discovery ({broker['host']})", level="info", version=version)

    while True:
        try:
            states = {f"{BASE_TOPIC}/app": {"version": version, "profile_count": len(profile_names),
                                             "profiles": profile_names}}
            for idx, (name, port) in enumerate(zip(profile_names, api_ports)):
                home = profile_homes[idx] if idx < len(profile_homes) else ""
                states[f"{BASE_TOPIC}/profile/{name}"] = profile_status(name, port, home, api_enabled)
            for topic, payload in states.items():
                body = json.dumps(payload, sort_keys=True)
                if last_payloads.get(topic) != body and client.is_connected():
                    client.publish(topic, body, qos=1, retain=True)
                    last_payloads[topic] = body
        except Exception as err:
            log_msg(f"[ha-sensor-reporter] Loop exception: {err}", is_err=True, version=version)
        time.sleep(POLL_SECONDS)


def run_rest_loop(hass_url: str, token: str, profile_names: list, api_ports: list,
                  profile_homes: list, api_enabled: bool, version: str) -> None:
    last_sent: dict = {}

    def report(entity_id: str, state: str, attributes: dict) -> None:
        key = json.dumps([state, attributes], sort_keys=True)
        previous = last_sent.get(entity_id)
        if previous and previous[0] == key and time.monotonic() - previous[1] < REFRESH_SECONDS:
            return
        if post_ha_state(hass_url, token, entity_id, state, attributes):
            last_sent[entity_id] = (key, time.monotonic())

    log_msg("[ha-sensor-reporter] MQTT unavailable; publishing via the Core REST API", level="info", version=version)
    while True:
        try:
            report("sensor.hermes_agent", "online", {
                "friendly_name": "Hermes Agent", "version": version,
                "profile_count": len(profile_names), "profiles": profile_names, "icon": "mdi:robot"})
            for idx, (name, port) in enumerate(zip(profile_names, api_ports)):
                home = profile_homes[idx] if idx < len(profile_homes) else ""
                st = profile_status(name, port, home, api_enabled)
                report(f"sensor.hermes_agent_{name}", st["status"], {
                    "friendly_name": f"Hermes Agent ({name})", "profile_name": name,
                    "api_port": port, "api_enabled": api_enabled, "api_healthy": st["api_healthy"],
                    "gateway_running": st["gateway_running"], "status": st["status"],
                    "icon": "mdi:robot-happy" if st["online"] else "mdi:robot-off"})
        except Exception as err:
            log_msg(f"[ha-sensor-reporter] Loop exception: {err}", is_err=True, version=version)
        time.sleep(POLL_SECONDS)


def run_reporter_loop(options_file: str, profile_names: list, api_ports: list, version: str,
                      profile_homes: list) -> None:
    hass_url = os.environ.get("HASS_URL") or "http://supervisor/core"
    token = os.environ.get("HASS_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""
    api_enabled = read_api_enabled(options_file)
    log_msg("[ha-sensor-reporter] Starting Home Assistant status sensor reporter...", level="info", version=version)

    broker = supervisor_mqtt_config()
    if broker:
        try:
            import paho.mqtt.client  # noqa: F401
        except ImportError:
            broker = None
            log_msg("[ha-sensor-reporter] paho-mqtt missing; using REST fallback", is_err=True, version=version)
    if broker:
        if token:
            remove_legacy_rest_states(hass_url, token, profile_names, version)
        run_mqtt_loop(broker, profile_names, api_ports, profile_homes, api_enabled, version)
        return
    if not token:
        log_msg("[ha-sensor-reporter] Warning: No Home Assistant token available; sensor reporting disabled.", is_err=True, version=version)
        return
    run_rest_loop(hass_url, token, profile_names, api_ports, profile_homes, api_enabled, version)


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        opts_file = sys.argv[1]
        version_str = sys.argv[2]
        names = sys.argv[3].split(",") if sys.argv[3] else []
        ports = [int(p) for p in sys.argv[4].split(",")] if sys.argv[4] else []
        homes = sys.argv[5].split(",") if len(sys.argv) >= 6 and sys.argv[5] else []
        run_reporter_loop(opts_file, names, ports, version_str, homes)
