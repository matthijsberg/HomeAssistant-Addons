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


def test_tariff_provider_fallback_markup_export_ingestion():
    """
    Verifies that TariffProvider.from_dict() properly ingests fallback_markup_export
    and that changes in this configuration directly propagate into calculate_export_value_from_import().
    """
    config_dict_standard = {
        "dynamic_tariffs": {
            "fallback_markup_import": 0.0121,
            "fallback_markup_export": 0.0121,
            "fallback_tax_electricity": 0.11085,
            "fallback_fixed_monthly_fee": 6.25
        }
    }
    tp_standard = TariffProvider.from_dict(config_dict_standard)
    assert tp_standard.config.export_fixed_markup_eur == 0.0121

    # Price all-in for spot=0.10
    p_all_in = (0.10 + 0.0121 + 0.11085) * 1.21  # 0.2697695
    p_export_standard = tp_standard.calculate_export_value_from_import(p_all_in)
    # Expected: spot (0.10) - penalty (0.00605) + markup_export (0.0121) = 0.10605
    assert abs(p_export_standard - 0.10605) < 0.00001

    # Modified configuration with higher export markup (e.g. 0.0250)
    config_dict_modified = {
        "dynamic_tariffs": {
            "fallback_markup_import": 0.0121,
            "fallback_markup_export": 0.0250,
            "fallback_tax_electricity": 0.11085,
            "fallback_fixed_monthly_fee": 6.25
        }
    }
    tp_modified = TariffProvider.from_dict(config_dict_modified)
    assert tp_modified.config.export_fixed_markup_eur == 0.0250

    p_export_modified = tp_modified.calculate_export_value_from_import(p_all_in)
    # Expected: spot (0.10) - penalty (0.00605) + markup_export (0.0250) = 0.11895
    assert abs(p_export_modified - 0.11895) < 0.00001
    assert abs((p_export_modified - p_export_standard) - (0.0250 - 0.0121)) < 0.00001


def test_solar_valuation_opportunity_cost_scenario():
    """
    Verifies dispatch valuation scenario:
    1000W baseload, 2000W solar production, 2500W heat pump demand.
    Expects 1000W at net export opportunity value + 1500W at all-in consumer import price.
    """
    from layer3_scheduling.dhw_financials import calculate_slot_financials

    config_dict = {
        "dynamic_tariffs": {
            "fallback_markup_import": 0.0121,
            "fallback_markup_export": 0.0121,
            "fallback_tax_electricity": 0.11085,
            "fallback_fixed_monthly_fee": 6.25
        }
    }
    tp = TariffProvider.from_dict(config_dict)

    # Spot = 0.10 €/kWh
    price_all_in = (0.10 + 0.0121 + 0.11085) * 1.21  # 0.2697695 €/kWh
    p_export = tp.calculate_export_value_from_import(price_all_in)  # 0.10605 €/kWh

    # Slot financials for 15-minute slot (0.25h)
    cost_eur, self_kwh, grid_kwh, p_eff = calculate_slot_financials(
        solar_kw=2.0,       # 2000W PV
        unalloc_kw=1.0,     # 1000W Baseload -> 1000W surplus
        el_demand_kw=2.5,   # 2500W Heat Pump demand
        price_all_in=price_all_in,
        step_hours=0.25,
        tariff_provider=tp
    )

    # 1000W surplus for 15m = 0.25 kWh
    assert abs(self_kwh - 0.25) < 0.0001
    # 2500W total - 1000W solar = 1500W grid import for 15m = 0.375 kWh
    assert abs(grid_kwh - 0.375) < 0.0001

    # Valuation: 1000W (0.25 kWh) @ p_export (€0.10605) + 1500W (0.375 kWh) @ price_all_in (€0.26977)
    expected_cost = (0.375 * price_all_in) + (0.25 * p_export)
    assert abs(cost_eur - expected_cost) < 0.00001
    assert abs(cost_eur - 0.127676) < 0.0001


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
    from layer3_scheduling.peak_detection import detect_dynamic_price_peaks
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
