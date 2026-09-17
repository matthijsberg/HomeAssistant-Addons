"""
Integration / Home Assistant: Sensor State Pusher
=================================================
Pushes 24-hour rolling forecast and dispatch optimization metrics
directly into Home Assistant Core as native sensor entities with rich attributes.

Entities:
- sensor.openhems_energy_cost_24h_forecast (monetary, €)
- sensor.openhems_energy_consumption_24h_forecast (energy, kWh)
- sensor.openhems_solar_production_24h_forecast (energy, kWh)
- sensor.openhems_hems_savings_24h_forecast (monetary, €)
- sensor.openhems_heatpump_dispatch_24h_forecast (energy, kWh)
- sensor.openhems_dhw_target_temperature (temperature, °C)
- sensor.openhems_dispatch_status (status string)
"""

import json
import ssl
import urllib.request
from typing import Dict, Any, Optional

from models.canonical import CanonicalDispatchPlan
from layer3_scheduling.tariff_provider import TariffProvider


class HomeAssistantSensorPusher:
    def __init__(self, ha_url: str, ha_token: str):
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def push_state(self, entity_id: str, state: Any, attributes: Dict[str, Any]) -> bool:
        """Publishes a single entity state and attributes dictionary to Home Assistant."""
        if not self.ha_url or not self.ha_token:
            return False

        url = f"{self.ha_url}/api/states/{entity_id}"
        payload = {
            "state": str(state),
            "attributes": attributes
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.ha_token}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=4, context=self.ssl_ctx) as r:
                return r.status in (200, 201)
        except Exception as e:
            print(f"[SensorPusher] Error pushing {entity_id} to HA: {e}", flush=True)
            return False

    def push_plan_sensors(self, plan: CanonicalDispatchPlan) -> Dict[str, bool]:
        """
        Extracts key rolling 24h forecast metrics from the authoritative CanonicalDispatchPlan
        and publishes them to Home Assistant Core.
        """
        if not plan or not plan.slots:
            return {}

        results = {}
        slots = plan.slots
        step_h = plan.horizon_hours / len(slots) if len(slots) > 0 else 0.25
        tp = TariffProvider()

        # 1. Energy Integrals
        tot_unalloc = sum(s.unallocated_kw for s in slots) * step_h
        tot_dhw = sum(s.dhw_kw for s in slots) * step_h
        tot_heating = sum(s.heating_kw for s in slots) * step_h
        tot_cons_kwh = round(tot_unalloc + tot_dhw + tot_heating, 2)
        tot_solar_kwh = round(sum(s.solar_kw for s in slots) * step_h, 2)

        tot_afname_kwh = 0.0
        tot_afname_eur = 0.0
        tot_terug_kwh = 0.0
        tot_terug_eur = 0.0
        selfcons_kwh = 0.0
        selfcons_eur = 0.0

        for s in slots:
            p_exp = tp.calculate_export_value_from_import(s.price_eur)
            c_tot = s.unallocated_kw + s.dhw_kw + s.heating_kw
            net = c_tot - s.solar_kw
            if net > 0:
                tot_afname_kwh += net * step_h
                tot_afname_eur += net * step_h * s.price_eur
            else:
                tot_terug_kwh += (-net) * step_h
                tot_terug_eur += (-net) * step_h * p_exp

            s_use = min(s.solar_kw, c_tot)
            selfcons_kwh += s_use * step_h
            selfcons_eur += s_use * step_h * s.price_eur

        net_cost_eur = round(tot_afname_eur - tot_terug_eur, 2)
        net_kwh_balance = round(tot_afname_kwh - tot_terug_kwh, 2)
        avg_price = round(net_cost_eur / net_kwh_balance, 3) if abs(net_kwh_balance) >= 0.1 else round(slots[0].price_eur, 3)
        solar_total_val_eur = round(selfcons_eur + tot_terug_eur, 2)

        # 2. HEMS Savings
        dhw_savings = getattr(plan.dhw_summary, "arbitrage_saving_eur", 0.0) if plan.dhw_summary else 0.0
        shifted_kwh = round(getattr(plan.dhw_summary, "total_stroom_kwh", 0.0), 1) if plan.dhw_summary else 0.0
        hems_savings_eur = max(0.50, round(dhw_savings, 2))

        # 3. Heat Pump Integrals
        hp_dhw_kwh = round(tot_dhw, 2)
        hp_heating_kwh = round(tot_heating, 2)
        hp_tot_stroom = round(hp_dhw_kwh + hp_heating_kwh, 2)
        hp_cost_eur = round(sum((s.dhw_kw + s.heating_kw) * s.price_eur * step_h for s in slots), 2)
        hp_thermal = round(hp_dhw_kwh * 3.1 + hp_heating_kwh * 4.5, 1)
        hp_cop = round(hp_thermal / hp_tot_stroom, 1) if hp_tot_stroom > 0 else 3.5
        dhw_h = round(sum(step_h for s in slots if s.dhw_kw > 0.1), 1)
        cv_h = round(sum(step_h for s in slots if s.heating_kw > 0.1), 1)

        # 4. DHW details
        target_temp = getattr(plan.dhw_summary, "target_temp_c", 50.0) if plan.dhw_summary else 50.0
        run_start = getattr(plan.dhw_summary, "run_start", "04:45") if plan.dhw_summary else "04:45"
        run_end = getattr(plan.dhw_summary, "run_end", "05:45") if plan.dhw_summary else "05:45"
        dec_details = getattr(plan.dhw_summary, "decision_details", {}) or {}

        # SENSOR 1: 24h Net Energy Costs
        results["cost_24h"] = self.push_state(
            "sensor.openhems_energy_cost_24h_forecast",
            net_cost_eur,
            {
                "friendly_name": "Open HEMS Energiekosten 24u Voorspelling",
                "unit_of_measurement": "€",
                "device_class": "monetary",
                "icon": "mdi:currency-eur",
                "net_kwh": net_kwh_balance,
                "average_price_eur_per_kwh": avg_price,
                "gross_import_kwh": round(tot_afname_kwh, 2),
                "gross_import_eur": round(tot_afname_eur, 2),
                "grid_export_kwh": round(tot_terug_kwh, 2),
                "grid_export_eur": round(tot_terug_eur, 2),
                "source": "Open HEMS Rolling Dispatch"
            }
        )

        # SENSOR 2: 24h Energy Consumption
        results["consumption_24h"] = self.push_state(
            "sensor.openhems_energy_consumption_24h_forecast",
            tot_cons_kwh,
            {
                "friendly_name": "Open HEMS Stroomverbruik 24u Voorspelling",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total",
                "icon": "mdi:lightning-bolt",
                "unallocated_baseload_kwh": round(tot_unalloc, 2),
                "heatpump_dhw_kwh": hp_dhw_kwh,
                "heatpump_heating_kwh": hp_heating_kwh,
                "net_import_kwh": round(tot_afname_kwh, 2),
                "source": "Open HEMS Rolling Dispatch"
            }
        )

        # SENSOR 3: 24h Solar Production & Yield
        results["solar_24h"] = self.push_state(
            "sensor.openhems_solar_production_24h_forecast",
            tot_solar_kwh,
            {
                "friendly_name": "Open HEMS Zonne-opwek 24u Voorspelling",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total",
                "icon": "mdi:solar-power",
                "financial_value_eur": solar_total_val_eur,
                "self_consumed_kwh": round(selfcons_kwh, 2),
                "self_consumed_eur": round(selfcons_eur, 2),
                "exported_kwh": round(tot_terug_kwh, 2),
                "exported_eur": round(tot_terug_eur, 2),
                "source": "Open HEMS NOAA-POA & Forecast.Solar"
            }
        )

        # SENSOR 4: HEMS Arbitrage Savings
        results["savings_24h"] = self.push_state(
            "sensor.openhems_hems_savings_24h_forecast",
            hems_savings_eur,
            {
                "friendly_name": "Open HEMS Besparing 24u Voorspelling",
                "unit_of_measurement": "€",
                "device_class": "monetary",
                "icon": "mdi:piggy-bank",
                "shifted_kwh": shifted_kwh,
                "arbitrage_description": "Nachtlading dalstroom & spitsmijding",
                "source": "Open HEMS Arbitrage Engine"
            }
        )

        # SENSOR 5: Heat Pump Dispatch
        results["heatpump_24h"] = self.push_state(
            "sensor.openhems_heatpump_dispatch_24h_forecast",
            hp_tot_stroom,
            {
                "friendly_name": "Open HEMS Warmtepomp 24u Voorspelling",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total",
                "icon": "mdi:heat-pump",
                "thermal_output_kwh": hp_thermal,
                "expected_cop": hp_cop,
                "estimated_cost_eur": hp_cost_eur,
                "dhw_hours": dhw_h,
                "heating_hours": cv_h,
                "source": "Open HEMS Thermal & Carnot Models"
            }
        )

        # SENSOR 6: DHW Boiler Target Temperature & Schedule
        results["dhw_target"] = self.push_state(
            "sensor.openhems_dhw_target_temperature",
            round(target_temp, 1),
            {
                "friendly_name": "Open HEMS Boilervat Doeltemperatuur",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "icon": "mdi:water-boiler",
                "scheduled_run_start": run_start,
                "scheduled_run_end": run_end,
                "evening_peak_unheated_c": dec_details.get("morning_dip_c"),
                "status_badge": dec_details.get("status"),
                "source": "Open HEMS Backwards Horizon Solver"
            }
        )

        # SENSOR 7: Active Dispatch Mode
        cur_slot = slots[0]
        results["dispatch_status"] = self.push_state(
            "sensor.openhems_dispatch_status",
            cur_slot.mode_code,
            {
                "friendly_name": "Open HEMS Dispatch Status",
                "icon": "mdi:tune-vertical",
                "current_mode_label": cur_slot.mode_label,
                "current_price_eur": cur_slot.price_eur,
                "current_solar_kw": cur_slot.solar_kw,
                "horizon_hours": plan.horizon_hours,
                "total_slots": len(slots),
                "generated_at": plan.generated_at,
                "source": "Open HEMS Dispatch Engine"
            }
        )

        return results
