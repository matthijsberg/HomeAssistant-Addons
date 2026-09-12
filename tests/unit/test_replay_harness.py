"""
Replay Harness & Golden Plan Regression Test Suite
===================================================
Replays a 100% genuine recorded production day (2026-09-12):
- Real EPEX spot prices with taxes (EnergyZero)
- Real Open-Meteo solar radiation and temperature for Culemborg
- Real InfluxDB 7x96 learned unallocated base profile
- Real initial DHW tank temperature (41.5°C)

Ensures that any developer, agent, or CI runner can verify the entire
HEMS pipeline without requiring access to Matthijs's live residence.
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


def test_replay_harness_golden_day():
    assert RECORDED_DAY_FILE.exists(), f"Missing recorded telemetry fixture at {RECORDED_DAY_FILE}"
    data = json.loads(RECORDED_DAY_FILE.read_text(encoding="utf-8"))

    ams_tz = ZoneInfo("Europe/Amsterdam")
    day_dt = datetime.fromisoformat("2026-09-12T09:00:00+02:00")

    # 1. Transform fixture data into raw inputs
    raw_prices = [{"dt": p["timestamp"], "price": p["price"]} for p in data["prices"]]
    
    # Calculate POA solar from radiation
    raw_solar = []
    raw_weather = []
    for w in data["weather"]:
        dt = datetime.fromisoformat(w["timestamp"])
        rad = w["solar_radiation_w_m2"]
        # Simplified POA conversion for replay: ~0.90 efficiency * 5.76 kWp * (rad / 1000)
        pv_kw = min(5.5, max(0.0, (rad / 1000.0) * 5.76 * 0.88))
        raw_solar.append({"dt": dt, "solar_kw": round(pv_kw, 3)})
        raw_weather.append({"dt": dt, "temperature": w["temperature_c"]})

    raw_unalloc = data["unallocated_profile_96"]
    # Replicate across 7 days so matrix indexing works
    full_matrix = raw_unalloc * 7

    # 2. Run TelemetrySanitizer
    frame = TelemetrySanitizer.sanitize(
        now=day_dt,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=full_matrix,
        current_dhw_temp=data["dhw_tank_temp_c"],
        current_room_temp=data["room_temp_c"],
        last_hardware_reading_time=day_dt,
        horizon_slots=96,
        step_mins=15
    )

    assert frame.is_fresh is True
    assert len(frame.slots) == 96
    assert frame.slots[0].dt.minute == 0

    # 3. Run CentralPlanner
    plan = CentralPlanner.plan(frame, current_dhw_temp=data["dhw_tank_temp_c"])

    # Invariants checks on the generated plan:
    assert len(plan.slots) == 96
    assert plan.dhw_summary is not None
    # Verify winter comfort cap (max 2.5 hours = 150 mins)
    assert plan.dhw_summary.spits_lockout_hours <= 2.5
    # Verify DHW daytime priority (must plan standard 50C or solar boost 60C during daylight)
    assert plan.dhw_summary.planned_mode in ["forced_on", "forced_solar_boost_60", "max_on"]

    # 4. Golden Plan Snapshot verification
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
        # First run: write snapshot
        GOLDEN_PLAN_SNAPSHOT.write_text(json.dumps(summary_snapshot, indent=2), encoding="utf-8")

    expected_snapshot = json.loads(GOLDEN_PLAN_SNAPSHOT.read_text(encoding="utf-8"))
    
    # Assert exact match with golden snapshot
    for key, expected_val in expected_snapshot.items():
        if key == "generated_at":
            continue
        assert summary_snapshot[key] == expected_val, (
            f"Drift detected in golden plan replay for key '{key}'! "
            f"Expected {expected_val}, got {summary_snapshot[key]}"
        )
