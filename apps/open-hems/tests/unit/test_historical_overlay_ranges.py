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


def test_historical_ranges_15m_and_1h_alignment():
    """Verify that switching between 15m and 1h resolutions keeps peak overlays aligned on identical times."""
    base = datetime(2026, 9, 26, 12, 0, 0, tzinfo=AMS_TZ)

    # 15m: 97 slots
    slots_15m = [base - timedelta(minutes=15 * (96 - i)) for i in range(97)]
    prices_15m = [0.20] * 97
    labels_15m = [dt.strftime("%H:%M") for dt in slots_15m]
    for i, dt in enumerate(slots_15m):
        if 20 <= dt.hour < 23:
            prices_15m[i] = 0.50

    forced_15m, advised_15m = fetch_historical_overlay_ranges(
        slots_15m[0].strftime("%Y-%m-%dT%H:%M:00Z"),
        slots_15m[-1].strftime("%Y-%m-%dT%H:%M:00Z"),
        slots_15m, prices=prices_15m, labels=labels_15m, interval_h=0.25
    )

    # 1h: 25 slots
    slots_1h = [base - timedelta(hours=(24 - i)) for i in range(25)]
    prices_1h = [0.20] * 25
    labels_1h = [dt.strftime("%H:00") for dt in slots_1h]
    for i, dt in enumerate(slots_1h):
        if 20 <= dt.hour < 23:
            prices_1h[i] = 0.50

    forced_1h, advised_1h = fetch_historical_overlay_ranges(
        slots_1h[0].strftime("%Y-%m-%dT%H:%M:00Z"),
        slots_1h[-1].strftime("%Y-%m-%dT%H:%M:00Z"),
        slots_1h, prices=prices_1h, labels=labels_1h, interval_h=1.0
    )

    assert len(forced_15m) > 0
    assert len(forced_1h) > 0
    # The start hour in 1h must align within 1 hour of 15m
    h_1h = int(forced_1h[0]["start_label"][:2])
    h_15m = int(forced_15m[0]["start_label"][:2])
    assert abs(h_1h - h_15m) <= 1


def test_historical_ranges_24h_vs_48h_multi_day_alignment():
    """Verify that multi-day 48h range does not offset day-2 peaks onto morning/night hours."""
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=AMS_TZ)

    # 48h slots (49 hourly slots)
    slots_48h = [now - timedelta(hours=(48 - i)) for i in range(49)]
    prices_48h = [0.20] * 49
    labels_48h = [dt.strftime("%d %H:00") for dt in slots_48h]

    # Set evening peak on day 2 (Sep 25) between 19:00 and 22:00
    for i, dt in enumerate(slots_48h):
        if dt.day == 25 and 19 <= dt.hour <= 22:
            prices_48h[i] = 0.50

    forced_48h, advised_48h = fetch_historical_overlay_ranges(
        slots_48h[0].strftime("%Y-%m-%dT%H:%M:00Z"),
        slots_48h[-1].strftime("%Y-%m-%dT%H:%M:00Z"),
        slots_48h, prices=prices_48h, labels=labels_48h, interval_h=1.0
    )

    assert len(forced_48h) > 0
    # Ensure the detected peak on the 25th starts in the evening (hour >= 18), NOT in early morning (02:00-05:00)
    for r in forced_48h:
        hour = int(r["start_label"].split()[1].split(":")[0])
        assert hour >= 17, f"48h peak offset bug: peak detected at hour {hour}, expected evening >= 17"

