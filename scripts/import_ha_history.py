#!/usr/bin/env python3
"""
Open HEMS - Historical Data Importer from Home Assistant InfluxDB ('hassio' -> 'openhems')
Author: Matthijs van den Berg / Hermes Agent
Version: 1.0.0

Extracts 1-minute aggregated real telemetry from HA 'hassio' database for a full year:
  - P1 Import / Export (kW -> W)
  - Solar Generation (W)
  - Heat Pump Power & Mode Disaggregation (SWW vs Heating vs Standby)
  - Unallocated Household Consumption (W)
  - Outdoor Temperature (°C)
  - DHW Tank Temperature (°C)
Applies data exclusion windows (e.g. Modbus outage) to prevent distorted training data.
Stores cleanly into 'openhems' InfluxDB measurement 'energy_telemetry' using Line Protocol.
"""

import sys
import os
import argparse
import json
import gzip
import time
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Any, Optional

SOURCE_INFLUX_URL = "http://a0d7b954-influxdb:8086"
SOURCE_DB = "hassio"
SOURCE_USER = "hermes"
SOURCE_PWD_DEFAULT = "iu32dp§2g3dpuiy§g23pd9h23"

TARGET_INFLUX_URL = "http://a0d7b954-influxdb:8086"
TARGET_DB = "openhems"
TARGET_USER = "openhems"


def load_secrets() -> Dict[str, Any]:
    for p in ["/config/open_hems_secrets.json", "/homeassistant/open_hems_secrets.json"]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] Failed to read {p}: {e}")
    return {}


def load_config() -> Dict[str, Any]:
    for p in ["/config/heatpump_config.json", "/config/projects/energy-scheduler/config/heatpump_config.json"]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] Failed to read {p}: {e}")
    return {}


def query_influx(url: str, db: str, user: str, pwd: str, query: str) -> List[Dict[str, Any]]:
    params = {
        "u": user,
        "p": pwd,
        "db": db,
        "q": query,
        "epoch": "s"
    }
    full_url = f"{url}/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("results", [])


def write_influx_lines(url: str, db: str, user: str, pwd: str, lines: List[str]) -> bool:
    if not lines:
        return True
    payload = "\n".join(lines).encode("utf-8")
    write_url = f"{url}/write?" + urllib.parse.urlencode({
        "u": user,
        "p": pwd,
        "db": db,
        "precision": "s"
    })
    
    # Try sending compressed if large
    if len(payload) > 50000:
        req = urllib.request.Request(
            write_url,
            data=gzip.compress(payload),
            headers={"Content-Encoding": "gzip"},
            method="POST"
        )
    else:
        req = urllib.request.Request(write_url, data=payload, method="POST")
        
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in [200, 204]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[ERROR] Influx write failed ({e.code}): {err_body}")
        return False
    except Exception as e:
        print(f"[ERROR] Influx write network error: {e}")
        return False


def is_in_exclusion(dt: datetime, exclusion_windows: List[Dict[str, Any]]) -> bool:
    dt_date = dt.date()
    for win in exclusion_windows:
        start = datetime.strptime(win["start"], "%Y-%m-%d").date()
        end = datetime.strptime(win["end"], "%Y-%m-%d").date()
        if start <= dt_date <= end:
            return True
    return False


def process_chunk(
    start_dt: datetime,
    end_dt: datetime,
    src_user: str,
    src_pwd: str,
    target_user: str,
    target_pwd: str,
    exclusion_windows: List[Dict[str, Any]],
    dry_run: bool = False
) -> Dict[str, Any]:
    t_start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    t_end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Construct multi-query for fast single round-trip
    q = f"""
SELECT mean(value) FROM "kW" WHERE entity_id = 'power_consumption' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(linear);
SELECT mean(value) FROM "kW" WHERE entity_id = 'power_production' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(linear);
SELECT mean(value) FROM "W" WHERE entity_id = 'zonnepanelen_power' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(linear);
SELECT mean(value) FROM "W" WHERE entity_id = 'warmtepomp_power' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(linear);
SELECT mean(value) FROM "state" WHERE entity_id = 'hc_dhw_dhw_demand' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(previous);
SELECT mean(value) FROM "°C" WHERE entity_id = 'buiten_temperatuur' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(linear);
SELECT last(value) FROM "°C" WHERE entity_id = 'hc_dhw_temperature_r5t_dhw_tank' AND time >= '{t_start_iso}' AND time < '{t_end_iso}' GROUP BY time(1m) fill(previous)
"""
    results = query_influx(SOURCE_INFLUX_URL, SOURCE_DB, src_user, src_pwd, q)
    names = ["p1_import", "p1_export", "solar", "wp", "dhw_demand", "outdoor_temp", "dhw_temp"]
    series_map: Dict[str, Dict[int, float]] = {}

    all_timestamps = set()
    for idx, name in enumerate(names):
        series_map[name] = {}
        if idx < len(results) and results[idx].get("series"):
            vals = results[idx]["series"][0].get("values", [])
            for row in vals:
                ts = row[0]
                val = row[1]
                if val is not None:
                    series_map[name][ts] = float(val)
                    all_timestamps.add(ts)

    if not all_timestamps:
        return {"timestamps": 0, "lines": 0, "energy_kwh": 0.0}

    sorted_ts = sorted(list(all_timestamps))
    lines = []
    tot_unalloc_kwh = 0.0
    tot_wp_cv_kwh = 0.0
    tot_wp_dhw_kwh = 0.0
    tot_solar_kwh = 0.0

    for ts in sorted_ts:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        in_excl = is_in_exclusion(dt, exclusion_windows)

        p1_imp_kw = series_map["p1_import"].get(ts, 0.0)
        p1_exp_kw = series_map["p1_export"].get(ts, 0.0)
        p_imp = round(max(0.0, p1_imp_kw * 1000.0), 1)
        p_exp = round(max(0.0, p1_exp_kw * 1000.0), 1)

        sol_val = series_map["solar"].get(ts, 0.0)
        p_sol = round(abs(sol_val), 1)

        wp_raw = series_map["wp"].get(ts, 0.0)
        dhw_dem = series_map["dhw_demand"].get(ts, 0.0)
        out_temp = series_map["outdoor_temp"].get(ts)
        dhw_temp = series_map["dhw_temp"].get(ts)

        # Disaggregate heat pump
        p_wp = round(max(0.0, wp_raw), 1)
        if in_excl:
            # During Modbus power meter outage, mark mode as excluded and do not skew WP stats
            mode = "excluded"
            p_wp_calc = 0.0
        else:
            p_wp_calc = p_wp
            if p_wp < 48.0:
                mode = "standby"
            elif dhw_dem > 0.5:
                mode = "dhw"
                tot_wp_dhw_kwh += (p_wp / 60.0) / 1000.0
            else:
                mode = "heating"
                tot_wp_cv_kwh += (p_wp / 60.0) / 1000.0

        # Totals and balance
        p_tot = max(0.0, (p_imp - p_exp) + p_sol)
        p_dir_sol = max(0.0, min(p_sol, p_tot))
        
        if not in_excl:
            p_unalloc = max(0.0, p_tot - p_wp_calc)
            tot_unalloc_kwh += (p_unalloc / 60.0) / 1000.0
        else:
            p_unalloc = p_tot # conservative during exclusion

        tot_solar_kwh += (p_sol / 60.0) / 1000.0

        # Generate Line Protocol entries
        # 1. P1 Grid import
        lines.append(f"energy_telemetry,device_id=main_grid_meter,device_type=grid_meter,flow=IMPORT,source_type=ha_history_import,vector=ELECTRICITY power_w={p_imp} {ts}")
        # 2. P1 Grid export
        if p_exp > 0:
            lines.append(f"energy_telemetry,device_id=main_grid_meter,device_type=grid_meter,flow=EXPORT,source_type=ha_history_import,vector=ELECTRICITY power_w={p_exp} {ts}")
        # 3. Solar PV
        lines.append(f"energy_telemetry,device_id=rooftop_solar,device_type=solar_pv,flow=GENERATION,source_type=ha_history_import,vector=ELECTRICITY power_w={p_sol} {ts}")
        # 4. Heat Pump
        lines.append(f"energy_telemetry,device_id=daikin_heat_pump,device_type=heat_pump,flow=CONSUMPTION,source_type=ha_history_import,vector=HEAT,mode={mode} power_w={p_wp} {ts}")
        # 5. Synchronized Power Balance
        lines.append(
            f"energy_telemetry,source=ha_history_import,device_type=balance "
            f"p1_import_w={p_imp:.1f},p1_export_w={p_exp:.1f},solar_w={p_sol:.1f},"
            f"heatpump_w={p_wp:.1f},direct_solar_w={p_dir_sol:.1f},"
            f"total_house_w={p_tot:.1f},unallocated_w={p_unalloc:.1f} {ts}"
        )
        # 6. Outdoor temp
        if out_temp is not None:
            lines.append(f"energy_telemetry,device_id=outdoor_weather,device_type=weather_station,flow=STATE,source_type=ha_history_import,vector=HEAT temperature_c={round(out_temp, 2)} {ts}")
        # 7. DHW Tank temp
        if dhw_temp is not None:
            lines.append(f"energy_telemetry,device_id=dhw_tank,device_type=thermal_buffer,flow=STORAGE,source_type=ha_history_import,vector=HEAT temperature_c={round(dhw_temp, 2)} {ts}")

    if not dry_run:
        # Batch write to InfluxDB in chunks of 10,000 lines
        batch_size = 10000
        for i in range(0, len(lines), batch_size):
            chunk = lines[i:i + batch_size]
            success = write_influx_lines(TARGET_INFLUX_URL, TARGET_DB, target_user, target_pwd, chunk)
            if not success:
                print(f"[WARN] Failed writing batch at ts {sorted_ts[0]}")

    return {
        "timestamps": len(sorted_ts),
        "lines": len(lines),
        "unalloc_kwh": tot_unalloc_kwh,
        "wp_cv_kwh": tot_wp_cv_kwh,
        "wp_dhw_kwh": tot_wp_dhw_kwh,
        "solar_kwh": tot_solar_kwh
    }


def main():
    parser = argparse.ArgumentParser(description="Import 1-year historical telemetry from HA 'hassio' to 'openhems'")
    parser.add_argument("--start", type=str, default="2025-09-01T00:00:00Z", help="Start time in UTC ISO format (e.g. 2025-09-01T00:00:00Z)")
    parser.add_argument("--end", type=str, default="2026-09-01T00:00:00Z", help="End time in UTC ISO format (e.g. 2026-09-01T00:00:00Z)")
    parser.add_argument("--chunk-days", type=int, default=7, help="Number of days per processing chunk (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing to InfluxDB")
    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(args.end.replace("Z", "+00:00"))

    secrets = load_secrets()
    config = load_config()

    target_pwd = secrets.get("influxdb", {}).get("local_ha_influxdb", "")
    src_pwd = SOURCE_PWD_DEFAULT

    exclusion_windows = config.get("data_exclusion_windows", [])

    print(f"=== Open HEMS Historical Telemetry Importer ===")
    print(f"Period: {start_dt.isoformat()} -> {end_dt.isoformat()} ({(end_dt - start_dt).days} days)")
    print(f"Source DB: {SOURCE_DB} on {SOURCE_INFLUX_URL} (User: {SOURCE_USER})")
    print(f"Target DB: {TARGET_DB} on {TARGET_INFLUX_URL} (User: {TARGET_USER})")
    print(f"Exclusion windows configured: {len(exclusion_windows)}")
    print(f"Dry run: {args.dry_run}")
    print("------------------------------------------------")

    current_start = start_dt
    chunk_delta = timedelta(days=args.chunk_days)

    total_ts = 0
    total_lines = 0
    grand_unalloc_kwh = 0.0
    grand_wp_cv_kwh = 0.0
    grand_wp_dhw_kwh = 0.0
    grand_solar_kwh = 0.0

    t0 = time.time()
    chunk_idx = 1

    while current_start < end_dt:
        current_end = min(current_start + chunk_delta, end_dt)
        c_t0 = time.time()
        res = process_chunk(
            current_start,
            current_end,
            SOURCE_USER,
            src_pwd,
            TARGET_USER,
            target_pwd,
            exclusion_windows,
            dry_run=args.dry_run
        )
        c_dur = time.time() - c_t0
        total_ts += res["timestamps"]
        total_lines += res["lines"]
        grand_unalloc_kwh += res["unalloc_kwh"]
        grand_wp_cv_kwh += res["wp_cv_kwh"]
        grand_wp_dhw_kwh += res["wp_dhw_kwh"]
        grand_solar_kwh += res["solar_kwh"]

        pct = min(100.0, ((current_end - start_dt).total_seconds() / (end_dt - start_dt).total_seconds()) * 100.0)
        print(f"[{pct:5.1f}%] Chunk {chunk_idx:02d} ({current_start.strftime('%Y-%m-%d')} -> {current_end.strftime('%Y-%m-%d')}): "
              f"{res['timestamps']} mins | {res['lines']} lines | Sol: {res['solar_kwh']:.0f} kWh | "
              f"CV: {res['wp_cv_kwh']:.0f} kWh | DHW: {res['wp_dhw_kwh']:.0f} kWh | "
              f"Unalloc: {res['unalloc_kwh']:.0f} kWh ({c_dur:.2f}s)")

        current_start = current_end
        chunk_idx += 1

    total_dur = time.time() - t0
    print("================================================")
    print(f"Import completed in {total_dur:.1f}s")
    print(f"Total 1-minute samples: {total_ts:,}")
    print(f"Total Line Protocol points: {total_lines:,}")
    print(f"Totals across period:")
    print(f"  • Zonnestroom (Solar):       {grand_solar_kwh:,.1f} kWh")
    print(f"  • WP Ruimteverwarming (CV):   {grand_wp_cv_kwh:,.1f} kWh")
    print(f"  • WP Warm Tapwater (SWW):     {grand_wp_dhw_kwh:,.1f} kWh")
    print(f"  • Ongedefinieerd Verbruik:    {grand_unalloc_kwh:,.1f} kWh (gem. {grand_unalloc_kwh * 1000.0 / max(1, total_ts / 60.0):.0f} W continu)")
    print("================================================")


if __name__ == "__main__":
    main()
