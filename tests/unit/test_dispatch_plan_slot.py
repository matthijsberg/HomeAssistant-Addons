"""
Unit Tests for DispatchPlanSlot Type Safety & State Invariants
=============================================================
Verifies:
1. mode_code is strictly typed as StandardizedState enum.
2. Invalid mode_code strings immediately raise ValueError.
3. Legacy mode code aliases are safely normalized.
4. color_hex and tailwind_class are automatically synchronized with mode_code.
"""

import pytest
from models.canonical import DispatchPlanSlot, StandardizedState, get_state_metadata


def test_dispatch_plan_slot_valid_enum():
    """Verify that DispatchPlanSlot accepts StandardizedState enum."""
    slot = DispatchPlanSlot(
        slot_idx=0,
        time_label="00:00",
        dt_iso="2026-09-17T00:00:00+02:00",
        price_eur=0.25,
        solar_kw=0.0,
        unallocated_kw=0.3,
        heating_kw=0.0,
        dhw_kw=0.0,
        net_import_kw=0.3,
        mode_code=StandardizedState.FORCED_OFF,
        mode_label="Geforceerd uit (blok)"
    )
    assert slot.mode_code == StandardizedState.FORCED_OFF
    assert isinstance(slot.mode_code, StandardizedState)
    assert slot.color_hex == "#EF4444"
    assert slot.tailwind_class == "text-red-400"


def test_dispatch_plan_slot_string_coercion():
    """Verify that a valid string is coerced into StandardizedState."""
    slot = DispatchPlanSlot(
        slot_idx=1,
        time_label="00:15",
        dt_iso="2026-09-17T00:15:00+02:00",
        price_eur=0.20,
        solar_kw=0.0,
        unallocated_kw=0.3,
        heating_kw=0.0,
        dhw_kw=0.0,
        net_import_kw=0.3,
        mode_code="advised_off",  # type: ignore[arg-type]
        mode_label="Geadviseerd uit"
    )
    assert slot.mode_code == StandardizedState.ADVISED_OFF
    assert isinstance(slot.mode_code, StandardizedState)
    assert slot.color_hex == "#F59E0B"


def test_dispatch_plan_slot_legacy_aliases():
    """Verify that legacy mode strings from older snapshots normalize safely."""
    slot_lockout = DispatchPlanSlot(
        slot_idx=2,
        time_label="00:30",
        dt_iso="2026-09-17T00:30:00+02:00",
        price_eur=0.30,
        solar_kw=0.0,
        unallocated_kw=0.3,
        heating_kw=0.0,
        dhw_kw=0.0,
        net_import_kw=0.3,
        mode_code="peak_lockout",  # type: ignore[arg-type]
        mode_label="Spitsblok"
    )
    assert slot_lockout.mode_code == StandardizedState.FORCED_OFF

    slot_boost = DispatchPlanSlot(
        slot_idx=3,
        time_label="00:45",
        dt_iso="2026-09-17T00:45:00+02:00",
        price_eur=0.05,
        solar_kw=3.0,
        unallocated_kw=0.3,
        heating_kw=0.0,
        dhw_kw=2.4,
        net_import_kw=-0.3,
        mode_code="forced_solar_boost_60",  # type: ignore[arg-type]
        mode_label="Zonnebuffer"
    )
    assert slot_boost.mode_code == StandardizedState.MAX_ON
    assert slot_boost.color_hex == "#A855F7"


def test_dispatch_plan_slot_invalid_mode_raises():
    """Verify that an unknown or invalid mode_code string strictly raises ValueError."""
    with pytest.raises(ValueError):
        DispatchPlanSlot(
            slot_idx=0,
            time_label="00:00",
            dt_iso="2026-09-17T00:00:00+02:00",
            price_eur=0.25,
            solar_kw=0.0,
            unallocated_kw=0.3,
            heating_kw=0.0,
            dhw_kw=0.0,
            net_import_kw=0.3,
            mode_code="unrecognized_garbage_state",  # type: ignore[arg-type]
            mode_label="Invalid"
        )
