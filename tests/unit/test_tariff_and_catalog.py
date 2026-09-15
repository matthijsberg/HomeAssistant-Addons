"""
Tests for TariffProvider & Mode Catalog
=======================================
"""

import pytest
from layer3_scheduling.tariff_provider import TariffProvider, TariffConfig
from models.mode_catalog import get_mode_meta, load_mode_catalog


def test_tariff_provider_calculations():
    # Standard Powerpeers parameters:
    # spot = 0.10
    # markup = 0.01210
    # tax = 0.11085
    # sum = 0.22295
    # vat = 21% -> 0.22295 * 1.21 = 0.2697695 -> ~0.26977
    tp = TariffProvider()
    price = tp.calculate_import_price(0.10)
    assert abs(price - 0.26977) < 0.0001

    # Zero spot price should still carry markup + tax + VAT
    p_zero = tp.calculate_import_price(0.0)
    expected_zero = (0.01210 + 0.11085) * 1.21
    assert abs(p_zero - expected_zero) < 0.0001


def test_mode_catalog_loading_and_fallback():
    # Verify standard modes exist
    meta_off = get_mode_meta("forced_off", "thermal_buffer")
    assert meta_off["code"] == "forced_off"
    assert meta_off["color_hex"] == "#EF4444"
    assert "Harde Spitsblokkade" in meta_off["badge_label"]

    meta_solar = get_mode_meta("charge_solar", "battery_storage")
    assert meta_solar["color_hex"] == "#10B981"
    assert "Zonneladen" in meta_solar["label"]

    # Unknown mode should safely return a valid dictionary
    unknown = get_mode_meta("super_custom_mode_xyz")
    assert unknown["code"] == "super_custom_mode_xyz"
    assert unknown["color_hex"].startswith("#")


def test_dynamic_peaks_macro_clustering_and_anti_cycling():
    """Verify macro-clustering of fragmented peak rungs and 120m dwell time enforcement."""
    from models.canonical import detect_dynamic_price_peaks
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    base_dt = datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    # Construct 24h timeline with a camelback peak in the evening (17:30-18:00 and 18:30-21:30)
    timeline = []
    for i in range(96):
        dt = base_dt + timedelta(minutes=15 * i)
        h = dt.hour
        m = dt.minute
        # Baseline price ~€0.25
        price = 0.25
        # Camelback evening peak:
        # Spike 1: 17:30-18:00 (high: 0.40)
        # Dip: 18:00-18:30 (moderate: 0.32)
        # Spike 2: 18:30-21:15 (very high: 0.44)
        if (h == 17 and m >= 30):
            price = 0.40
        elif (h == 18 and m < 30):
            price = 0.32
        elif (18 <= h < 21) or (h == 21 and m <= 15):
            price = 0.44

        timeline.append({"dt": dt, "price": price})

    peaks, slot_map = detect_dynamic_price_peaks(timeline, step_mins=15, max_lockout_mins=150)

    # Must be merged into exactly 1 Avondspits
    evening_peaks = [p for p in peaks if "Avond" in p.get("name", "")]
    assert len(evening_peaks) == 1
    ev_peak = evening_peaks[0]
    assert ev_peak["is_hard_lockout"] is True
    assert ev_peak["hard_duration_mins"] <= 150

    # Ensure no short-cycling: inside the peak event, there is only one continuous hard lockout block
    hard_slots = [k for k, v in slot_map.items() if v.get("is_hard_lockout") and "Avond" in v.get("name", "")]
    for j in range(len(hard_slots) - 1):
        # All hard slots must be contiguous without gaps!
        assert hard_slots[j+1] == hard_slots[j] + 1
