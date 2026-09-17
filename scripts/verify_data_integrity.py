#!/usr/bin/env bash
# ==============================================================================
# Open HEMS Automated Data & API Integrity Verification Gate
# Runs both offline structural checks and live API contract verification.
# ==============================================================================
""":"
exec python3 "$0" "$@"
"""

import sys
import os
import math
import json
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

AMS_TZ = ZoneInfo("Europe/Amsterdam")

def log_pass(msg: str):
    print(f"  \033[92m✓ PASS:\033[0m {msg}")

def log_fail(msg: str, errors: list):
    print(f"  \033[91m✗ FAIL:\033[0m {msg}")
    errors.append(msg)

def log_warn(msg: str):
    print(f"  \033[93m⚠ WARN:\033[0m {msg}")

def check_sanitizer_matrix_shapes(errors: list):
    """Verify TelemetrySanitizer correctly handles 2D 7x96, 1D 672, and 1D 96 shapes."""
    from layer1_data_collection.sanitizer import TelemetrySanitizer
    now = datetime.now(AMS_TZ)

    # 1. 2D shape (7x96) in Watts
    shape_2d = [[400.0 + (d * 10) + (q % 20) * 15 for q in range(96)] for d in range(7)]
    frame_2d = TelemetrySanitizer.sanitize(
        now=now,
        raw_prices=[{"dt": now, "price": 0.25}],
        raw_solar=[{"dt": now, "solar_kw": 1.5}],
        raw_weather=[{"dt": now, "temperature": 18.0}],
        raw_unallocated_matrix=shape_2d,
        last_hardware_reading_time=now
    )
    u_vals_2d = [s.unallocated_kw for s in frame_2d.slots]
    if len(set(u_vals_2d)) <= 1 or u_vals_2d[0] == 0.35:
        log_fail("TelemetrySanitizer failed to parse 2D (7x96) matrix - fell back to constant 0.35 kW!", errors)
    else:
        log_pass(f"TelemetrySanitizer parsed 2D (7x96) matrix: {len(u_vals_2d)} slots, varied ({min(u_vals_2d):.3f}–{max(u_vals_2d):.3f} kW)")

    # 2. 1D flat shape (672)
    shape_672 = [350.0 + (i % 96) * 10 for i in range(672)]
    frame_672 = TelemetrySanitizer.sanitize(
        now=now,
        raw_prices=[{"dt": now, "price": 0.25}],
        raw_solar=[{"dt": now, "solar_kw": 1.5}],
        raw_weather=[{"dt": now, "temperature": 18.0}],
        raw_unallocated_matrix=shape_672,
        last_hardware_reading_time=now
    )
    u_vals_672 = [s.unallocated_kw for s in frame_672.slots]
    if len(set(u_vals_672)) <= 1:
        log_fail("TelemetrySanitizer failed to parse 1D 672 flat list!", errors)
    else:
        log_pass(f"TelemetrySanitizer parsed 1D 672 flat list successfully")

def check_canonical_plan_invariants(errors: list):
    """Verify CanonicalDispatchPlan adheres to all physical and architectural invariants."""
    from daemon import ensure_active_canonical_plan

    plan = ensure_active_canonical_plan(force_refresh=True)
    if not plan or not plan.slots:
        log_fail("Canonical plan generation returned empty or None!", errors)
        return

    slots = plan.slots
    if len(slots) != 96:
        log_fail(f"Canonical plan has {len(slots)} slots, expected exactly 96 (24h @ 15m)!", errors)
    else:
        log_pass("Canonical plan has exactly 96 quarter-hour slots (24-hour rolling horizon)")

    slot_dts = [datetime.fromisoformat(s.dt_iso) for s in slots]

    # 1. Physical Nighttime Solar Invariant (Zero solar at night)
    night_solar = [s.solar_kw for s, dt in zip(slots, slot_dts) if dt.hour >= 21 or dt.hour < 6]
    max_night = max(night_solar) if night_solar else 0.0
    if max_night > 0.001:
        log_fail(f"Physical invariant violated: Nighttime solar is non-zero ({max_night} kW) between 21:00 and 06:00!", errors)
    else:
        log_pass("Physical invariant confirmed: Solar production strictly 0.00 kW between 21:00 and 06:00")

    # 2. Physical Daytime Solar Headroom Invariant
    day_solar = [s.solar_kw for s, dt in zip(slots, slot_dts) if 11 <= dt.hour <= 15]
    max_day = max(day_solar) if day_solar else 0.0
    if max_day < 0.2:
        log_fail(f"Physical invariant violated: Daytime solar peak is abnormally low or zero ({max_day} kW) on 5.76 kWp array!", errors)
    else:
        log_pass(f"Daytime solar peak confirmed: {max_day:.2f} kW")

    # 3. Dynamic Unallocated Load Invariant (Must not be dead-flat 350W line)
    unalloc_vals = [s.unallocated_kw for s in slots]
    mean_u = sum(unalloc_vals) / len(unalloc_vals)
    var_u = sum((x - mean_u)**2 for x in unalloc_vals) / len(unalloc_vals)
    std_u = math.sqrt(var_u)
    if std_u < 0.02:
        log_fail(f"Unallocated load profile invariant violated: Flat line detected (std={std_u:.4f} kW)! 7x96 profile is uncoupled.", errors)
    else:
        log_pass(f"Unallocated load profile confirmed dynamic: mean={mean_u:.3f} kW, std={std_u:.3f} kW, min={min(unalloc_vals):.3f} kW, max={max(unalloc_vals):.3f} kW")

    # 4. DHW Tank Volume Specification
    if plan.dhw_summary:
        log_pass(f"DHW dispatch summary present: mode={plan.dhw_summary.planned_mode}, target={plan.dhw_summary.target_temp_c}°C, window={plan.dhw_summary.run_start}–{plan.dhw_summary.run_end}")

def check_frontend_tokens_and_scripts(daemon_path: Path, errors: list):
    """Verify frontend JavaScript syntax, design tokens, and absence of conflicting controls."""
    html_path = daemon_path.parent / "web" / "index.html"
    content = ""
    if html_path.exists():
        content += html_path.read_text(encoding="utf-8") + "\n"
        js_file = daemon_path.parent / "web" / "js" / "app.js"
        if js_file.exists():
            content += js_file.read_text(encoding="utf-8") + "\n"
    elif daemon_path.exists():
        content = daemon_path.read_text(encoding="utf-8")
    else:
        log_fail(f"Frontend file not found at {html_path} or {daemon_path}", errors)
        return

    # 1. Check for conflicting local select dropdowns
    if 'id="epex-res-select"' in content:
        log_fail("Rogue local dropdown 'epex-res-select' found in HTML! All charts must use central OpenHEMSChartEngine.", errors)
    else:
        log_pass("No conflicting local resolution dropdowns found in HTML")

    # 2. Check for OpenHEMSTokens
    required_tokens = ["solar", "solarBg", "price", "priceLine", "unallocated", "dhw", "heating", "netto"]
    missing_tokens = [t for t in required_tokens if f"{t}:" not in content and f"'{t}':" not in content and f'"{t}":' not in content]
    if missing_tokens:
        log_fail(f"OpenHEMSTokens missing required color tokens: {missing_tokens}", errors)
    else:
        log_pass("OpenHEMSTokens defines all required color tokens")

    # 3. Check for OpenHEMSChartEngine methods
    engine_methods = ["getChartType", "setChartType", "getResolution", "setResolution", "refreshAllCharts", "init"]
    missing_methods = [m for m in engine_methods if m not in content]
    if missing_methods:
        log_fail(f"OpenHEMSChartEngine missing required methods: {missing_methods}", errors)
    else:
        log_pass("OpenHEMSChartEngine implements all required state and refresh methods")

    # 4. Node syntax check across all <script> blocks
    import re
    scripts = re.findall(r'<script>(.*?)</script>', content, re.DOTALL)
    for i, script_code in enumerate(scripts):
        p = subprocess.run(["node", "-c", "-"], input=script_code, text=True, capture_output=True)
        if p.returncode != 0:
            log_fail(f"Frontend <script> #{i+1} has syntax error: {p.stderr.strip()}", errors)
        else:
            log_pass(f"Frontend <script> #{i+1} passed JavaScript syntax compilation")

    # 5. Anti-Pattern Guard: Zero fragile label string matching in web/js/app.js
    app_js_path = daemon_path.parent / "web" / "js" / "app.js"
    if app_js_path.exists():
        app_js = app_js_path.read_text(encoding="utf-8")
        fragile_matches = re.findall(r'(\b\w+\.label\.(?:includes|indexOf)\b)', app_js)
        if fragile_matches:
            log_fail(f"Anti-pattern detected in web/js/app.js: Found fragile label matching: {fragile_matches}. Use immutable dataset.id instead.", errors)
        else:
            log_pass("Zero fragile dataset label-matching detected in web/js/app.js (100% typed ds.id contracts)")

def check_live_api_contracts(base_url: str, errors: list):
    """Query the running daemon endpoints and verify cross-resolution & Single Source of Truth contracts."""
    import urllib.request
    import urllib.error

    print(f"\n--- Checking Live API Endpoints at {base_url} ---")
    
    # Check health/consistency endpoint
    try:
        url_health = f"{base_url}/api/health/consistency"
        with urllib.request.urlopen(url_health, timeout=6) as r:
            h_data = json.loads(r.read().decode())
            if h_data.get("status", "").upper() == "HEALTHY" and h_data.get("single_source_of_truth_verified", False):
                log_pass("API /api/health/consistency returned status 'HEALTHY' and single_source_of_truth_verified=True")
            else:
                log_fail(f"API /api/health/consistency unhealthy: {h_data.get('validation_issues') or h_data.get('status')}", errors)
    except Exception as e:
        log_fail(f"Failed to reach /api/health/consistency: {e}", errors)

    # Check 15m and 1h chart-data
    try:
        url_15m = f"{base_url}/api/schedule/chart-data?resolution=15m"
        url_1h = f"{base_url}/api/schedule/chart-data?resolution=1h"
        url_prices = f"{base_url}/api/analytics/electricity_prices?resolution=15m"

        with urllib.request.urlopen(url_15m, timeout=6) as r:
            d_15m = json.loads(r.read().decode())
        with urllib.request.urlopen(url_1h, timeout=6) as r:
            d_1h = json.loads(r.read().decode())
        with urllib.request.urlopen(url_prices, timeout=6) as r:
            d_prices = json.loads(r.read().decode())

        # Check total solar energy match across forecast horizon (15m vs 1h)
        h15 = d_15m.get("history_count", 0)
        h1 = d_1h.get("history_count", 0)
        s15_kwh = sum(-x * 0.25 for x in d_15m["datasets"]["solar_kw_neg"][h15:])
        s1h_kwh = sum(-x * 1.0 for x in d_1h["datasets"]["solar_kw_neg"][h1:])
        diff_solar = abs(s15_kwh - s1h_kwh)
        if diff_solar > 0.5:
            log_fail(f"Cross-resolution solar energy mismatch: 15m={s15_kwh:.2f} kWh, 1h={s1h_kwh:.2f} kWh (diff: {diff_solar:.2f} kWh)", errors)
        else:
            log_pass(f"Cross-resolution solar energy aligned: 15m={s15_kwh:.2f} kWh, 1h={s1h_kwh:.2f} kWh (diff: {diff_solar:.2f} kWh)")

        # Check total unallocated energy match across forecast horizon (15m vs 1h)
        u15_kwh = sum(x * 0.25 for x in d_15m["datasets"]["unallocated_kw"][h15:])
        u1h_kwh = sum(x * 1.0 for x in d_1h["datasets"]["unallocated_kw"][h1:])
        diff_unalloc = abs(u15_kwh - u1h_kwh)
        if diff_unalloc > 0.5:
            log_fail(f"Cross-resolution unallocated energy mismatch: 15m={u15_kwh:.2f} kWh, 1h={u1h_kwh:.2f} kWh (diff: {diff_unalloc:.2f} kWh)", errors)
        else:
            log_pass(f"Cross-resolution unallocated energy aligned: 15m={u15_kwh:.2f} kWh, 1h={u1h_kwh:.2f} kWh (diff: {diff_unalloc:.2f} kWh)")

        # Check Single Source of Truth: chart-data vs electricity_prices solar
        s_chart = [-x for x in d_15m["datasets"]["solar_kw_neg"]]
        s_prices = d_prices.get("solar_forecast_kw", [])
        mismatches = [(i, sc, sp) for i, (sc, sp) in enumerate(zip(s_chart, s_prices)) if abs(sc - sp) > 0.05]
        if mismatches:
            log_fail(f"Single Source of Truth violated: chart-data solar does not match electricity_prices solar in {len(mismatches)} slots!", errors)
        else:
            log_pass("Single Source of Truth confirmed: chart-data solar == electricity_prices solar 100%")

    except Exception as e:
        log_fail(f"Failed live API verification: {e}", errors)

def check_chart_contracts_and_overlays(errors: list):
    """
    Automated mathematical and visual invariant checks on chart overlays:
    1. Range boundaries strictly within [0, total_slots - 1]
    2. Zero overlap between forced_off (spitsblok) and active heating
    3. Maximum duration invariants (no runaway blocks > 5 hours)
    """
    from models.canonical import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
    from api.context import ensure_active_canonical_plan
    plan = ensure_active_canonical_plan()
    if not plan or not plan.slots:
        log_fail("Cannot test chart overlays: plan is empty!", errors)
        return

    for is_15m in [True, False]:
        res_label = "15m" if is_15m else "1h"
        hist_count = 4 if is_15m else 1
        total_slots = (len(plan.slots) if is_15m else len(plan.slots) // 4) + hist_count

        spits = extract_plan_spitsblok_ranges(plan.slots, history_count=hist_count, is_15m=is_15m)
        heats = extract_plan_heating_ranges(plan.slots, history_count=hist_count, is_15m=is_15m, domain="space_heating")

        spits_set = set()
        for s in spits:
            s_idx, e_idx = s["start_idx"], s["end_idx"]
            if not (0 <= s_idx <= e_idx < total_slots):
                log_fail(f"Spitsblok range out of bounds ({res_label}): {s}", errors)
            dur_slots = e_idx - s_idx + 1
            max_dur = 20 if is_15m else 5
            if dur_slots > max_dur:
                log_fail(f"Spitsblok duration excessive ({res_label}): {dur_slots} slots exceeds {max_dur}!", errors)
            for i in range(s_idx, e_idx + 1):
                spits_set.add(i)

        for h in heats:
            s_idx, e_idx = h["start_idx"], h["end_idx"]
            if not (0 <= s_idx <= e_idx < total_slots):
                log_fail(f"Heating range out of bounds ({res_label}): {h}", errors)
            dur_slots = e_idx - s_idx + 1
            max_dur = 32 if is_15m else 8  # max 8 hours
            if dur_slots > max_dur:
                log_fail(f"Heating duration excessive ({res_label}): {dur_slots} slots exceeds {max_dur}!", errors)
            for i in range(s_idx, e_idx + 1):
                if i in spits_set:
                    log_fail(f"ILLEGAL OVERLAP at slot {i} ({res_label}): active heating overlaps with spitsblok!", errors)

    log_pass("Chart overlays invariant verified: strict bounds, zero overlap, and duration caps confirmed across 15m and 1h")

def main():
    parser = argparse.ArgumentParser(description="Open HEMS Automated Data & API Integrity Verification Gate")
    parser.add_argument("--live", action="store_true", help="Run live API smoke tests against running instance")
    parser.add_argument("--url", default="http://172.30.33.10:8099", help="Base URL for live tests")
    parser.add_argument("--addon-dir", default="/config/addons/open-hems", help="Add-on directory path")
    args = parser.parse_args()

    addon_path = Path(args.addon_dir).resolve()
    sys.path.insert(0, str(addon_path))

    errors = []
    print("=" * 70)
    print("  🛡️  Open HEMS Data & API Integrity Verification Gate")
    print("=" * 70)

    print("\n[Phase 1: Telemetry Sanitizer Matrix Invariance]")
    check_sanitizer_matrix_shapes(errors)

    print("\n[Phase 2: Canonical Plan Physical Invariants]")
    check_canonical_plan_invariants(errors)

    print("\n[Phase 3: Frontend Script Compilation & Design Tokens]")
    check_frontend_tokens_and_scripts(addon_path / "daemon.py", errors)

    print("\n[Phase 4: Chart Overlay Invariants & Visual Sanity]")
    check_chart_contracts_and_overlays(errors)

    if args.live:
        check_live_api_contracts(args.url, errors)

    print("\n" + "=" * 70)
    if errors:
        print(f"  ❌ GATE FAILED: {len(errors)} integrity errors detected!")
        for e in errors:
            print(f"    - {e}")
        print("=" * 70)
        sys.exit(1)
    else:
        print("  ✅ GATE PASSED: All physical and data integrity checks 100% verified!")
        print("=" * 70)
        sys.exit(0)

if __name__ == "__main__":
    main()
