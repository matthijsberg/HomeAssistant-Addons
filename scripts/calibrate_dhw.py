#!/usr/bin/env python3
"""
Open HEMS - Domestic Hot Water (DHW / SWW) Empirical COP Calibrator
Fits canonical DHW parameters: COP_50, k_T, k_out via OLS over historical runs.
"""

import os
import sys
import json
import math
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

from api.secrets_store import load_secrets
from models.physics import dhw_step, get_dhw_cop_params
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

    return {
        "n_runs": n_runs,
        "cop_50_fitted": round(cop_50_fit, 3),
        "k_t_fitted": round(k_t_fit, 4),
        "k_out_fitted": round(k_out_fit, 4),
        "r_squared": round(r_squared, 3),
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "current_defaults": {
            "cop_50": 2.85,
            "k_t": 0.07,
            "k_out": 0.05
        }
    }


if __name__ == "__main__":
    res = run_dhw_calibration()
    print(json.dumps(res, indent=2))
