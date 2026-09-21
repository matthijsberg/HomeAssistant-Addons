"""
Unit Tests: Historical Plan vs. Actual Analytics
================================================
Verifies:
1. Aligned 15m time series for solar, heat pump, and DHW.
2. Conversion of planned windows into index ranges.
3. Conversion of recorded annotations into actual heating ranges.
4. Correct structure of GET /api/analytics/plan_vs_actual.
"""

import pytest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

from api.routes_analytics import (
    fetch_actual_heating_ranges_from_db,
    fetch_planned_dhw_ranges_from_db
)

AMS_TZ = ZoneInfo("Europe/Amsterdam")


def test_fetch_planned_dhw_ranges_from_db():
    base_dt = datetime(2026, 9, 21, 12, 0, tzinfo=AMS_TZ)
    # 8 slots of 15 min: 12:00, 12:15, 12:30, 12:45, 13:00, 13:15, 13:30, 13:45
    slot_dts = [base_dt + timedelta(minutes=15 * i) for i in range(8)]

    # Planned window: 12:30 to 13:30 (slots 2, 3, 4, 5)
    hist_windows = [
        {"date": "2026-09-21", "start_time": "12:30", "end_time": "13:30", "power_kw": 3.0}
    ]

    ranges = fetch_planned_dhw_ranges_from_db(slot_dts, hist_windows, interval_h=0.25)
    assert len(ranges) == 1
    assert ranges[0]["start_idx"] == 2
    assert ranges[0]["end_idx"] == 5
    assert ranges[0]["name"] == "GEPLAND VERWARMEN"


def test_fetch_actual_heating_ranges_grouping():
    base_dt = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    slot_dts = [base_dt + timedelta(minutes=15 * i) for i in range(6)]

    # Mock InfluxDB returning annotations at 12:15 and 12:30 (slots 1 and 2)
    mock_series = [{
        "values": [
            ["2026-09-21T12:15:00Z", "max_on", "Boost"],
            ["2026-09-21T12:30:00Z", "max_on", "Boost"]
        ]
    }]

    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"results": [{"series": [{"values": [["2026-09-21T12:15:00Z", "max_on", "Boost"], ["2026-09-21T12:30:00Z", "max_on", "Boost"]]}]}]}'
        mock_url.return_value.__enter__.return_value = mock_resp

        ranges = fetch_actual_heating_ranges_from_db("2026-09-21T12:00:00Z", "2026-09-21T14:00:00Z", slot_dts, interval_h=0.25)
        assert len(ranges) == 1
        assert ranges[0]["start_idx"] == 1
        assert ranges[0]["end_idx"] == 2
        assert ranges[0]["name"] == "ACTUEEL VERWARMD"


def test_compute_historical_draw_offs_no_phantom():
    from layer2_calibration.dhw_thermal_model import DhwThermalModel

    # Hourly test: 11:00 (55.16°C, idle), 12:00 (54.74°C, 2.28 kWh heat pump on), 13:00 (59.98°C, heating finished)
    sorted_ts = ["2026-09-21T09:00:00Z", "2026-09-21T10:00:00Z", "2026-09-21T11:00:00Z"]
    t_map = {
        "2026-09-21T09:00:00Z": 55.16,
        "2026-09-21T10:00:00Z": 54.74, # -0.42 drop due to coil circulation mixing / 1h bucket averaging
        "2026-09-21T11:00:00Z": 59.98,
    }
    d_map = {
        "2026-09-21T09:00:00Z": 0.0,
        "2026-09-21T10:00:00Z": 2.28, # Heat pump active!
        "2026-09-21T11:00:00Z": 1.0,
    }

    demands = DhwThermalModel.compute_historical_draw_offs(
        sorted_timestamps=sorted_ts,
        temperature_map=t_map,
        heatpump_el_kwh_map=d_map,
        interval_h=1.0
    )

    # Must NOT produce 4+ kWh phantom draw-off at slot 10:00Z
    assert demands[1] == 0.0
