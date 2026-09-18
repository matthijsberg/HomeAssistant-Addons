"""
Open HEMS: Secrets & Configuration Store
========================================
Handles secure, isolated credentials management and JSON configuration files.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any


CONFIG_FILE = Path("/config/heatpump_config.json")
PARAMS_FILE = Path("/config/heatpump_model_parameters.json")
SECRETS_FILE = Path("/config/open_hems_secrets.json")
HA_API_CONFIG = Path("/config/.ha_api_config.json")


def load_json(p: Path, default=None) -> Any:
    """Safely loads JSON from path or returns default."""
    if default is None:
        default = {}
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Warning loading {p}: {e}")
    return default


def save_json(p: Path, data: Any) -> None:
    """Atomically saves data to JSON file with standard 0644 permissions."""
    tmp = f"{p}.tmp.{os.getpid()}"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o644)
    except Exception:
        pass


def load_secrets() -> dict:
    """Loads private credentials from the isolated vault."""
    if SECRETS_FILE.exists():
        try:
            with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning loading secrets: {e}")
    return {"influxdb": {}, "mqtt": {}}


def save_secret(domain: str, conn_id: str, secret: str) -> None:
    """Saves a credential to the isolated private vault."""
    if not secret:
        return
    sec = load_secrets()
    sec.setdefault(domain, {})[conn_id] = secret
    tmp = f"{SECRETS_FILE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sec, f, indent=2)
    os.replace(tmp, SECRETS_FILE)


def get_secret(domain: str, conn_id: str, default: str = "") -> str:
    """Retrieves a credential from the private vault."""
    sec = load_secrets()
    return sec.get(domain, {}).get(conn_id, default)


def ensure_framework_defaults(cfg: dict) -> None:
    """Initializes the generic framework defaults if config is fresh."""
    dirty = False

    # Multi-instance InfluxDB connections
    if "influxdb_connections" not in cfg or not cfg["influxdb_connections"]:
        cfg["influxdb_connections"] = [
            {
                "id": "local_ha_influxdb",
                "name": "Lokale Open HEMS InfluxDB (1.8)",
                "type": "influx_v1",
                "url": cfg.get("influxdb", {}).get("url", "http://a0d7b954-influxdb:8086"),
                "database": "openhems",
                "read_database": "openhems",
                "username": "openhems",
                "password": "",
                "retention_policy": "autogen",
                "enabled": True,
                "is_default": True
            }
        ]
        dirty = True
    else:
        for c in cfg["influxdb_connections"]:
            if c.get("id") == "local_ha_influxdb":
                if c.get("database") in ["hermes", "hassio"]:
                    c["database"] = "openhems"
                    dirty = True
                if c.get("username") == "hermes":
                    c["username"] = "openhems"
                    dirty = True

    # Multi-instance MQTT connections
    if "mqtt_connections" not in cfg or not cfg["mqtt_connections"]:
        cfg["mqtt_connections"] = [
            {
                "id": "local_mosquitto",
                "name": "Lokale Mosquitto Broker",
                "type": "standard",
                "host": cfg.get("mqtt", {}).get("host", "core-mosquitto"),
                "port": cfg.get("mqtt", {}).get("port", 1883),
                "base_topic": cfg.get("mqtt", {}).get("base_topic", "openhems"),
                "client_id": cfg.get("mqtt", {}).get("client_id", "open-hems-collector"),
                "username": cfg.get("mqtt", {}).get("username", ""),
                "password": cfg.get("mqtt", {}).get("password", ""),
                "tls": cfg.get("mqtt", {}).get("tls", False),
                "enabled": True,
                "is_default": True
            }
        ]
        dirty = True

    # Multi-instance device catalogue
    if "devices" not in cfg or not cfg["devices"]:
        cfg["devices"] = [
            {
                "id": "daikin_altherma_heat_pump",
                "name": "Daikin Altherma Warmtepomp",
                "type": "heat_pump",
                "adapter": "daikin_p1p2",
                "capabilities": ["modulate", "smart_grid_ready", "read_power", "read_temperatures"],
                "ha_power_entity": "sensor.warmtepomp_power",
                "parameters": {"mod_floor_w": 950, "cv_flow_lag_hours": 3.5}
            },
            {
                "id": "inepro_pv_meter",
                "name": "Inepro 103 Zonnepanelen (5.76 kWp)",
                "type": "solar_inverter",
                "adapter": "p1_modbus",
                "capabilities": ["read_power", "read_production"],
                "ha_power_entity": "sensor.solar_power",
                "parameters": {"kwp": 5.76, "inverter_limit_kw": 5.5}
            },
            {
                "id": "dhw_storage_350l",
                "name": "SWW Boilervat 350L",
                "type": "thermal_buffer",
                "adapter": "temperature_sensor",
                "capabilities": ["read_temperature", "read_energy"],
                "ha_temp_entity": "sensor.hc_dhw_temperature_r5t_dhw_tank",
                "parameters": {"volume_liters": 350}
            },
            {
                "id": "deye_home_battery",
                "name": "Deye Hybride Thuisaccu (10 kW / 10 kWh)",
                "type": "home_battery",
                "adapter": "deye_modbus_tcp",
                "capabilities": ["read_power", "read_soc", "set_power_limit", "set_mode"],
                "ha_power_entity": "sensor.battery_power",
                "parameters": {"capacity_kwh": 10.0, "max_charge_power_w": 5000, "max_discharge_power_w": 5000}
            }
        ]
        dirty = True

    if dirty:
        save_json(CONFIG_FILE, cfg)
