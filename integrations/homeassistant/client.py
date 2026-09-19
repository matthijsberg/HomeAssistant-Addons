"""
Home Assistant Core REST API Client
===================================
Handles all HTTP communications, entity discovery, and service calls to Home Assistant Core.
"""

import os
import ssl
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Tuple, Optional, Dict, Any, List

from api.secrets_store import load_secrets, load_json, HA_API_CONFIG
from integrations.daikin_altherma.actuator import DaikinActuator


_WORKING_HA_BASE_URL: Optional[str] = None


def get_ha_client_config() -> Tuple[str, str]:
    """
    Returns (ha_url, token) for Home Assistant Core REST API.
    Auto-discovers and caches the responsive endpoint among:
      - Direct internal HA Docker bridge: https://172.30.32.1:8123
      - Direct internal Docker service name: https://homeassistant:8123
      - User-configured URL in secrets
      - http://supervisor/core
    """
    global _WORKING_HA_BASE_URL
    sec = load_secrets()
    ha_sec = sec.get("homeassistant", {})
    token = ha_sec.get("token") or os.environ.get("HASS_TOKEN", "")

    if not token and HA_API_CONFIG.exists():
        cfg = load_json(HA_API_CONFIG)
        token = cfg.get("HASS_TOKEN")

    if not token and os.path.exists("/data/options.json"):
        try:
            with open("/data/options.json") as f:
                opts = json.load(f)
                token = opts.get("homeassistant_token") or token
        except Exception:
            pass

    if _WORKING_HA_BASE_URL is not None:
        return _WORKING_HA_BASE_URL, token

    configured_url = ha_sec.get("url") or os.environ.get("HASS_URL")
    candidates = [
        "https://172.30.32.1:8123",
        "https://homeassistant:8123",
        configured_url,
        "http://supervisor/core",
        "https://hass.b3rg.nl:8123"
    ]
    seen = set()
    uniq_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            uniq_candidates.append(c)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for candidate in uniq_candidates:
        try:
            req = urllib.request.Request(f"{candidate}/api/states/zone.home", headers=headers)
            with urllib.request.urlopen(req, timeout=1.5, context=ctx) as r:
                if r.status in [200, 201]:
                    _WORKING_HA_BASE_URL = str(candidate)
                    return _WORKING_HA_BASE_URL, token
        except Exception:
            continue

    default_url = configured_url or "https://172.30.32.1:8123"
    return default_url, token


def fetch_ha_entities() -> List[Dict[str, Any]]:
    """Queries Home Assistant Core REST API for available entities for dropdown selection."""
    ha_url, token = get_ha_client_config()
    if not token:
        return []

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(f"{ha_url}/api/states", headers=headers)
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            states = json.loads(r.read().decode("utf-8"))
            filtered = []
            for s in states:
                eid = s.get("entity_id", "")
                domain = eid.split(".")[0]
                if domain in ["sensor", "switch", "climate", "binary_sensor", "input_boolean", "weather"]:
                    fname = s.get("attributes", {}).get("friendly_name") or eid
                    filtered.append({
                        "entity_id": eid,
                        "friendly_name": fname,
                        "domain": domain,
                        "unit": s.get("attributes", {}).get("unit_of_measurement"),
                        "state": s.get("state")
                    })
            return sorted(filtered, key=lambda x: x["friendly_name"].lower())
    except Exception as e:
        print(f"Warning fetching HA entities: {e}")
        return []


def get_ha_states_map() -> Dict[str, Dict[str, Any]]:
    """Returns a dict mapping entity_id -> state dict from HA Core."""
    return {e["entity_id"]: e for e in fetch_ha_entities()}


def call_ha_service_detailed(domain: str, service: str, service_data: dict) -> Tuple[bool, Optional[str]]:
    """Calls a Home Assistant Core REST API service and returns success status plus error message."""
    global _WORKING_HA_BASE_URL
    ha_url, token = get_ha_client_config()
    if not token:
        return False, "Geen Home Assistant token geconfigureerd in options.json of environment"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"{ha_url}/api/services/{domain}/{service}"
    try:
        req = urllib.request.Request(url, data=json.dumps(service_data).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            if r.status in [200, 201]:
                return True, None
            return False, f"HTTP status {r.status}"
    except urllib.error.HTTPError as he:
        return False, f"HTTP Fout {he.code}: {he.reason}"
    except urllib.error.URLError as ue:
        _WORKING_HA_BASE_URL = None
        return False, f"Verbindingsfout naar HA ({ha_url}): {ue.reason}"
    except Exception as e:
        _WORKING_HA_BASE_URL = None
        return False, str(e)


def call_ha_service(domain: str, service: str, service_data: dict) -> bool:
    """Calls a Home Assistant Core REST API service and logs if the call fails."""
    success, err_msg = call_ha_service_detailed(domain, service, service_data)
    if not success:
        print(f"Error calling HA service {domain}.{service}: {err_msg}")
    return success


def make_daikin_ha_actuator() -> DaikinActuator:
    """Creates a configured DaikinActuator delegating select, switch and climate calls to Home Assistant Core."""
    states_map = get_ha_states_map()

    def select_caller(option_name: str) -> bool:
        eid = "input_select.warmtepomp_smart_grid_modus"
        curr_opt = states_map.get(eid, {}).get("state")
        if curr_opt == option_name:
            # Idempotent guard: already in target state, do NOT fire redundant service calls
            return True
        ok = call_ha_service("input_select", "select_option", {"entity_id": eid, "option": option_name})
        if not ok:
            raise RuntimeError(f"Home Assistant service call mislukt voor {eid} -> {option_name}")
        states_map[eid] = {"state": option_name}
        return True

    def switch_caller(switch_name: str, state: bool):
        entity_map = {
            "s10s": "switch.warmtepomp_smart_grid_1_s10s",
            "s11s": "switch.warmtepomp_smart_grid_2_s11s",
            "cv_master": "switch.hc_mode_altherma_on"
        }
        eid = entity_map.get(switch_name)
        if eid:
            curr_state = (states_map.get(eid, {}).get("state") == "on")
            if curr_state == state:
                # Idempotent guard: switch already in desired state
                return True
            service = "turn_on" if state else "turn_off"
            ok = call_ha_service("switch", service, {"entity_id": eid})
            if not ok:
                raise RuntimeError(f"Home Assistant service call mislukt voor {eid} -> {service}")
            states_map[eid] = {"state": "on" if state else "off"}
        return True

    def climate_caller(climate_name: str, temp: float):
        eid = "climate.hc_dhw_dhw_setpoint"
        curr_temp_str = states_map.get(eid, {}).get("attributes", {}).get("temperature")
        try:
            if curr_temp_str is not None and abs(float(curr_temp_str) - temp) < 0.2:
                # Idempotent guard: temperature setpoint already set
                return True
        except (ValueError, TypeError):
            pass
        ok = call_ha_service("climate", "set_temperature", {"entity_id": eid, "temperature": temp})
        if not ok:
            raise RuntimeError(f"Home Assistant service call mislukt voor {eid} -> {temp}°C")
        return True

    return DaikinActuator(
        switch_caller=switch_caller,
        climate_caller=climate_caller,
        select_caller=select_caller
    )
