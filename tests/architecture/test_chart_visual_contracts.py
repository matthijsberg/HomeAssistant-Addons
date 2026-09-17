# Open HEMS Architecture Test: Comprehensive Chart & Overlay Invariants Across All Pages
import re
from pathlib import Path
from models.canonical import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
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


def test_forecast_kpi_cards_carry_explicit_24u_rollend_titles():
    """Guarantee that forecast KPI cards declare 24u Rollend in their contracts."""
    index_html = REPO_ROOT / "web" / "index.html"
    content = index_html.read_text(encoding="utf-8")
    assert 'id="pred-kpi-costs-title"' in content
    assert 'Kosten (24u Rollend)' in content
    assert 'Zonnepanelen (24u Rollend)' in content
    assert 'Besparing (24u Rollend)' in content
    assert 'Warmtepomp (24u Rollend)' in content
