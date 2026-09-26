"""
Unit Tests: Historical Chart Overlay Ranges & Database Logging
==============================================================
Verifies that historical endpoints return both forced_off_ranges (red hatching)
and advised_off_ranges (amber hatching), sourced from InfluxDB device_dispatch_history
and day-aligned dynamic price peak detection.
"""

from datetime import datetime, timedelta
import pytz
from api.routes_analytics import fetch_historical_overlay_ranges, handle_get

AMS_TZ = pytz.timezone("Europe/Amsterdam")


def test_fetch_historical_overlay_ranges_synthetic_prices():
    now = datetime.now(AMS_TZ)
    base = now.replace(minute=0, second=0, microsecond=0)
    slot_dts = [base - timedelta(minutes=15 * (96 - i)) for i in range(97)]
    labels = [dt.strftime("%H:%M") for dt in slot_dts]

    # Create prices with clear morning peak (07:00-09:00) and evening peak (18:00-21:30)
    prices = [0.20] * 97
    for i, dt in enumerate(slot_dts):
        if 7 <= dt.hour < 9:
            prices[i] = 0.40
        elif 18 <= dt.hour < 21:
            prices[i] = 0.50

    t_start_iso = slot_dts[0].strftime("%Y-%m-%dT%H:%M:00Z")
    t_end_iso = slot_dts[-1].strftime("%Y-%m-%dT%H:%M:00Z")

    forced, advised = fetch_historical_overlay_ranges(
        t_start_iso, t_end_iso, slot_dts, prices=prices, labels=labels, interval_h=0.25
    )

    # Should detect at least one hard lockout and one soft advice
    assert len(forced) > 0, "Expected at least one forced_off (red) range!"
    for r in forced:
        assert 0 <= r["start_idx"] <= r["end_idx"] < 97

    for r in advised:
        assert 0 <= r["start_idx"] <= r["end_idx"] < 97


def test_power_producers_returns_both_ranges():
    class DummyHandler:
        def __init__(self, path):
            self.path = path
            self.response = None
        def _send_json(self, data, status=200):
            self.response = data

    h = DummyHandler("/api/analytics/power_producers?range=24h&resolution=15m")
    handle_get(h, "/api/analytics/power_producers", {"range": ["24h"], "resolution": ["15m"]})

    assert h.response is not None
    assert h.response.get("status") == "success"
    assert "forced_off_ranges" in h.response
    assert "advised_off_ranges" in h.response
    assert isinstance(h.response["forced_off_ranges"], list)
    assert isinstance(h.response["advised_off_ranges"], list)


def test_electricity_prices_returns_both_ranges():
    class DummyHandler:
        def __init__(self, path):
            self.path = path
            self.response = None
        def _send_json(self, data, status=200):
            self.response = data

    h = DummyHandler("/api/analytics/electricity_prices?horizon=24h&resolution=15m")
    handle_get(h, "/api/analytics/electricity_prices", {"horizon": ["24h"], "resolution": ["15m"]})

    assert h.response is not None
    assert h.response.get("status") == "success"
    assert "forced_off_ranges" in h.response
    assert "advised_off_ranges" in h.response
    assert isinstance(h.response["forced_off_ranges"], list)
    assert isinstance(h.response["advised_off_ranges"], list)
