"""
Integrations: Daikin Altherma Telemetry Reader
==============================================
Reads raw hardware states and temperatures from P1P2 MQTT bridge or HA states.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class DaikinTelemetry:
    tank_temperature_c: Optional[float]
    outdoor_temperature_c: Optional[float]
    room_temperature_c: Optional[float]
    flow_temperature_c: Optional[float]
    electrical_power_w: float
    heat_power_w: float
    current_mode: str
    cv_master_switch_on: bool
    is_heating_enabled: bool = True


class DaikinReader:
    """Telemetry parser and reader for Daikin Altherma."""

    @classmethod
    def parse_raw_state(cls, state_dict: Dict[str, Any]) -> DaikinTelemetry:
        return DaikinTelemetry(
            tank_temperature_c=state_dict.get("tank_temperature_c"),
            outdoor_temperature_c=state_dict.get("outdoor_temperature_c"),
            room_temperature_c=state_dict.get("room_temperature_c"),
            flow_temperature_c=state_dict.get("flow_temperature_c"),
            electrical_power_w=float(state_dict.get("electrical_power_w", 0.0)),
            heat_power_w=float(state_dict.get("heat_power_w", 0.0)),
            current_mode=str(state_dict.get("current_mode", "standby")),
            cv_master_switch_on=bool(state_dict.get("cv_master_switch_on", True)),
            is_heating_enabled=bool(state_dict.get("is_heating_enabled", True))
        )

    @classmethod
    def is_space_heating_circuit_enabled(cls, states_map: Dict[str, Any]) -> bool:
        """
        Determines if space heating (CV) is physically and logically enabled in Home Assistant.
        Multi-Entity Rule: If ANY authoritative heating entity reports 'off', heating is disabled.
        Authoritative entities:
          1. switch.hc_mode_altherma_on (Master Daikin Climate Enable switch)
          2. climate.hc_room_room_heating (Daikin Room Heating Circuit)
          3. climate.woonkamer_climate_daikin (Living Room Thermostat)
        """
        if not states_map:
            return True

        master_sw = states_map.get("switch.hc_mode_altherma_on", {}).get("state")
        if master_sw == "off":
            return False

        circuit = states_map.get("climate.hc_room_room_heating", {}).get("state")
        if circuit == "off":
            return False

        thermostat = states_map.get("climate.woonkamer_climate_daikin", {}).get("state")
        if thermostat == "off":
            return False

        return True
