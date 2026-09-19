"""
Replay Harness & Golden Plan Regression Test Suite
===================================================
Replays genuine recorded production days & full-year seasonal archetypes:
1. day_2026_09_12_recorded.json (Historical baseline)
2. golden_summer_solar_heavy.json (Hoogzomer & negatieve prijzen)
3. golden_winter_sunny_peak.json (Koude zonnige winterdag met avondpiek)
4. golden_winter_dunkelflaute.json (Grijze vriezende winterdag zonder zon)
5. golden_winter_defrost_humid.json (Vriesmist, hoge luchtvochtigheid & ontdooicycli)
6. golden_shoulder_season.json (Schouderseizoen & zomersluitingstransitie)

Ensures that any developer, agent, or CI runner can verify the entire
HEMS pipeline across the full seasonal spectrum without live residence hardware.
"""

import pytest
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from models.canonical import CanonicalDispatchPlan, StandardizedState
from layer1_data_collection.sanitizer import TelemetrySanitizer
from layer3_scheduling.central_planner import CentralPlanner


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "golden"
RECORDED_DAY_FILE = FIXTURES_DIR / "day_2026_09_12_recorded.json"
GOLDEN_PLAN_SNAPSHOT = FIXTURES_DIR / "golden_plan_2026_09_12_snapshot.json"


def _load_and_plan_fixture(fixture_filename: str):
    fixture_path = FIXTURES_DIR / fixture_filename
    assert fixture_path.exists(), f"Missing fixture at {fixture_path}"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    date_str = data["recorded_date"]
    day_dt = datetime.fromisoformat(f"{date_str}T09:00:00+02:00")

    raw_prices = [{"dt": p["timestamp"], "price": p["price"]} for p in data["prices"]]

    raw_solar = []
    raw_weather = []
    for w in data["weather"]:
        dt = datetime.fromisoformat(w["timestamp"])
        rad = w["solar_radiation_w_m2"]
        pv_kw = min(5.5, max(0.0, (rad / 1000.0) * 5.76 * 0.88))
        raw_solar.append({"dt": dt, "solar_kw": round(pv_kw, 3)})
        raw_weather.append({"dt": dt, "temperature": w["temperature_c"]})

    raw_unalloc = data["unallocated_profile_96"] * 7

    frame = TelemetrySanitizer.sanitize(
        now=day_dt,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=raw_unalloc,
        current_dhw_temp=data["dhw_tank_temp_c"],
        current_room_temp=data["room_temp_c"],
        last_hardware_reading_time=day_dt,
        horizon_slots=96,
        step_mins=15
    )

    plan = CentralPlanner.plan(frame, current_dhw_temp=data["dhw_tank_temp_c"])
    return data, frame, plan


def test_replay_harness_golden_day():
    """Verify original baseline recorded production day (2026-09-12)."""
    data, frame, plan = _load_and_plan_fixture("day_2026_09_12_recorded.json")

    assert frame.is_fresh is True
    assert len(frame.slots) == 96
    assert frame.slots[0].dt.minute == 0

    assert len(plan.slots) == 96
    assert plan.dhw_summary is not None
    assert plan.dhw_summary.spits_lockout_hours <= 2.5
    assert plan.dhw_summary.planned_mode in ["forced_on", "forced_solar_boost_60", "max_on"]

    summary_snapshot = {
        "generated_at": "2026-09-12T09:00:00+02:00",
        "horizon_hours": plan.horizon_hours,
        "dhw_planned_mode": plan.dhw_summary.planned_mode,
        "dhw_target_temp_c": plan.dhw_summary.target_temp_c,
        "dhw_run_start": plan.dhw_summary.run_start,
        "dhw_run_end": plan.dhw_summary.run_end,
        "dhw_power_kw": plan.dhw_summary.power_kw,
        "spits_lockout_hours": plan.dhw_summary.spits_lockout_hours,
        "dynamic_peaks_count": len(plan.dynamic_peaks),
        "total_active_dhw_slots": sum(1 for s in plan.slots if s.dhw_kw > 0)
    }

    if not GOLDEN_PLAN_SNAPSHOT.exists():
        GOLDEN_PLAN_SNAPSHOT.write_text(json.dumps(summary_snapshot, indent=2), encoding="utf-8")

    expected_snapshot = json.loads(GOLDEN_PLAN_SNAPSHOT.read_text(encoding="utf-8"))

    for key, expected_val in expected_snapshot.items():
        if key == "generated_at":
            continue
        assert summary_snapshot[key] == expected_val, (
            f"Drift detected in golden plan replay for key '{key}'! "
            f"Expected {expected_val}, got {summary_snapshot[key]}"
        )


def test_replay_summer_solar_heavy():
    """Hoogzomer: 35kWh PV, zomersluiting CV actief, boiler benut zonne-overschot."""
    data, frame, plan = _load_and_plan_fixture("golden_summer_solar_heavy.json")

    assert frame.is_fresh is True
    # Summer lockout strictly engaged: CV space heating must be 0 kWh
    assert plan.heating_summary is not None
    assert plan.heating_summary.is_heating_season is False
    assert plan.heating_summary.total_heating_kwh_el == 0.0
    assert "Zomersluiting" in plan.heating_summary.season_status_label

    # DHW boiler absorbs solar surplus
    assert plan.dhw_summary is not None
    assert plan.dhw_summary.planned_mode in ["forced_on", "forced_solar_boost_60", "max_on"]


def test_replay_winter_sunny_peak():
    """Winterzon: koude dag (2-5C) met heldere zon en harde avondspitsblokkade."""
    data, frame, plan = _load_and_plan_fixture("golden_winter_sunny_peak.json")

    assert plan.heating_summary is not None
    assert plan.heating_summary.is_heating_season is True
    assert plan.heating_summary.total_heating_kwh_el > 15.0
    assert plan.heating_summary.preheat_hours > 0.0
    assert plan.heating_summary.lockout_hours > 0.0

    # Comfort preservation
    assert plan.heating_summary.min_projected_room_temp_c >= plan.heating_summary.min_comfort_room_c

    # DHW leverages midday solar for optimal buffer
    assert plan.dhw_summary.planned_mode in ["forced_on", "max_on"]
    assert plan.dhw_summary.target_temp_c >= 47.0


def test_replay_winter_dunkelflaute():
    """Dunkelflaute: vriezende dag (-2 to 0C) zonder zon, hoge stookvraag, nachtdal."""
    data, frame, plan = _load_and_plan_fixture("golden_winter_dunkelflaute.json")

    assert plan.heating_summary is not None
    assert plan.heating_summary.is_heating_season is True
    # Significant space heating load
    assert plan.heating_summary.total_heating_kwh_el >= 25.0

    # DHW targets standard 50C without expecting solar
    assert 49.0 <= plan.dhw_summary.target_temp_c <= 53.0


def test_replay_winter_defrost_humid():
    """Kwakkelwinter: vriesmist (1-3C), hoge luchtvochtigheid met ontdooiverliezen."""
    data, frame, plan = _load_and_plan_fixture("golden_winter_defrost_humid.json")

    assert plan.heating_summary is not None
    assert plan.heating_summary.is_heating_season is True
    # COP penalty reflects near-freezing humidity conditions
    assert plan.heating_summary.average_cop <= 3.85
    assert plan.heating_summary.total_heating_kwh_el > 20.0


def test_replay_shoulder_season():
    """Schouderseizoen: milde dag (10-17.5C) met lage deellast stookvraag."""
    data, frame, plan = _load_and_plan_fixture("golden_shoulder_season.json")

    assert plan.heating_summary is not None
    assert plan.heating_summary.is_heating_season is True
    # Modest space heating load compared to deep winter
    assert plan.heating_summary.total_heating_kwh_el < 15.0
    assert 58.0 <= plan.dhw_summary.target_temp_c <= 60.0
