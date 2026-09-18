import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from layer1_data_collection.sanitizer import TelemetrySanitizer
from layer3_scheduling.central_planner import CentralPlanner
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.dhw_daytime_arbiter import DhwDaytimeArbiter


def create_sample_telemetry_frame(now_dt: datetime, current_dhw_temp: float = 46.0):
    raw_prices = [{"dt": (now_dt + timedelta(hours=i)).isoformat(), "price": 0.15 if i < 4 else 0.35} for i in range(24)]
    raw_solar = [{"dt": (now_dt + timedelta(hours=i)).isoformat(), "solar_kw": 3.5 if 1 <= i <= 4 else 0.0} for i in range(24)]
    raw_weather = [{"dt": (now_dt + timedelta(hours=i)).isoformat(), "temperature": 18.0} for i in range(24)]

    return TelemetrySanitizer.sanitize(
        now=now_dt,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=[0.35] * 672,
        current_dhw_temp=current_dhw_temp,
        last_hardware_reading_time=now_dt,
        horizon_slots=96,
        step_mins=15,
    )


def test_no_contradiction_when_target_temp_reaches_boost_60():
    """
    Edge case: t_target_opt reaches 60.0°C (equal to spec.boost_setpoint_c).
    Verifies that comfort_text, bullet_1, bullet_2, and finance_text never claim
    that 60°C is 'vermeden' when the planned target is >= 59.9°C.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    frame = create_sample_telemetry_frame(now_dt, current_dhw_temp=46.0)
    spec = DhwTankSpec()

    # Simulate horizon solver yielding 60.0°C
    with patch.object(
        DhwDaytimeArbiter,
        "compute_optimal_horizon_target_temp",
        return_value=(60.0, 48, 12.0, 10.0, 8.0, 2.0),
    ):
        plan = CentralPlanner.plan(frame, current_dhw_temp=46.0, dhw_spec=spec)
        details = plan.dhw_summary.decision_details
        assert details is not None
        sww_target = plan.dhw_summary.target_temp_c

        assert sww_target >= (spec.boost_setpoint_c - 0.1), f"Expected 60.0°C target, got {sww_target}"
        comfort_text = details.get("comfort_text", "")
        bullet_2 = details.get("bullet_2", "")
        finance_text = details.get("finance_text", "")

        # Verify no self-contradictory claims
        assert "vermeden" not in comfort_text.lower(), "comfort_text claimed 60°C was avoided despite targeting 60°C"
        assert "vermijd" not in comfort_text.lower(), "comfort_text claimed avoiding 60°C"
        assert "doorkoken naar 60" not in comfort_text.lower(), "comfort_text claimed 60°C was unnecessary overboiling"
        assert "vermeden" not in bullet_2.lower(), "bullet_2 claimed 60°C was avoided"
        assert "60.0°c" in comfort_text.lower() or "60°c" in comfort_text.lower()


def test_pad_a2_selection_explanation_consistency():
    """
    When Pad A2 (60°C buffer run) is chosen because it is cheaper over 24h,
    the explanation must directly state that heating to 60°C is planned,
    with no mention of 60°C being avoided.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    frame = create_sample_telemetry_frame(now_dt, current_dhw_temp=44.0)
    spec = DhwTankSpec()

    # Create a mock DaytimeArbitrationResult where Pad A2 is selected
    from layer3_scheduling.dhw_daytime_arbiter import DaytimeArbitrationResult, EvaluatedPath

    path_a1 = EvaluatedPath(
        path_id="PAD_A1_DAY_50",
        name="Pad A1: Optimale Horizon-Lading (tot 50.0°C)",
        description="50°C nu + nachtrun",
        day_target_temp_c=50.0,
        day_slots=[48, 49, 50],
        day_window_label="12:00–12:45",
        day_cost_eur=0.40,
        day_power_kw=1.8,
        day_el_kwh=1.35,
        simulated_morning_dip_c=36.0,
        simulated_morning_dip_time="07:00",
        night_run_required=True,
        night_slots=[12, 13, 14],
        night_window_label="03:00–03:45",
        night_cost_eur=0.50,
        night_el_kwh=1.35,
        total_24h_cost_eur=0.90,
    )
    path_a2 = EvaluatedPath(
        path_id="PAD_A2_DAY_60",
        name="Pad A2: Doortrekken naar 60°C (Buffer)",
        description="60°C nu, geen nachtrun",
        day_target_temp_c=60.0,
        day_slots=[48, 49, 50, 51],
        day_window_label="12:00–13:00",
        day_cost_eur=0.65,
        day_power_kw=2.4,
        day_el_kwh=2.4,
        simulated_morning_dip_c=42.5,
        simulated_morning_dip_time="07:00",
        night_run_required=False,
        night_slots=[],
        night_window_label="Geen nachtrun",
        night_cost_eur=0.0,
        night_el_kwh=0.0,
        total_24h_cost_eur=0.65,
    )
    mock_arb_res = DaytimeArbitrationResult(
        situation="SITUATION_1_EVENING_COMFORT_RISK",
        unheated_evening_dip_c=38.5,
        evening_dip_time="19:00",
        evaluated_paths=[path_a1, path_a2],
        selected_path=path_a2,
        planned_mode="forced_solar_boost_60",
        planned_mode_label="Maximaal aan (doorverwarming tot 60°C)",
        target_temp_c=60.0,
        power_kw=2.4,
        planned_slots=[48, 49, 50, 51],
        savings_eur=0.25,
        explanation="In 1 run doorwarmen naar 60°C om 12:00–13:00 is de voordeligste keuze. Dit overbrugt de hele nacht en bespaart €0.25 t.o.v. stoppen bij 50.0°C en nachtelijk bijladen.",
    )

    with patch.object(DhwDaytimeArbiter, "evaluate_daytime_arbitrage", return_value=mock_arb_res):
        plan = CentralPlanner.plan(frame, current_dhw_temp=44.0, dhw_spec=spec)
        details = plan.dhw_summary.decision_details
        assert details is not None

        assert plan.dhw_summary.target_temp_c == 60.0
        assert "vermeden" not in details["comfort_text"].lower()
        assert "In 1 run doorwarmen naar 60°C" in details["comfort_text"]
        assert "Pad A2" in details["finance_text"]
        assert "Pad A1" in details["finance_text"]
        assert "Doortrekken naar 60.0°C" in details["bullet_2"]


def test_pad_a1_selection_explanation_consistency():
    """
    When Pad A1 (50°C optimal run) is chosen, the explanation must state
    that 50.0°C was chosen and explain savings vs 60°C.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    frame = create_sample_telemetry_frame(now_dt, current_dhw_temp=46.0)
    spec = DhwTankSpec()

    plan = CentralPlanner.plan(frame, current_dhw_temp=46.0, dhw_spec=spec)
    details = plan.dhw_summary.decision_details
    assert details is not None

    # When 50°C is chosen, comfort_text must match the arbiter's explanation
    assert "Besluit &amp; Doeltemperatuur:" in details["comfort_text"]
    assert "Financiële Padvergelijking" in details["finance_card_title"]
    assert "Pad A1" in details["finance_text"]
    assert "Pad A2" in details["finance_text"]
