import pytest
from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy


def test_summer_lockout():
    """Verify that warm weather (>16C) completely suppresses heating."""
    outdoor_warm = [19.0] * 96
    prices = [0.25] * 96
    solar = [2.0] * 96

    summary = SpaceHeatingPolicy.plan_space_heating(
        outdoor_temps_c=outdoor_warm,
        prices_eur=prices,
        solar_kw=solar,
        active_dhw_slots=[],
        dynamic_peaks=[]
    )

    assert not summary.is_heating_season
    assert "Zomersluiting" in summary.season_status_label
    assert summary.total_heating_kwh_el == 0.0
    assert summary.preheat_hours == 0.0
    assert summary.lockout_hours == 0.0


def test_heating_season_preheat_and_lockouts():
    """Verify that cold weather activates heating with preheat and peak lockouts."""
    outdoor_cold = [7.0] * 96
    prices = [0.22] * 96
    solar = [0.0] * 96

    # Night valley (02:00 to 05:00, slots 8 to 20): cheap price
    for i in range(8, 20):
        prices[i] = 0.15

    # Morning peak lockout (07:00 to 09:30, slots 28 to 38)
    peaks = [{
        "start_idx": 28,
        "end_idx": 37,
        "is_hard_lockout": True,
        "name": "Ochtendspits"
    }]

    summary = SpaceHeatingPolicy.plan_space_heating(
        outdoor_temps_c=outdoor_cold,
        prices_eur=prices,
        solar_kw=solar,
        active_dhw_slots=[],
        dynamic_peaks=peaks,
        current_room_temp_c=20.0
    )

    assert summary.is_heating_season
    assert "Stookseizoen" in summary.season_status_label
    assert summary.total_heating_kwh_el > 0.0
    assert summary.preheat_hours > 0.0
    assert summary.lockout_hours > 0.0

    # Verify peak lockout slots have 0 kW
    for s_idx in range(28, 38):
        assert summary.slots[s_idx].heating_kw_el == 0.0
        assert summary.slots[s_idx].mode_code == "forced_off"

    # Verify comfort threshold (min room temp >= 19.5C)
    assert summary.min_projected_room_temp_c >= 19.5


def test_dhw_hydraulic_interlock_cv_pause():
    """Verify that during active DHW slots, space heating is strictly 0 kW."""
    outdoor_cold = [5.0] * 96
    prices = [0.20] * 96
    solar = [0.0] * 96
    dhw_slots = [40, 41, 42, 43]  # 10:00 to 11:00 DHW run

    summary = SpaceHeatingPolicy.plan_space_heating(
        outdoor_temps_c=outdoor_cold,
        prices_eur=prices,
        solar_kw=solar,
        active_dhw_slots=dhw_slots,
        dynamic_peaks=[]
    )

    for slot_idx in dhw_slots:
        assert summary.slots[slot_idx].heating_kw_el == 0.0


def test_carnot_cop_calculation():
    """Verify temperature-dependent Carnot COP curve."""
    cop_cold = SpaceHeatingPolicy.calculate_carnot_cop(-5.0)
    cop_mild = SpaceHeatingPolicy.calculate_carnot_cop(10.0)
    cop_warm = SpaceHeatingPolicy.calculate_carnot_cop(18.0)

    assert 2.5 <= cop_cold <= 3.8
    assert 4.0 <= cop_mild <= 5.2
    assert cop_warm >= cop_mild


def test_thermal_cost_calculation():
    """Verify that thermal cost accounts for COP and solar surplus."""
    cost_cold_grid = SpaceHeatingPolicy.calculate_thermal_cost(price_eur=0.25, cop=3.0, solar_kw=0.0)
    cost_mild_grid = SpaceHeatingPolicy.calculate_thermal_cost(price_eur=0.25, cop=5.0, solar_kw=0.0)
    cost_solar_mild = SpaceHeatingPolicy.calculate_thermal_cost(price_eur=0.25, cop=5.0, solar_kw=2.5)

    assert cost_cold_grid > cost_mild_grid
    assert cost_mild_grid > cost_solar_mild
    assert cost_solar_mild <= 0.02


def test_dynamic_thermostat_setpoint():
    """Verify that custom thermostat setpoint (e.g. 21.0C) shifts comfort boundaries."""
    outdoor_cold = [6.0] * 96
    prices = [0.22] * 96
    solar = [0.0] * 96

    summary = SpaceHeatingPolicy.plan_space_heating(
        outdoor_temps_c=outdoor_cold,
        prices_eur=prices,
        solar_kw=solar,
        active_dhw_slots=[],
        dynamic_peaks=[],
        current_room_temp_c=21.0,
        target_room_temp_c=21.0
    )

    assert summary.target_room_temp_c == 21.0
    assert summary.min_comfort_room_c == 20.5
    assert summary.max_preheat_room_c == 22.2
    assert summary.min_projected_room_temp_c >= 20.5


def test_winter_sun_and_evening_peak_buffering():
    """
    Simulate cold winter day (4C) with midday solar and evening peak (17:00 - 20:30).
    Verifies that floor pre-charges during midday buffer despite room warming,
    allowing building to coast through evening peak without dropping below comfort.
    """
    outdoor = [4.0] * 96
    prices = [0.24] * 96
    solar = [0.0] * 96

    # Solar between 11:00 and 15:00 (slots 44 to 60)
    for i in range(44, 60):
        solar[i] = 2.8
        prices[i] = 0.18

    # Evening peak lockout (17:00 to 20:30, slots 68 to 82)
    peaks = [{
        "start_idx": 68,
        "end_idx": 82,
        "is_hard_lockout": True,
        "name": "Avondpiek"
    }]

    summary = SpaceHeatingPolicy.plan_space_heating(
        outdoor_temps_c=outdoor,
        prices_eur=prices,
        solar_kw=solar,
        active_dhw_slots=[],
        dynamic_peaks=peaks,
        current_room_temp_c=20.0,
        current_floor_temp_c=21.5,
        target_room_temp_c=20.0
    )

    # Verify preheat active in midday solar window before peak
    preheat_in_solar = [summary.slots[i].is_preheat_active for i in range(48, 68)]
    assert any(preheat_in_solar), "Preheat buffer must be scheduled before the evening peak"

    # Verify evening peak is strictly forced off
    for s_idx in range(68, 83):
        assert summary.slots[s_idx].heating_kw_el == 0.0
        assert summary.slots[s_idx].mode_code == "forced_off"

    # Coasting through peak: room temp must stay above min comfort (summary.min_comfort_room_c = 19.5C)
    peak_room_temps = [summary.slots[s_idx].room_temp_c for s_idx in range(68, 83)]
    assert min(peak_room_temps) >= summary.min_comfort_room_c, f"Room dropped to {min(peak_room_temps)}C during peak!"
