# Open HEMS Architecture Test: Comprehensive Chart & Overlay Invariants Across All Pages
import re
from pathlib import Path
from layer3_scheduling.peak_detection import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
from api.context import ensure_active_canonical_plan

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_plugin_uses_strict_integer_indices_only():
    """Guarantee that OpenHEMSSpitsblokPlugin NEVER performs fragile string lookups or findIndex."""
    app_js = REPO_ROOT / "web" / "js" / "app.js"
    assert app_js.exists(), "app.js must exist"
    content = app_js.read_text(encoding="utf-8")

    # Extract OpenHEMSSpitsblokPlugin definition
    plugin_match = re.search(r"const OpenHEMSSpitsblokPlugin\s*=\s*\{([\s\S]*?)\n\s*\};", content)
    assert plugin_match, "OpenHEMSSpitsblokPlugin must be defined in app.js"
    plugin_body = plugin_match.group(1)

    assert "findIndex" not in plugin_body, "OpenHEMSSpitsblokPlugin must NOT use findIndex (causes multi-day stretch bugs)"
    assert "start_label" not in plugin_body, "OpenHEMSSpitsblokPlugin must use strict start_idx, never start_label"
    assert "end_label" not in plugin_body, "OpenHEMSSpitsblokPlugin must use strict end_idx, never end_label"
    assert "getRangeBounds" in plugin_body, "OpenHEMSSpitsblokPlugin must use bounded integer validation"


def test_all_pages_overlay_invariants_across_resolutions():
    """Guarantee that overlays on all pages (15m and 1h) have strict bounds, zero overlap, and duration caps."""
    plan = ensure_active_canonical_plan()
    assert plan and plan.slots, "Canonical plan must be active"

    for is_15m in [True, False]:
        res_label = "15m" if is_15m else "1h"
        hist_count = 4 if is_15m else 1
        total_slots = (len(plan.slots) if is_15m else len(plan.slots) // 4) + hist_count

        spits = extract_plan_spitsblok_ranges(plan.slots, history_count=hist_count, is_15m=is_15m)
        heats = extract_plan_heating_ranges(plan.slots, history_count=hist_count, is_15m=is_15m, domain="space_heating")

        spits_set = set()
        for s in spits:
            s_idx, e_idx = s["start_idx"], s["end_idx"]
            assert 0 <= s_idx <= e_idx < total_slots, f"Spitsblok range out of bounds ({res_label}): {s}"
            dur_slots = e_idx - s_idx + 1
            max_dur = 20 if is_15m else 5  # max 5 hours
            assert dur_slots <= max_dur, f"Spitsblok duration excessive ({res_label}): {dur_slots} > {max_dur}"
            for i in range(s_idx, e_idx + 1):
                spits_set.add(i)

        for h in heats:
            s_idx, e_idx = h["start_idx"], h["end_idx"]
            assert 0 <= s_idx <= e_idx < total_slots, f"Heating range out of bounds ({res_label}): {h}"
            dur_slots = e_idx - s_idx + 1
            max_dur = 32 if is_15m else 8  # max 8 hours
            assert dur_slots <= max_dur, f"Heating duration excessive ({res_label}): {dur_slots} > {max_dur}"
            for i in range(s_idx, e_idx + 1):
                assert i not in spits_set, f"ILLEGAL OVERLAP at slot {i} ({res_label}): active heating overlaps with spitsblok!"


def test_forecast_kpi_cards_carry_clean_titles():
    """Guarantee that forecast KPI cards declare clean, uncluttered titles without redundant (24u Rollend)."""
    index_html = REPO_ROOT / "web" / "index.html"
    content = index_html.read_text(encoding="utf-8")
    assert 'id="pred-kpi-costs-title"' in content
    assert 'id="pred-kpi-solar-title"' in content
    assert 'id="pred-kpi-savings-title"' in content
    assert 'id="pred-kpi-hp-title"' in content
    assert 'Kosten (24u Rollend)' not in content


def test_heating_forecast_resolution_parity():
    """Guarantee that /api/model/heating-forecast returns label count and overlays matching resolution."""
    from api.routes_model import handle_get

    class DummyHandler:
        def __init__(self, path):
            self.path = path
            self.response = None
        def _send_json(self, data):
            self.response = data

    # 15m mode: exactly 4 history + 96 future = 100 slots
    h15 = DummyHandler('/api/model/heating-forecast?resolution=15m')
    handle_get(h15, '/api/model/heating-forecast', {'resolution': ['15m']})
    d15 = h15.response
    assert d15 is not None
    assert len(d15['labels']) == 100
    assert len(d15['indoor_temps_c']) == 100
    for s in d15['forced_off_ranges']:
        assert 0 <= s['start_idx'] <= s['end_idx'] < 100
    for a in d15.get('advised_off_ranges', []):
        assert 0 <= a['start_idx'] <= a['end_idx'] < 100
    for h in d15['heating_ranges']:
        assert 0 <= h['start_idx'] <= h['end_idx'] < 100

    # 1h mode: exactly 1 history + 24 future = 25 slots
    h1 = DummyHandler('/api/model/heating-forecast?resolution=1h')
    handle_get(h1, '/api/model/heating-forecast', {'resolution': ['1h']})
    d1 = h1.response
    assert d1 is not None
    assert len(d1['labels']) == 25
    assert len(d1['indoor_temps_c']) == 25
    for s in d1['forced_off_ranges']:
        assert 0 <= s['start_idx'] <= s['end_idx'] < 25
    for a in d1.get('advised_off_ranges', []):
        assert 0 <= a['start_idx'] <= a['end_idx'] < 25
    for h in d1['heating_ranges']:
        assert 0 <= h['start_idx'] <= h['end_idx'] < 25


def test_heating_history_resolution_parity():
    """Guarantee that /api/analytics/heating_history returns matching resolution datasets."""
    from api.routes_analytics import handle_get

    class DummyHandler:
        def __init__(self, path):
            self.path = path
            self.response = None
        def _send_json(self, data):
            self.response = data

    # 1h mode: ~24-25 slots
    h1 = DummyHandler('/api/analytics/heating_history?range=24h&resolution=1h')
    handle_get(h1, '/api/analytics/heating_history', {'range': ['24h'], 'resolution': ['1h']})
    d1 = h1.response
    assert d1 is not None
    assert d1['status'] == 'success'
    n1 = len(d1['labels'])
    assert len(d1['indoor_temperatures_c']) == n1
    assert len(d1['outdoor_temperatures_c']) == n1
    assert len(d1['demand_kwh_th']) == n1
    for s in d1['forced_off_ranges']:
        assert 0 <= s['start_idx'] <= s['end_idx'] < n1
    for h in d1['heating_ranges']:
        assert 0 <= h['start_idx'] <= h['end_idx'] < n1


def test_all_chart_endpoints_resolution_parity_and_alignment():
    """Invariant #8: Enforces 15m vs 1h peak and soft-advice alignment across all historical endpoints."""
    from api.routes_analytics import handle_get

    class DummyHandler:
        def __init__(self, path):
            self.path = path
            self.response = None
        def _send_json(self, data, status=200):
            self.response = data

    endpoints = [
        "/api/analytics/power_producers?range=24h",
        "/api/analytics/dhw_history?range=24h",
        "/api/analytics/heating_history?range=24h",
    ]

    for ep in endpoints:
        h15 = DummyHandler(f"{ep}&resolution=15m")
        handle_get(h15, ep.split("?")[0], {"range": ["24h"], "resolution": ["15m"]})
        d15 = h15.response
        assert d15 is not None
        assert d15.get("status") == "success"

        h1 = DummyHandler(f"{ep}&resolution=1h")
        handle_get(h1, ep.split("?")[0], {"range": ["24h"], "resolution": ["1h"]})
        d1 = h1.response
        assert d1 is not None
        assert d1.get("status") == "success"

        f15 = d15.get("forced_off_ranges", [])
        f1 = d1.get("forced_off_ranges", [])

        # Check bounds
        n15 = len(d15["labels"])
        n1 = len(d1["labels"])
        for r in f15:
            assert 0 <= r["start_idx"] <= r["end_idx"] < n15
        for r in f1:
            assert 0 <= r["start_idx"] <= r["end_idx"] < n1

        # Check hour alignment if peaks exist in both
        if f15 and f1:
            h_15 = int(f15[0]["start_label"][:2])
            h_1 = int(f1[0]["start_label"][:2])
            assert abs(h_15 - h_1) <= 1, f"Peak start hour desync on {ep}: 15m={h_15} vs 1h={h_1}"


def test_battery_status_resolution_parity():
    """Invariant #8: Enforces 15m vs 1h parity and alignment on the battery dispatch endpoint."""
    from api.routes_forecast import handle_get

    class DummyHandler:
        def __init__(self, path):
            self.path = path
            self.response = None
        def _send_json(self, data, status=200):
            self.response = data

    h15 = DummyHandler("/api/model/battery-status?resolution=15m&horizon=24h")
    ok15 = handle_get(h15, "/api/model/battery-status", {"resolution": ["15m"], "horizon": ["24h"]})
    assert ok15 is True
    d15 = h15.response
    assert d15 is not None
    assert d15.get("status") == "success"
    assert len(d15.get("labels", [])) == 96
    assert len(d15.get("trajectory", {}).get("soc_pct", [])) == 96

    h1 = DummyHandler("/api/model/battery-status?resolution=1h&horizon=24h")
    ok1 = handle_get(h1, "/api/model/battery-status", {"resolution": ["1h"], "horizon": ["24h"]})
    assert ok1 is True
    d1 = h1.response
    assert d1 is not None
    assert d1.get("status") == "success"
    assert len(d1.get("labels", [])) == 24
    assert len(d1.get("trajectory", {}).get("soc_pct", [])) == 24

    # Verify ranges are within bounds
    for r in d15.get("charge_ranges", []):
        assert 0 <= r["start_idx"] <= r["end_idx"] < 96
    for r in d15.get("discharge_ranges", []):
        assert 0 <= r["start_idx"] <= r["end_idx"] < 96
    for r in d1.get("charge_ranges", []):
        assert 0 <= r["start_idx"] <= r["end_idx"] < 24
    for r in d1.get("discharge_ranges", []):
        assert 0 <= r["start_idx"] <= r["end_idx"] < 24


def test_reusable_ui_and_chart_contracts():
    """Guarantee that OpenHEMSUI, OpenHEMSTokens, and OpenHEMSChartEngine exist, are globally exported, and define standardized components."""
    app_js = (REPO_ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    index_html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    charts_thermal_js = (REPO_ROOT / "web" / "js" / "modules" / "charts_thermal.js").read_text(encoding="utf-8")

    # 1. Global Exports
    assert "window.OpenHEMSTokens = OpenHEMSTokens;" in app_js
    assert "window.OpenHEMSUI = OpenHEMSUI;" in app_js
    assert "window.OpenHEMSChartEngine = OpenHEMSChartEngine;" in app_js

    # 2. Standardized Legend Component
    assert "renderLegend(containerOrId, items)" in app_js
    assert 'id="battery-trajectory-legend"' in index_html
    assert 'id="cost-forecast-legend"' in index_html
    assert 'id="cost-history-legend"' in index_html
    assert "OpenHEMSUI.renderLegend('battery-trajectory-legend'" in charts_thermal_js

    # 3. Standardized Chart.js Preset Factory
    assert "createDualAxisOptions" in app_js
    assert "createBarDataset" in app_js
    assert "barPercentage: 0.9" in app_js
    assert "categoryPercentage: 0.9" in app_js

    # 4. Color Tokens Consistency
    assert "solarCharge: '#F59E0B'" in app_js
    assert "discharge: '#10B981'" in app_js
    assert "gridCharge: '#8B5CF6'" in app_js



