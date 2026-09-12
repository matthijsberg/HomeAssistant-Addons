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
            cv_master_switch_on=bool(state_dict.get("cv_master_switch_on", True))
        )
