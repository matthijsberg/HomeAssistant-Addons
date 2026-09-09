"""
Open HEMS Site-Specific Adapter: Daikin Altherma P1P2 State Classifier & Power Disaggregator.
Author: Open HEMS Core Architecture
License: Apache-2.0

Separation Principle:
This module contains site- and hardware-specific classification logic for Daikin Altherma 3 H HT
via P1P2-bridge telemetry and HA entity fallbacks. It is cleanly isolated in `site_adapters/`
so that Open HEMS Core models and scheduling layers remain generic and modular.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class HeatPumpDisaggregation:
    mode: str                    # 'DHW', 'HEATING', 'COOLING', 'STANDBY'
    total_power_w: float
    dhw_w: float
    heating_w: float
    cooling_w: float
    standby_w: float
    confidence: float
    source: str                  # 'mqtt_p1p2', 'ha_entities', 'power_threshold'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "total_power_w": round(self.total_power_w, 1),
            "dhw_w": round(self.dhw_w, 1),
            "heating_w": round(self.heating_w, 1),
            "cooling_w": round(self.cooling_w, 1),
            "standby_w": round(self.standby_w, 1),
            "confidence": self.confidence,
            "source": self.source
        }


class DaikinP1P2StateClassifier:
    """
    Evaluates raw physical inputs from Daikin P1P2 MQTT bridge topics and
    Home Assistant fallback entities to classify current heat pump operational state
    and disaggregate actual electrical power draw into pure physical buckets.
    """

    STANDBY_THRESHOLD_W = 48.0  # Daikin Altherma standby is typically 30-36 W

    @classmethod
    def classify(
        cls,
        total_power_w: float,
        mqtt_cache: Optional[Dict[str, Any]] = None,
        ha_states: Optional[Dict[str, Any]] = None
    ) -> HeatPumpDisaggregation:
        mqtt_cache = mqtt_cache or {}
        ha_states = ha_states or {}
        p_tot = max(0.0, float(total_power_w))

        # 1. Check standby threshold first
        # If total electrical power is below standby threshold, compressor is idle
        if p_tot < cls.STANDBY_THRESHOLD_W:
            return HeatPumpDisaggregation(
                mode="STANDBY",
                total_power_w=p_tot,
                dhw_w=0.0,
                heating_w=0.0,
                cooling_w=0.0,
                standby_w=p_tot,
                confidence=1.0,
                source="power_threshold"
            )

        # 2. Extract MQTT P1P2 Signals (Highest Priority - direct from Daikin bus)
        p1p2_action = str(mqtt_cache.get("P1P2/P/P1P2MQTT/bridge0/C/9/Action_Heating_Cooling_Auto_Off", "")).lower()
        p1p2_dhw_demand = str(mqtt_cache.get("P1P2/P/P1P2MQTT/bridge0/S/1/DHW_Demand", "")).strip()
        p1p2_valve_dhw = str(mqtt_cache.get("P1P2/P/P1P2MQTT/bridge0/S/1/Valve_DHW_Tank", "")).strip()
        p1p2_climate_heat = str(mqtt_cache.get("P1P2/P/P1P2MQTT/bridge0/S/1/Climate_Heating", "")).strip()
        p1p2_climate_cool = str(mqtt_cache.get("P1P2/P/P1P2MQTT/bridge0/S/1/Climate_Cooling", "")).strip()

        # 3. Extract Home Assistant Fallback Signals
        ha_select = str(ha_states.get("select.daily_energy_usage_sums_wp", "")).lower()
        ha_compressor = str(ha_states.get("binary_sensor.hc_mode_compressor", "")).lower()
        ha_dhw_demand = str(ha_states.get("binary_sensor.hc_dhw_dhw_demand", "")).lower()
        ha_heat_mode = str(ha_states.get("binary_sensor.hc_mode_climate_heating", "")).lower()
        ha_cool_mode = str(ha_states.get("binary_sensor.hc_mode_climate_cooling", "")).lower()

        # Decision Tree:
        # A. DHW Demand / Boiler Reheat Mode
        is_dhw = (
            p1p2_action == "fan" or
            p1p2_dhw_demand == "1" or
            ha_dhw_demand == "on" or
            ha_select == "dhw"
        )
        if is_dhw:
            return HeatPumpDisaggregation(
                mode="DHW",
                total_power_w=p_tot,
                dhw_w=p_tot,
                heating_w=0.0,
                cooling_w=0.0,
                standby_w=0.0,
                confidence=0.98 if p1p2_action or p1p2_dhw_demand else 0.90,
                source="mqtt_p1p2" if p1p2_action or p1p2_dhw_demand else "ha_entities"
            )

        # B. Cooling Mode
        is_cooling = (
            p1p2_action == "cooling" or
            p1p2_climate_cool == "1" or
            ha_cool_mode == "on" or
            ha_select == "cooling"
        )
        if is_cooling:
            return HeatPumpDisaggregation(
                mode="COOLING",
                total_power_w=p_tot,
                dhw_w=0.0,
                heating_w=0.0,
                cooling_w=p_tot,
                standby_w=0.0,
                confidence=0.98 if p1p2_action or p1p2_climate_cool else 0.90,
                source="mqtt_p1p2" if p1p2_action or p1p2_climate_cool else "ha_entities"
            )

        # C. Space Heating (CV) Mode
        is_heating = (
            p1p2_action == "heating" or
            p1p2_climate_heat == "1" or
            ha_heat_mode == "on" or
            ha_select == "heating"
        )
        if is_heating:
            return HeatPumpDisaggregation(
                mode="HEATING",
                total_power_w=p_tot,
                dhw_w=0.0,
                heating_w=p_tot,
                cooling_w=0.0,
                standby_w=0.0,
                confidence=0.98 if p1p2_action or p1p2_climate_heat else 0.90,
                source="mqtt_p1p2" if p1p2_action or p1p2_climate_heat else "ha_entities"
            )

        # D. Explicit Standby / Idle
        if p1p2_action in ["idle", "off"] or ha_compressor == "off" or ha_select == "standby":
            return HeatPumpDisaggregation(
                mode="STANDBY",
                total_power_w=p_tot,
                dhw_w=0.0,
                heating_w=0.0,
                cooling_w=0.0,
                standby_w=p_tot,
                confidence=0.95,
                source="mqtt_p1p2" if p1p2_action else "ha_entities"
            )

        # Default: if power is draw (>48W) and no signal matched, assume heating
        return HeatPumpDisaggregation(
            mode="HEATING",
            total_power_w=p_tot,
            dhw_w=0.0,
            heating_w=p_tot,
            cooling_w=0.0,
            standby_w=0.0,
            confidence=0.60,
            source="fallback_inferred"
        )
