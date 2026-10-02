"""
Unit Tests: DeviceSlotDispatch & Canonical Plan Schema 1.1.0 (REG5)
===================================================================
Tests backward compatibility with legacy schema 1.0.0, per-device
dispatch serialization, and schema version reporting.
"""

from models.canonical import (
    DeviceSlotDispatch,
    DispatchPlanSlot,
    CanonicalDispatchPlan,
    StandardizedState,
    DHWPlanSummary,
)


def test_slot_defaults_to_empty_device_dispatches():
    slot = DispatchPlanSlot(
        slot_idx=0,
        time_label="12:00",
        dt_iso="2026-09-25T12:00:00",
        price_eur=0.25,
        solar_kw=2.0,
        unallocated_kw=0.4,
        heating_kw=0.0,
        dhw_kw=0.0,
        net_import_kw=-1.6,
        mode_code=StandardizedState.NORMAL,
        mode_label="Normaal",
    )
    assert slot.device_dispatches == {}


def test_slot_with_device_dispatches():
    bat_dispatch = DeviceSlotDispatch(
        device_id="home_battery",
        device_type="home_battery",
        mode_code="forced_off",
        mode_label="Spitsontlasting",
        electric_kw=-2.5,
        payload={"soc_pct": 68.5, "target_power_w": 2500}
    )
    hp_dispatch = DeviceSlotDispatch(
        device_id="heatpump_daikin",
        device_type="heat_pump",
        mode_code="forced_off",
        mode_label="Blokkade",
        electric_kw=0.0,
        payload={"sg_mode": "SG1"}
    )

    slot = DispatchPlanSlot(
        slot_idx=0,
        time_label="18:00",
        dt_iso="2026-09-25T18:00:00",
        price_eur=0.45,
        solar_kw=0.0,
        unallocated_kw=0.5,
        heating_kw=0.0,
        dhw_kw=0.0,
        net_import_kw=-2.0,
        mode_code=StandardizedState.FORCED_OFF,
        mode_label="Geforceerd uit",
        device_dispatches={
            "home_battery": bat_dispatch,
            "heatpump_daikin": hp_dispatch,
        }
    )

    assert len(slot.device_dispatches) == 2
    assert slot.device_dispatches["home_battery"].electric_kw == -2.5
    assert slot.device_dispatches["home_battery"].payload["soc_pct"] == 68.5

    # Test serialization
    serialized = bat_dispatch.to_dict()
    deserialized = DeviceSlotDispatch.from_dict(serialized)
    assert deserialized.device_id == "home_battery"
    assert deserialized.electric_kw == -2.5
    assert deserialized.payload["soc_pct"] == 68.5


def test_canonical_dispatch_plan_schema_version_1_1_0():
    dhw = DHWPlanSummary(
        planned_mode="standard",
        planned_mode_label="Normaal",
        color_hex="#10B981",
        tailwind_class="text-emerald-400",
        target_temp_c=50.0,
        run_start="13:00",
        run_end="14:15",
        run_duration_min=75,
        power_kw=1.8,
        total_stroom_kwh=2.25,
        spits_lockout_hours=2.0,
        dynamic_peaks=[],
        unheated_trajectory=[],
        counterfactual_reason="Laagste marktprijs",
        arbitrage_saving_eur=0.69,
    )

    plan = CanonicalDispatchPlan(
        generated_at="2026-09-25T12:00:00",
        horizon_hours=24.0,
        resolution_mins=15,
        is_fresh=True,
        freshness_age_seconds=10.0,
        slots=[],
        dhw_summary=dhw,
        dynamic_peaks=[],
    )

    assert plan.schema_version == "1.1.0"
