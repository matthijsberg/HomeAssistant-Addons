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
