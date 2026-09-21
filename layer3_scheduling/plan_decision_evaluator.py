"""
Open HEMS: Plan Decision Evaluator & Real-time Audit Logger
===========================================================
Layer 3 Scheduling & Arbitration:
  - Opportunistic DHW run merging & live plan slot mutations
  - Night boiler pre-evaluation & audit logging
  - Daily planner decision auditing (peak lockouts, space heating policy, daytime arbitrage)
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional

from models.canonical import StandardizedState
from layer3_scheduling.plan_store import PlanStore
from layer3_scheduling.decision_audit import DecisionAuditLogger
from layer3_scheduling.opportunistic_merger import OpportunisticDHWMerger
from api.infra_diagnostics import write_hems_annotation
from integrations.homeassistant.client import get_ha_states_map, call_ha_service


GLOBAL_OPPORTUNISTIC_MERGE = None
_LAST_LOGGED_DECISION: Dict[str, Any] = {"state": None, "ts": 0.0}
_LAST_NIGHT_AUDIT_LOG: Dict[str, Any] = {"state": None, "ts": 0.0}
_LAST_PLAN_LOGS: Dict[str, Any] = {
    "peaks_key": None,
    "cv_key": None,
    "dhw_strategy_key": None,
    "last_ts": 0.0
}


def evaluate_and_apply_dhw_run_merger(
    plan: Any,
    t_live: float,
    current_solar_kw: float = 0.0,
    current_price_eur: float = 0.24
) -> Any:
    """
    Evaluates whether the heat pump has autonomously started heating DHW (e.g. after a shower)
    and merges an upcoming 60C solar boost run into the active run if cost-effective.
    """
    global GLOBAL_OPPORTUNISTIC_MERGE
    states_map = get_ha_states_map()

    wp_power_sensor = states_map.get("sensor.warmtepomp_power", {})
    try:
        wp_power = float(wp_power_sensor.get("state", 0.0))
    except (ValueError, TypeError):
        wp_power = 0.0

    dhw_demand_sensor = states_map.get("binary_sensor.hc_dhw_dhw_demand", {})
    dhw_valve_sensor = states_map.get("binary_sensor.hc_dhw_valve_dhw_tank", {})
    is_dhw_valve = (dhw_demand_sensor.get("state") == "on" or dhw_valve_sensor.get("state") == "on")

    # Hydraulic fidelity: Only consider active heating if 3-way valve is physically routed to DHW!
    # Never trigger on high electrical compressor power alone (which can be space heating).
    is_actively_heating = (wp_power > 600.0 and is_dhw_valve)

    is_hard_lockout = False
    if plan and getattr(plan, "dynamic_peaks", None):
        for p_peak in plan.dynamic_peaks:
            if p_peak.get("is_hard_lockout") and p_peak.get("start_idx", 99) <= 0 <= p_peak.get("end_idx", -1):
                is_hard_lockout = True
                break

    merge_res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=is_actively_heating,
        current_tank_temp_c=t_live,
        current_solar_kw=current_solar_kw,
        current_price_eur=current_price_eur,
        is_hard_lockout_now=is_hard_lockout,
        current_power_kw=round(wp_power / 1000.0, 2)
    )

    GLOBAL_OPPORTUNISTIC_MERGE = merge_res

    if merge_res.should_merge:
        # Actuate HA purely via Smart Grid relays (SG4) and hydraulic interlock.
        # DO NOT touch climate.hc_dhw_dhw_setpoint; Daikin heats to boost temp natively in SG4.
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_1_s10s"})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_2_s11s"})
        call_ha_service("switch", "turn_off", {"entity_id": "switch.hc_mode_altherma_on"})

        # Cancel the upcoming planned slots in the plan!
        for slot_idx in merge_res.cancelled_slots:
            if slot_idx < len(plan.slots):
                plan.slots[slot_idx].mode_code = StandardizedState.NORMAL
                plan.slots[slot_idx].mode_label = "Normaal (50°C)"
                plan.slots[slot_idx].dhw_kw = 0.0

        run_pwr = max(3.0, round(wp_power / 1000.0, 2))
        # Reflect active run on current slot (slot 0) and next slots
        if plan and plan.slots:
            for run_i in range(min(4, len(plan.slots))):
                plan.slots[run_i].mode_code = StandardizedState.MAX_ON
                plan.slots[run_i].mode_label = "Zonnebuffer (Fusie tot 60°C)"
                plan.slots[run_i].dhw_kw = run_pwr
                plan.slots[run_i].heating_kw = 0.0

        store = PlanStore.get_instance()
        store.register_in_flight_run(target_temp_c=60.0, mode="max_on")
        store.publish_plan(plan)
        
        now_ts = time.time()
        if _LAST_LOGGED_DECISION.get("state") != "max_on" or (now_ts - _LAST_LOGGED_DECISION.get("ts", 0)) >= 900.0:
            _LAST_LOGGED_DECISION["state"] = "max_on"
            _LAST_LOGGED_DECISION["ts"] = now_ts
            write_hems_annotation(
                event_type="run_merger",
                title="⚡ DHW Zonnebuffer Fusie (60°C)",
                description=merge_res.decision_explanation,
                state_code="max_on",
                power_kw=run_pwr,
                target_temp_c=60.0,
                savings_eur=merge_res.savings_estimate_eur
            )
            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="opportunistic_merge",
                chosen_mode="max_on",
                target_temp_c=60.0,
                inputs={
                    "tank_temp_c": t_live,
                    "wp_power_w": wp_power,
                    "solar_kw": current_solar_kw,
                    "current_price_eur": current_price_eur,
                    "price_tolerance_eur": 0.05
                },
                reason=merge_res.reason,
                explanation=merge_res.decision_explanation,
                savings_estimate_eur=merge_res.savings_estimate_eur
            )
    elif is_actively_heating and t_live >= 49.8:
        # Target reached! Stop forced mode and return relays to SG2 (Automatisch)
        call_ha_service("switch", "turn_off", {"entity_id": "switch.warmtepomp_smart_grid_1_s10s"})
        call_ha_service("switch", "turn_off", {"entity_id": "switch.warmtepomp_smart_grid_2_s11s"})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.hc_mode_altherma_on"})

        if plan and plan.slots:
            for slot_idx in merge_res.cancelled_slots:
                if slot_idx < len(plan.slots):
                    plan.slots[slot_idx].mode_code = StandardizedState.NORMAL
                    plan.slots[slot_idx].mode_label = "Normaal"
                    plan.slots[slot_idx].dhw_kw = 0.0

            plan.slots[0].mode_code = StandardizedState.NORMAL
            plan.slots[0].mode_label = "Normaal (Standby)"
            plan.slots[0].dhw_kw = 0.0

            store = PlanStore.get_instance()
            store.clear_in_flight_run()
            store.publish_plan(plan)

        now_ts = time.time()
        if _LAST_LOGGED_DECISION.get("state") != "released_50" or (now_ts - _LAST_LOGGED_DECISION.get("ts", 0)) >= 900.0:
            _LAST_LOGGED_DECISION["state"] = "released_50"
            _LAST_LOGGED_DECISION["ts"] = now_ts
            write_hems_annotation(
                event_type="system_release",
                title="✅ DHW Doel 50°C Bereikt — Automatisch (SG2)",
                description="Boilervat op doeltemperatuur. Warmtepomp vrijgegeven naar ruststand.",
                state_code="normal",
                power_kw=0.0,
                target_temp_c=50.0
            )
            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="system_release",
                chosen_mode="normal",
                target_temp_c=50.0,
                inputs={
                    "tank_temp_c": t_live,
                    "wp_power_w": wp_power,
                    "target_temp_c": 50.0
                },
                reason="✅ DHW Doel 50°C Bereikt — Automatisch (SG2)",
                explanation="Boilervat is op doeltemperatuur (>= 50°C). Smart Grid relais zijn vrijgegeven naar Automatisch (SG2 ruststand).",
                savings_estimate_eur=0.00
            )
    elif is_actively_heating and plan and plan.slots:
        # Boiler is actively heating to 50°C standard comfort (still below 49.8°C)!
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_1_s10s"})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_2_s11s"})
        call_ha_service("switch", "turn_off", {"entity_id": "switch.hc_mode_altherma_on"})

        run_pwr = max(3.0, round(wp_power / 1000.0, 2))
        for run_i in range(min(3, len(plan.slots))):
            plan.slots[run_i].mode_code = StandardizedState.FORCED_ON
            plan.slots[run_i].mode_label = "Geforceerd aan (50°C)"
            plan.slots[run_i].dhw_kw = run_pwr
            plan.slots[run_i].heating_kw = 0.0

        for slot_idx in merge_res.cancelled_slots:
            if slot_idx < len(plan.slots):
                plan.slots[slot_idx].mode_code = StandardizedState.NORMAL
                plan.slots[slot_idx].mode_label = "Normaal"
                plan.slots[slot_idx].dhw_kw = 0.0

        store = PlanStore.get_instance()
        store.register_in_flight_run(target_temp_c=50.0, mode="forced_on")
        store.publish_plan(plan)

        now_ts = time.time()
        if _LAST_LOGGED_DECISION.get("state") != "forced_50" or (now_ts - _LAST_LOGGED_DECISION.get("ts", 0)) >= 900.0:
            _LAST_LOGGED_DECISION["state"] = "forced_50"
            _LAST_LOGGED_DECISION["ts"] = now_ts
            write_hems_annotation(
                event_type="dhw_run",
                title="🚿 DHW Basislading (50°C) Gestart",
                description=merge_res.decision_explanation,
                state_code="forced_on",
                power_kw=run_pwr,
                target_temp_c=50.0
            )
            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="standard_charge",
                chosen_mode="forced_on",
                target_temp_c=50.0,
                inputs={
                    "tank_temp_c": t_live,
                    "wp_power_w": wp_power,
                    "target_temp_c": 50.0
                },
                reason="DHW Basislading (50°C) Gestart",
                explanation=merge_res.decision_explanation,
                savings_estimate_eur=0.10
            )
    else:
        if _LAST_LOGGED_DECISION.get("state") in ["max_on", "released_50", "forced_50"]:
            _LAST_LOGGED_DECISION["state"] = "idle"

    return merge_res


def evaluate_and_log_night_boiler_decision(plan: Any, t_live: float):
    global _LAST_NIGHT_AUDIT_LOG
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    # Active during evening and night (19:00 - 06:00)
    is_evening_or_night = (now_ams.hour >= 19 or now_ams.hour < 6)
    if not is_evening_or_night:
        return

    try:
        from layer2_calibration.dhw_thermal_model import DhwThermalModel
        dhw_model = DhwThermalModel()
        decision_data = dhw_model.evaluate_night_heating_decision(
            t_current_c=t_live,
            now_dt=now_ams,
            tomorrow_solar_peak_kw=2.5,
            planned_heat_hour=12.5
        )

        comfort_safe = decision_data.get("morning_is_safe", True)
        m_dip = decision_data.get("morning_dip_temp_c", 40.0)
        m_time = decision_data.get("morning_dip_time", "08:30")
        p95_dip = decision_data.get("morning_dip_p95_c", 38.0)
        savings = float(decision_data.get("savings_by_waiting", 0.23))
        chosen_mode = "normal" if comfort_safe else "forced_on"
        decision_state_key = f"{chosen_mode}_{round(m_dip, 0)}"

        now_ts = time.time()
        # Log if state changed or if at least 2 hours have passed since last night decision log
        if _LAST_NIGHT_AUDIT_LOG.get("state") != decision_state_key or (now_ts - _LAST_NIGHT_AUDIT_LOG.get("ts", 0)) >= 7200.0:
            _LAST_NIGHT_AUDIT_LOG["state"] = decision_state_key
            _LAST_NIGHT_AUDIT_LOG["ts"] = now_ts

            reason = "🌙 DHW Nachtbesluit: Wachten op Middagzon (Geen nachtlading nodig)" if comfort_safe else "🌙 DHW Nachtbesluit: Nachtlading Gepland (Comfortzekerheid)"
            explanation = decision_data.get("decision_explanation", "")
            if not explanation:
                if comfort_safe:
                    explanation = f"Het vat daalt vannacht zonder verwarming naar prognose {m_dip}°C om {m_time}u (P95 zware douche: {p95_dip}°C). Ochtendcomfort blijft ruim boven 40°C gewaarborgd. Nachtlading overbodig; wachten op middagzon bespaart ~€{savings:.2f}."
                else:
                    explanation = f"Comfortrisico dreigt: vat daalt naar prognose {m_dip}°C om {m_time}u (<40°C). Een nachtlading naar 50°C is ingepland in het goedkoopste dalkwartier."

            write_hems_annotation(
                event_type="night_decision",
                title=reason,
                description=explanation,
                state_code=chosen_mode,
                power_kw=0.0 if comfort_safe else 3.0,
                target_temp_c=50.0 if not comfort_safe else 0.0,
                savings_eur=savings if comfort_safe else 0.0
            )

            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="night_decision",
                chosen_mode=chosen_mode,
                target_temp_c=50.0 if not comfort_safe else None,
                inputs={
                    "tank_nu_c": round(t_live, 1),
                    "ochtend_dip_c": m_dip,
                    "ochtend_dip_tijd": m_time,
                    "ochtend_dip_p95_c": p95_dip,
                    "comfort_gewaarborgd": comfort_safe,
                    "besparing_wachten_eur": savings
                },
                reason=reason,
                explanation=explanation,
                savings_estimate_eur=savings if comfort_safe else 0.0
            )
    except Exception as e:
        print(f"Warning in evaluate_and_log_night_boiler_decision: {e}")


def evaluate_and_log_planner_decisions(plan: Any, frame: Any):
    global _LAST_PLAN_LOGS
    now_ts = time.time()
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    today_date = now_ams.strftime("%Y-%m-%d")

    # 1. Dynamic Spitsblokkades (Peak Lockouts) - Evaluated 1x per calendar day when day-ahead prices arrive
    if hasattr(plan, "dynamic_peaks") and plan.dynamic_peaks:
        if _LAST_PLAN_LOGS.get("peaks_date") != today_date:
            _LAST_PLAN_LOGS["peaks_date"] = today_date

            p_names = [f"{p.get('name', 'Spits')} ({p.get('start_time')}–{p.get('end_time')}, max €{p.get('max_price', 0):.2f}/kWh)" for p in plan.dynamic_peaks]
            peaks_desc = ", ".join(p_names)
            title = f"📅 EPEX Spitsblokkades Vastgesteld voor Vandaag ({len(plan.dynamic_peaks)} pieken)"
            desc = f"Beursnoteringen voor {now_ams.strftime('%d-%m-%Y')} verwerkt: {peaks_desc}. Warmtepomp zal tijdens deze uren automatisch worden vergrendeld (SG1) om dure piekafname te vermijden."

            write_hems_annotation(
                event_type="peak_schedule",
                title=title,
                description=desc,
                state_code="planned",
                power_kw=0.0,
                target_temp_c=0.0,
                savings_eur=0.45
            )
            DecisionAuditLogger.log_decision(
                domain="grid_tariff",
                decision_type="peak_detection",
                chosen_mode="planned",
                target_temp_c=None,
                inputs={"datum": today_date, "pieken": peaks_desc, "aantal": len(plan.dynamic_peaks)},
                reason=title,
                explanation=desc,
                savings_estimate_eur=0.45,
                category="DECISION"
            )

    # 2. CV Ruimteverwarming Policy (Space Heating)
    if hasattr(plan, "space_heating_summary") and plan.space_heating_summary:
        sh = plan.space_heating_summary
        cv_state = "summer_lockout" if sh.is_summer_lockout else ("preheat" if sh.preheat_hours > 0 else "modulating")
        if _LAST_PLAN_LOGS.get("cv_key") != cv_state or (now_ts - _LAST_PLAN_LOGS.get("last_ts", 0)) >= 14400.0:
            _LAST_PLAN_LOGS["cv_key"] = cv_state

            if sh.is_summer_lockout:
                title = f"☀️ CV Vloerverwarming: Zomerstop Actief ({sh.mean_outdoor_temp_c:.1f}°C)"
                desc = f"Gemiddelde buitentemperatuur is {sh.mean_outdoor_temp_c:.1f}°C (>= 16,0°C drempel). Ruimteverwarming is uitgeschakeld; warmtepomp blijft 100% beschikbaar voor tapwater."
                mode = "normal"
            elif sh.preheat_hours > 0:
                title = f"♨️ CV Vloerverwarming: Nachtdal Pre-Heat Gepland ({sh.preheat_hours:.1f}u)"
                desc = f"Verwarming laadt {sh.preheat_kwh_th:.1f} kWh thermische buffer in de dekvloer tijdens goedkope nachturen (02:00–06:00). Voorkomt piekafname overdag."
                mode = "advised_on"
            else:
                title = f"♨️ CV Vloerverwarming: Stooklijn Modulatie"
                desc = f"Verwarming volgt reguliere stooklijn (gemiddeld {sh.mean_outdoor_temp_c:.1f}°C buiten)."
                mode = "normal"

            write_hems_annotation(
                event_type="space_heating_policy",
                title=title,
                description=desc,
                state_code=mode,
                power_kw=round(sh.total_electric_kwh / 24.0, 2),
                target_temp_c=20.0,
                savings_eur=0.35 if sh.preheat_hours > 0 else 0.0
            )
            DecisionAuditLogger.log_decision(
                domain="space_heating",
                decision_type="heating_policy",
                chosen_mode=mode,
                target_temp_c=20.0,
                inputs={
                    "buitentemp_gem_c": round(sh.mean_outdoor_temp_c, 1),
                    "zomerstop_actief": sh.is_summer_lockout,
                    "cop_gemiddeld": round(sh.average_cop, 2),
                    "warmtevraag_24u_kwh": round(sh.total_heat_demand_kwh, 1)
                },
                reason=title,
                explanation=desc,
                savings_estimate_eur=0.35 if sh.preheat_hours > 0 else 0.0
            )

    # 3. DHW Daytime Arbitration Logging (Full 24h Traceability)
    if hasattr(plan, "metadata") and isinstance(plan.metadata, dict) and "daytime_arbitrage_audit" in plan.metadata:
        audit = plan.metadata["daytime_arbitrage_audit"]
        if audit:
            last_mode = _LAST_PLAN_LOGS.get("dhw_day_mode")
            cur_mode = audit.get("planned_mode")
            if last_mode != cur_mode or (now_ts - _LAST_PLAN_LOGS.get("last_dhw_day_ts", 0)) >= 3600.0:
                _LAST_PLAN_LOGS["dhw_day_mode"] = cur_mode
                _LAST_PLAN_LOGS["last_dhw_day_ts"] = now_ts

                title = f"♨️ DHW Dagplanning Arbitrage: {audit.get('planned_mode_label')}"
                explanation = audit.get("explanation", "")
                DecisionAuditLogger.log_decision(
                    domain="dhw_boiler",
                    decision_type="daytime_arbitrage",
                    chosen_mode=cur_mode,
                    target_temp_c=audit.get("target_temp_c"),
                    inputs={
                        "situation": audit.get("situation"),
                        "evening_dip_c": audit.get("unheated_evening_dip_c"),
                        "evening_dip_time": audit.get("evening_dip_time"),
                        "selected_path": audit.get("selected_path_id"),
                        "evaluated_paths": audit.get("evaluated_paths")
                    },
                    reason=title,
                    explanation=explanation,
                    savings_estimate_eur=audit.get("savings_eur", 0.0),
                    category="DECISION"
                )
