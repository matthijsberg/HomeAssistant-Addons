#!/usr/bin/env python3
"""
Open HEMS - Domestic Hot Water (DHW / SWW) Empirical COP Calibrator
Fits canonical DHW parameters: COP_50, k_T, k_out via OLS over historical runs.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Any
import numpy as np

from api.secrets_store import load_secrets
from layer2_calibration.dhw_thermal_model import DhwThermalModel


def query_influx(url: str, db: str, user: str, pwd: str, query: str) -> dict:
    params = {"u": user, "p": pwd, "db": db, "q": query, "epoch": "s"}
    full_url = f"{url.rstrip('/')}/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def run_dhw_calibration() -> Dict[str, Any]:
    sec = load_secrets()
    pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")
    influx_url = "http://a0d7b954-influxdb:8086"
    db_name = "openhems"

    # 1. Fetch DHW heat pump telemetry (power_w and time)
    q_hp = "SELECT power_w FROM energy_telemetry WHERE device_id = 'daikin_heat_pump' AND mode = 'dhw' ORDER BY time ASC"
    res_hp = query_influx(influx_url, db_name, "openhems", pwd, q_hp)
    series_hp = res_hp["results"][0].get("series", [])
    if not series_hp:
        return {"error": "Geen DHW data gevonden in InfluxDB"}
    hp_values = series_hp[0]["values"]

    # 2. Fetch tank temperatures
    q_tank = "SELECT temperature_c FROM energy_telemetry WHERE device_id = 'dhw_tank' ORDER BY time ASC"
    res_tank = query_influx(influx_url, db_name, "openhems", pwd, q_tank)
    tank_values = res_tank["results"][0]["series"][0]["values"]
    tank_map = {int(row[0]): float(row[1]) for row in tank_values if row[1] is not None}
    tank_sorted_ts = sorted(tank_map.keys())

    # 3. Fetch outdoor weather temperatures
    q_out = "SELECT temperature_c FROM energy_telemetry WHERE device_id = 'outdoor_weather' ORDER BY time ASC"
    res_out = query_influx(influx_url, db_name, "openhems", pwd, q_out)
    out_values = res_out["results"][0]["series"][0]["values"]
    out_map = {int(row[0]): float(row[1]) for row in out_values if row[1] is not None}
    out_sorted_ts = sorted(out_map.keys())

    # 4. Group consecutive DHW telemetry into distinct heating runs
    runs_raw = []
    curr_run = []
    for ts, p in hp_values:
        if not curr_run:
            curr_run.append((int(ts), float(p)))
        else:
            if int(ts) - curr_run[-1][0] > 600:  # Gap > 10 min indicates separate run
                runs_raw.append(curr_run)
                curr_run = [(int(ts), float(p))]
            else:
                curr_run.append((int(ts), float(p)))
    if curr_run:
        runs_raw.append(curr_run)

    # 5. Process each run through the First Law thermodynamic balance
    c_tank = 0.407       # kWh / K (350L)
    ua_w_per_k = 2.5     # W / K
    t_amb = 18.0         # °C indoor ambient
    dhw_model = DhwThermalModel()

    dataset = []
    for run in runs_raw:
        t_start_ts = run[0][0]
        t_end_ts = run[-1][0]
        duration_s = t_end_ts - t_start_ts
        duration_h = duration_s / 3600.0

        # Discard spurious spikes (< 12 min) or very short micro-cycles
        if duration_s < 720:
            continue

        # Electrical energy via trapezoidal integration
        e_el_kwh = sum(
            ((run[i][1] + run[i+1][1]) / 2.0) * ((run[i+1][0] - run[i][0]) / 3600.0) / 1000.0
            for i in range(len(run) - 1)
        )
        if e_el_kwh < 0.4:
            continue

        # Tank temperature before and after
        # Find nearest tank temp within 180s of start and end
        t_start_c = None
        for dt_probe in range(0, 300, 30):
            if (t_start_ts - dt_probe) in tank_map:
                t_start_c = tank_map[t_start_ts - dt_probe]
                break
            if (t_start_ts + dt_probe) in tank_map:
                t_start_c = tank_map[t_start_ts + dt_probe]
                break

        t_end_c = None
        for dt_probe in range(0, 300, 30):
            if (t_end_ts + dt_probe) in tank_map:
                t_end_c = tank_map[t_end_ts + dt_probe]
                break
            if (t_end_ts - dt_probe) in tank_map:
                t_end_c = tank_map[t_end_ts - dt_probe]
                break

        if t_start_c is None or t_end_c is None:
            continue

        raw_delta_t = t_end_c - t_start_c
        if raw_delta_t <= 0.5:  # Require positive heating
            continue

        # Outdoor temperature during run
        out_temps = [
            out_map[ts] for ts in out_sorted_ts
            if t_start_ts - 300 <= ts <= t_end_ts + 300
        ]
        t_outdoor_avg = float(np.mean(out_temps)) if out_temps else 10.0

        t_tank_avg = (t_start_c + t_end_c) / 2.0

        # Physical corrections:
        # Standby loss: Q_sb = UA * (T_tank_avg - T_amb) * dt_h / 1000
        q_standby_kwh = (ua_w_per_k * max(0.0, t_tank_avg - t_amb) * duration_h) / 1000.0

        # Tap water drawn off during run (from learned 7x96 profile)
        run_dt = datetime.fromtimestamp(t_start_ts, tz=timezone.utc)
        dow = run_dt.weekday()
        q_idx = run_dt.hour * 4 + run_dt.minute // 15
        q_tap_slot = dhw_model.get_learned_tap_kwh_th(dow, q_idx)
        q_tap_run = q_tap_slot * (duration_h / 0.25)

        # Corrected delta_T: Q_th_delivered = C * Delta_T + Q_standby + Q_tap
        # Delta_T_gecorrigeerd = Delta_T_raw + (Q_standby + Q_tap) / C
        q_thermal_delivered = (c_tank * raw_delta_t) + q_standby_kwh + q_tap_run
        cop_measured = q_thermal_delivered / e_el_kwh

        # Filter unphysical measurement outliers (e.g. sensor disconnects)
        if 1.2 <= cop_measured <= 4.2:
            dataset.append({
                "t_start_ts": t_start_ts,
                "duration_min": duration_s / 60.0,
                "t_start_c": t_start_c,
                "t_end_c": t_end_c,
                "t_tank_avg": t_tank_avg,
                "t_outdoor_avg": t_outdoor_avg,
                "e_el_kwh": e_el_kwh,
                "q_th_kwh": q_thermal_delivered,
                "cop_measured": cop_measured,
            })

    n_runs = len(dataset)
    if n_runs < 10:
        return {"error": f"Te weinig valide runs ({n_runs})"}

    # 6. Ordinary Least Squares (OLS) Multivariable Regression:
    # Model: COP = COP_50 - k_T * (T_tank - 50.0) + k_out * (T_out - 10.0)
    # y = beta_0 + beta_1 * x1 + beta_2 * x2
    # with x1 = -(T_tank - 50.0), x2 = (T_out - 10.0)
    Y = np.array([d["cop_measured"] for d in dataset])
    X = np.column_stack([
        np.ones(n_runs),
        np.array([-(d["t_tank_avg"] - 50.0) for d in dataset]),
        np.array([(d["t_outdoor_avg"] - 10.0) for d in dataset])
    ])

    # Solve Beta = (X^T X)^-1 X^T Y
    beta, residuals, rank, s = np.linalg.lstsq(X, Y, rcond=None)
    cop_50_fit = float(beta[0])
    k_t_fit = float(beta[1])
    k_out_fit = float(beta[2])

    # Predictions & error metrics
    Y_pred = X @ beta
    res_vec = Y - Y_pred
    mae = float(np.mean(np.abs(res_vec)))
    rmse = float(np.sqrt(np.mean(res_vec ** 2)))
    ss_tot = float(np.sum((Y - np.mean(Y)) ** 2))
    ss_res = float(np.sum(res_vec ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    durations = [d["duration_min"] for d in dataset]
    powers = [(d["e_el_kwh"] / (d["duration_min"] / 60.0)) for d in dataset]
    rates = [((d["t_end_c"] - d["t_start_c"]) / (d["duration_min"] / 60.0)) for d in dataset]
    th_powers = [(d["q_th_kwh"] / (d["duration_min"] / 60.0)) for d in dataset]

    return {
        "n_runs": n_runs,
        "cop_50_fitted": round(cop_50_fit, 3),
        "k_t_fitted": round(k_t_fit, 4),
        "k_out_fitted": round(k_out_fit, 4),
        "r_squared": round(r_squared, 3),
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "mean_duration_min": round(float(np.mean(durations)), 1),
        "median_duration_min": round(float(np.median(durations)), 1),
        "mean_power_kw": round(float(np.mean(powers)), 2),
        "median_power_kw": round(float(np.median(powers)), 2),
        "mean_warming_rate_c_per_h": round(float(np.mean(rates)), 1),
        "median_warming_rate_c_per_h": round(float(np.median(rates)), 1),
        "mean_thermal_output_kw": round(float(np.mean(th_powers)), 2),
        "current_defaults": {
            "cop_50": 2.0,
            "k_t": 0.07,
            "k_out": 0.05
        }
    }


def run_dhw_power_calibration(dry_run: bool = True, write: bool = False, days: int = 365) -> Dict[str, Any]:
    """
    Deel B: Validates and fits the dynamic DHW electrical power model:
        P_el = p_nom_50 + k_t_tank * (T_tank - 50) - k_out * (T_out - 10)
    using stationary 5-minute intervals across InfluxDB energy_telemetry.
    Enforces strict statistical acceptance criteria before allowing parameters to be written.
    """
    import shutil
    import scipy.stats as stats
    from api.secrets_store import PARAMS_FILE, load_json, save_json

    sec = load_secrets()
    pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")
    influx_url = "http://a0d7b954-influxdb:8086"
    db_name = "openhems"

    q = f"""
    SELECT mean(power_w) AS power_w FROM energy_telemetry WHERE device_id = 'daikin_heat_pump' AND mode = 'dhw' AND time >= now() - {days}d GROUP BY time(5m) fill(none);
    SELECT mean(temperature_c) AS tank_temp FROM energy_telemetry WHERE device_id = 'dhw_tank' AND time >= now() - {days}d GROUP BY time(5m) fill(none);
    SELECT mean(temperature_c) AS out_temp FROM energy_telemetry WHERE device_id = 'outdoor_weather' AND time >= now() - {days}d GROUP BY time(5m) fill(none);
    """
    data = query_influx(influx_url, db_name, "openhems", pwd, q)

    p_dict = {row[0]: row[1] for row in data["results"][0]["series"][0]["values"] if row[1] is not None}
    t_dict = {row[0]: row[1] for row in data["results"][1]["series"][0]["values"] if row[1] is not None}
    out_dict = {row[0]: row[1] for row in data["results"][2]["series"][0]["values"] if row[1] is not None}

    # 1. Filter stationary intervals (active neighbor on both sides + power > 800W)
    usable_p = []
    usable_tank = []
    usable_out = []

    ts_sorted = sorted(p_dict.keys())
    for ts in ts_sorted:
        p_val = p_dict.get(ts)
        p_prev = p_dict.get(ts - 300)
        p_next = p_dict.get(ts + 300)

        if p_val is None or p_val < 800:
            continue
        if p_prev is None or p_prev < 800:
            continue
        if p_next is None or p_next < 800:
            continue

        t_tank = t_dict.get(ts)
        t_out = out_dict.get(ts)
        if t_tank is None or t_out is None:
            continue
        if not (30.0 <= t_tank <= 65.0):
            continue
        if not (-20.0 <= t_out <= 45.0):
            continue

        usable_p.append(p_val)
        usable_tank.append(t_tank)
        usable_out.append(t_out)

    N = len(usable_p)
    p_arr = np.array(usable_p)
    tank_arr = np.array(usable_tank)
    out_arr = np.array(usable_out)

    # 2. Evaluate Acceptance Criteria
    out_p05 = float(np.percentile(out_arr, 5)) if N > 0 else 0.0
    out_p95 = float(np.percentile(out_arr, 95)) if N > 0 else 0.0
    out_spread = out_p95 - out_p05

    q20 = float(np.percentile(out_arr, 20)) if N > 0 else 0.0
    q80 = float(np.percentile(out_arr, 80)) if N > 0 else 0.0
    n_cold = int(np.sum(out_arr <= q20)) if N > 0 else 0
    n_warm = int(np.sum(out_arr >= q80)) if N > 0 else 0

    crit_spread_ok = (out_spread >= 15.0)
    crit_count_ok = (N >= 200)
    crit_quintiles_ok = (n_cold >= 30 and n_warm >= 30)

    # 3. Simultaneous Multiple OLS Regression
    X = np.column_stack([np.ones(N), tank_arr - 50.0, -(out_arr - 10.0)])
    Y = p_arr / 1000.0  # Convert to kW
    beta = np.linalg.lstsq(X, Y, rcond=None)[0]
    residuals = Y - X @ beta
    dof = N - X.shape[1]
    s2 = np.sum(residuals**2) / dof
    var_beta = s2 * np.linalg.inv(X.T @ X)
    se_beta = np.sqrt(np.diag(var_beta))

    t_stat = beta / se_beta
    t_crit = stats.t.ppf(0.975, dof)
    ci_lower = beta - t_crit * se_beta
    ci_upper = beta + t_crit * se_beta
    p_values = 2 * (1 - stats.t.cdf(np.abs(t_stat), dof))

    # Significance test: 95% CI of k_out must not contain 0
    crit_signif_ok = not (ci_lower[2] <= 0.0 <= ci_upper[2])
    all_criteria_passed = crit_spread_ok and crit_count_ok and crit_quintiles_ok and crit_signif_ok

    report = {
        "n_stationary_intervals": N,
        "outdoor_temp_spread_k": round(out_spread, 1),
        "outdoor_p05_c": round(out_p05, 1),
        "outdoor_p95_c": round(out_p95, 1),
        "coldest_quintile_count": n_cold,
        "warmest_quintile_count": n_warm,
        "criteria": {
            "spread_ge_15k": crit_spread_ok,
            "samples_ge_200": crit_count_ok,
            "quintiles_ge_30": crit_quintiles_ok,
            "k_out_significant_95pct": crit_signif_ok,
            "all_passed": all_criteria_passed
        },
        "estimates": {
            "p_nom_50_kw": {
                "estimate": round(float(beta[0]), 3),
                "se": round(float(se_beta[0]), 4),
                "ci_95": [round(float(ci_lower[0]), 3), round(float(ci_upper[0]), 3)],
                "p_value": float(p_values[0])
            },
            "k_t_tank_kw_per_k": {
                "estimate": round(float(beta[1]), 4),
                "se": round(float(se_beta[1]), 4),
                "ci_95": [round(float(ci_lower[1]), 4), round(float(ci_upper[1]), 4)],
                "p_value": float(p_values[1])
            },
            "k_out_kw_per_k": {
                "estimate": round(float(beta[2]), 4),
                "se": round(float(se_beta[2]), 4),
                "ci_95": [round(float(ci_lower[2]), 4), round(float(ci_upper[2]), 4)],
                "p_value": float(p_values[2])
            }
        }
    }

    if write and not dry_run:
        if not all_criteria_passed:
            print("[ERROR] Weigeren parameters weg te schrijven: niet alle statistische criteria zijn gehaald!")
            report["write_status"] = "refused_criteria_failed"
        else:
            # Create backup of PARAMS_FILE
            backup_path = PARAMS_FILE.with_suffix(".json.bak")
            shutil.copyfile(PARAMS_FILE, backup_path)
            cur_cfg = load_json(PARAMS_FILE)
            cur_cfg.setdefault("dhw_power", {})["p_nom_50"] = round(float(beta[0]), 2)
            cur_cfg["dhw_power"]["k_t_tank"] = round(float(beta[1]), 3)
            cur_cfg["dhw_power"]["k_out"] = round(float(beta[2]), 3)
            cur_cfg["dhw_power"]["t_ref_tank_c"] = 50.0
            cur_cfg["dhw_power"]["t_ref_out_c"] = 10.0
            cur_cfg["dhw_power"]["p_min_kw"] = 1.6
            cur_cfg["dhw_power"]["p_max_kw"] = 3.5
            save_json(PARAMS_FILE, cur_cfg)
            print(f"✓ Gekalibreerde parameters opgeslagen naar {PARAMS_FILE} (backup: {backup_path})")
            report["write_status"] = "written"
    else:
        report["write_status"] = "dry_run"

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DHW Calibrator")
    parser.add_argument("--mode", choices=["cop", "power", "all"], default="all")
    parser.add_argument("--write", action="store_true", help="Write fitted parameters to file")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Display only")
    args = parser.parse_args()

    results = {}
    if args.mode in ["cop", "all"]:
        print("=== DHW COP Empirical Calibration ===")
        results["cop"] = run_dhw_calibration()
    if args.mode in ["power", "all"]:
        print("=== DHW Electrical Power Model Calibration ===")
        is_dry = not args.write
        results["power"] = run_dhw_power_calibration(dry_run=is_dry, write=args.write)

    print(json.dumps(results, indent=2))
