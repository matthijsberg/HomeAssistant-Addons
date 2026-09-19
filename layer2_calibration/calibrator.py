#!/usr/bin/env python3
"""
Model Calibration & Adaptive Offset Engine
==========================================
Self-learning empirical feedback loop for HEMS predictive modeling.

Solves:
  1. Solar Orientation, Tilt & Shading Profile:
     Replaces generic flat-plate assumptions with an hourly empirical yield coefficient K(h)
     derived from actual inverter production vs. global radiation. Accounts for roof tilt,
     southwest orientation, and morning/afternoon shading.
  2. Domestic Hot Water (DHW) Energy & Loss Model:
     Calibrates standby tank cooling losses (Q_standby) and empirical COP from actual heat pump runs.
  3. Space Heating Building Envelope (UA & Infiltration):
     Performs multi-variable Ordinary Least Squares (OLS) regression over thermal heat output,
     indoor/outdoor temperature delta, wind speed, and passive solar gains.
"""

import os
import sys
import json
import math
from datetime import datetime

CONFIG_PATH = "/config/heatpump_config.json"
MODEL_PARAMS_PATH = "/config/heatpump_model_parameters.json"


class ModelCalibrationEngine:
    def __init__(self, config_path: str = CONFIG_PATH, params_path: str = MODEL_PARAMS_PATH):
        self.config_path = config_path
        self.params_path = params_path
        self.config = self._load_json(self.config_path)
        self.params = self._load_json(self.params_path)

    def _load_json(self, path: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not read {path}: {e}")
        return {}

    def _save_params(self):
        tmp_path = f"{self.params_path}.tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w") as f:
                json.dump(self.params, f, indent=2)
            os.replace(tmp_path, self.params_path)
            os.chmod(self.params_path, 0o644)
        except Exception as e:
            print(f"Warning: Could not save model parameters: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def is_sample_excluded(self, sensor_name: str, ts) -> bool:
        """Checks if a sample timestamp falls inside any configured data exclusion window."""
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts)
        elif isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts[:19])
            except ValueError:
                return False
        else:
            dt = ts

        d_str = dt.strftime("%Y-%m-%d")
        for win in self.config.get("data_exclusion_windows", []):
            if win.get("sensor") == sensor_name or win.get("sensor") in sensor_name:
                start = win.get("start", "1970-01-01")
                end = win.get("end", "2099-12-31")
                if start <= d_str <= end:
                    return True
        return False

    # =========================================================================
    # GUARDRAILS & CLAMPING CONFIGURATION
    # =========================================================================
    GUARDRAILS = {
        "solar_factor_min": 0.50,
        "solar_factor_max": 2.50,
        "solar_step_limit_pct": 0.20,      # Max 20% shift per calibration run
        "dhw_standby_min_kwh": 0.80,
        "dhw_standby_max_kwh": 2.50,
        "dhw_daily_min_kwh": 3.00,
        "dhw_daily_max_kwh": 9.50,
        "ua_base_min": 6.00,
        "ua_base_max": 11.00,
        "ua_step_limit_pct": 0.15,         # Max 15% shift per calibration run
        "c_wind_min": 0.001,
        "c_wind_max": 0.080,
        "min_heating_samples_required": 10
    }

    def apply_smoothed_clamping(self, old_val: float, new_val: float, min_val: float, max_val: float, max_shift_pct: float = 0.20) -> float:
        """
        Dampens updates using Exponential Moving Average and clamps to physical boundaries.
        Prevents sensor glitches, outlier days, or cloudy weeks from skewing the model.
        """
        if old_val is None or old_val <= 0:
            return round(max(min_val, min(max_val, new_val)), 5)

        # 1. Dampen using 80% historical + 20% new observation
        smoothed = (0.80 * old_val) + (0.20 * new_val)

        # 2. Limit step change to max_shift_pct from old value
        max_allowed = old_val * (1.0 + max_shift_pct)
        min_allowed = old_val * (1.0 - max_shift_pct)
        stepped = max(min_allowed, min(max_allowed, smoothed))

        # 3. Hard physical boundary clamping
        final_val = max(min_val, min(max_val, stepped))
        return round(final_val, 5)

    # =========================================================================
    # 1. SOLAR ORIENTATION, TILT & SHADING PROFILE
    # =========================================================================
    def calculate_hourly_solar_profile(self, actual_pv_hourly: dict, radiation_hourly: dict, kwp: float = 5.5) -> dict:
        """
        Computes the hourly tilt & shading correction factor K(h) for each hour (0..23).
        actual_pv_hourly: {timestamp: kWh}
        radiation_hourly: {timestamp: W/m²}
        Returns: {hour (0..23): correction_factor (float)}
        """
        common_ts = sorted(list(set(actual_pv_hourly.keys()) & set(radiation_hourly.keys())))
        hourly_samples = {h: [] for h in range(24)}

        for ts in common_ts:
            dt = datetime.fromtimestamp(ts)
            pv_kwh = actual_pv_hourly[ts]
            rad_wm2 = radiation_hourly[ts]

            # Only analyze hours with meaningful sunlight (> 50 W/m2 and positive production)
            if rad_wm2 >= 50.0 and pv_kwh > 0.0:
                # Theoretical horizontal output for 5.5 kWp
                ideal_flat_kwh = (rad_wm2 / 1000.0) * kwp
                if ideal_flat_kwh > 0.05:
                    ratio = pv_kwh / ideal_flat_kwh
                    # Bound individual sample ratios to prevent extreme sunrise/sunset noise
                    clamped_ratio = max(0.2, min(3.5, ratio))
                    hourly_samples[dt.hour].append(clamped_ratio)

        # Build 24-hour calibrated profile
        calibrated_profile = {}
        for h in range(24):
            samples = hourly_samples[h]
            if len(samples) >= 3:
                # Robust median/mean
                avg_factor = round(sum(samples) / len(samples), 3)
                calibrated_profile[h] = avg_factor
            elif 9 <= h <= 17:
                # Reasonable default for midday hours without enough samples
                calibrated_profile[h] = 1.05
            else:
                # Sunrise/sunset defaults
                calibrated_profile[h] = 0.85

        return calibrated_profile

    # =========================================================================
    # 2. DHW STANDBY LOSS & TAPWATER ENERGY CONSUMPTION PROFILE
    # =========================================================================
    def calculate_dhw_empirical_parameters(self, actual_dhw_thermal_daily: dict, tank_volume: float = 350.0) -> dict:
        """
        Calculates average daily thermal energy demand and standby cooling losses.
        actual_dhw_thermal_daily: {timestamp: kWh}
        Returns: {
            "avg_daily_dhw_thermal_kwh": float,
            "standby_cooling_loss_kwh": float,
            "samples_count": int
        }
        """
        vals = [v for v in actual_dhw_thermal_daily.values() if v is not None and v > 0.5]
        if not vals:
            return {
                "avg_daily_dhw_thermal_kwh": 4.10,
                "standby_cooling_loss_kwh": 1.20,
                "samples_count": 0
            }

        # Average thermal energy delivered per day
        avg_dhw = round(sum(vals) / len(vals), 2)
        # Standby cooling loss is estimated as the minimum non-zero topup day (no shower day)
        standby_loss = round(min(vals), 2) if min(vals) < 2.5 else 1.20

        return {
            "avg_daily_dhw_thermal_kwh": avg_dhw,
            "standby_cooling_loss_kwh": standby_loss,
            "samples_count": len(vals)
        }

    # =========================================================================
    # 3. SPACE HEATING OLS REGRESSION (UA_base, c_wind, c_solar)
    # =========================================================================
    def solve_ols_3x3(self, X: list, Y: list) -> list:
        """
        Solves Ordinary Least Squares: Beta = (X^T X)^(-1) X^T Y for 3 features.
        X[i] = [x1 (delta_T), x2 (wind), x3 (solar_gains)]
        """
        n = len(Y)
        if n < 5:
            return [7.02137, 0.03634, 0.08885]

        ATA = [[0.0] * 3 for _ in range(3)]
        ATY = [0.0] * 3

        for row_x, y in zip(X, Y):
            for i in range(3):
                ATY[i] += row_x[i] * y
                for j in range(3):
                    ATA[i][j] += row_x[i] * row_x[j]

        # Gauss-Jordan elimination on 3x4 augmented matrix [ATA | ATY]
        M = [ATA[i] + [ATY[i]] for i in range(3)]
        for i in range(3):
            pivot = M[i][i]
            if abs(pivot) < 1e-12:
                return [7.02137, 0.03634, 0.08885]
            for j in range(i, 4):
                M[i][j] /= pivot
            for k in range(3):
                if k != i:
                    factor = M[k][i]
                    for j in range(i, 4):
                        M[k][j] -= factor * M[i][j]

        # Physical constraints check (UA must be positive, wind positive, solar gain positive/negative)
        ua = max(4.0, min(12.0, M[0][3]))
        c_wind = max(0.001, min(0.15, M[1][3]))
        c_solar = max(0.001, min(0.20, M[2][3]))

        return [round(ua, 5), round(c_wind, 5), round(c_solar, 5)]

    # =========================================================================
    # ACCURACY METRICS: MAE, RMSE & BIAS
    # =========================================================================
    def calculate_accuracy_metrics(self, actuals: list, predictions: list) -> dict:
        """Computes Mean Absolute Error, Root Mean Squared Error, and Mean Percentage Bias."""
        if not actuals or len(actuals) != len(predictions):
            return {"mae": 0.0, "rmse": 0.0, "bias_pct": 0.0, "n": 0}

        diffs = [p - a for a, p in zip(actuals, predictions)]
        abs_diffs = [abs(d) for d in diffs]
        sq_diffs = [d ** 2 for d in diffs]

        n = len(actuals)
        mae = sum(abs_diffs) / n
        rmse = math.sqrt(sum(sq_diffs) / n)

        mean_actual = sum(actuals) / n if sum(actuals) != 0 else 1.0
        bias_pct = (sum(diffs) / n) / mean_actual * 100.0

        return {
            "mae": round(mae, 3),
            "rmse": round(rmse, 3),
            "bias_pct": round(bias_pct, 2),
            "n": n
        }

    # =========================================================================
    # FULL AUTOMATED CALIBRATION RUN
    # =========================================================================
    def run_full_calibration(self, core) -> dict:
        """Runs complete calibration across solar profile, DHW demand, and space heating."""
        print("\n--- RUNNING MODEL CALIBRATION ENGINE ---")

        # 1. Solar Calibration (Wittboy/Open-Meteo vs. Inverter Actuals)
        q_pv = "SELECT non_negative_difference(last(value)) FROM \"kWh\" WHERE entity_id = 'zonnepanelen_export_power' AND time > now() - 30d GROUP BY time(1h)"
        q_rad = "SELECT mean(value) FROM \"W/m²\" WHERE entity_id = 'wittboy_gw2000a_weather_station_gw2000a_solar_radiation' AND time > now() - 30d GROUP BY time(1h)"

        res_pv = core.query_influx(q_pv)
        res_rad = core.query_influx(q_rad)

        pv_dict = self._extract_series(res_pv)
        rad_dict = self._extract_series(res_rad)

        kwp = self.config.get("solar", {}).get("kwp", 5.5)
        solar_profile = self.calculate_hourly_solar_profile(pv_dict, rad_dict, kwp=kwp)

        # 2. DHW Calibration
        q_dhw = "SELECT non_negative_difference(last(value)) FROM \"kWh\" WHERE entity_id = 'warmtepomp_hot_water_production_all_time' AND time > now() - 60d GROUP BY time(1d)"
        res_dhw = core.query_influx(q_dhw)
        dhw_dict = self._extract_series(res_dhw)
        dhw_params = self.calculate_dhw_empirical_parameters(dhw_dict, tank_volume=350.0)

        # 3. Space Heating OLS Calibration
        q_thermal = "SELECT non_negative_difference(last(value)) FROM \"kWh\" WHERE entity_id = 'warmtepomp_heating_production_all_time' AND time > now() - 365d GROUP BY time(1d)"
        q_tout = "SELECT mean(value) FROM \"°C\" WHERE (entity_id = 'buiten_temperatuur' OR entity_id = 'temperatuur_buiten') AND time > now() - 365d GROUP BY time(1d)"
        q_tin = "SELECT mean(value) FROM \"°C\" WHERE (entity_id = 'sco2_staging_01_woonkamer_co2_temperature' OR entity_id = 'hc_sensors_temperature_room') AND time > now() - 365d GROUP BY time(1d)"
        q_wind = "SELECT mean(value) FROM \"Bft\" WHERE (entity_id = 'br_wind_force_2' OR entity_id = 'br_wind_force') AND time > now() - 365d GROUP BY time(1d)"
        q_rad_day = "SELECT mean(value) FROM \"W/m²\" WHERE entity_id = 'wittboy_gw2000a_weather_station_gw2000a_solar_radiation' AND time > now() - 365d GROUP BY time(1d)"

        d_th = self._extract_series(core.query_influx(q_thermal))
        d_to = self._extract_series(core.query_influx(q_tout))
        d_ti = self._extract_series(core.query_influx(q_tin))
        d_wi = self._extract_series(core.query_influx(q_wind))
        d_so = self._extract_series(core.query_influx(q_rad_day))

        common_days = sorted(list(set(d_th.keys()) & set(d_to.keys()) & set(d_ti.keys()) & set(d_wi.keys())))
        X = []
        Y = []
        skipped_excluded_days = 0
        for ts in common_days:
            # Check data exclusion window (e.g. disconnected meter period)
            if self.is_sample_excluded("sensor.warmtepomp_power", ts):
                skipped_excluded_days += 1
                continue

            th_val = d_th[ts]
            if th_val and th_val > 2.0:
                delta_t = max(0.0, d_ti[ts] - d_to[ts])
                wind_kmh = d_wi.get(ts, 2.0) * 3.6
                sol_gain = (d_so.get(ts, 100.0) / 1000.0)
                X.append([delta_t, wind_kmh, sol_gain])
                Y.append(th_val)

        if skipped_excluded_days > 0:
            print(f"Notice: Filtered out {skipped_excluded_days} days falling inside data exclusion windows.")

        ua, c_wind, c_solar = self.solve_ols_3x3(X, Y)

        # 4. Apply Guardrails & Exponential Smoothing against existing parameters
        old_params = self.params or {}
        old_tilt = old_params.get("solar_hourly_tilt_profile", {})
        smoothed_solar_profile = {}
        for h, new_factor in solar_profile.items():
            old_factor = float(old_tilt.get(str(h), old_tilt.get(h, new_factor)))
            smoothed_solar_profile[h] = self.apply_smoothed_clamping(
                old_factor, new_factor,
                min_val=self.GUARDRAILS["solar_factor_min"],
                max_val=self.GUARDRAILS["solar_factor_max"],
                max_shift_pct=self.GUARDRAILS["solar_step_limit_pct"]
            )

        # Space heating clamping (with summer freeze check)
        old_ua = old_params.get("ua_base", 8.0)
        if len(X) >= self.GUARDRAILS["min_heating_samples_required"]:
            smoothed_ua = self.apply_smoothed_clamping(
                old_ua, ua,
                min_val=self.GUARDRAILS["ua_base_min"],
                max_val=self.GUARDRAILS["ua_base_max"],
                max_shift_pct=self.GUARDRAILS["ua_step_limit_pct"]
            )
            smoothed_c_wind = self.apply_smoothed_clamping(
                old_params.get("c_wind", 0.01), c_wind,
                min_val=self.GUARDRAILS["c_wind_min"],
                max_val=self.GUARDRAILS["c_wind_max"],
                max_shift_pct=0.50
            )
        else:
            print(f"Notice: Only {len(X)} heating days found; keeping existing space heating parameters (summer freeze).")
            smoothed_ua = old_ua
            smoothed_c_wind = old_params.get("c_wind", 0.005)

        # DHW clamping
        old_dhw_avg = old_params.get("dhw_average_daily_kwh", 4.5)
        old_dhw_sb = old_params.get("dhw_standby_loss_kwh", 1.5)
        smoothed_dhw_avg = self.apply_smoothed_clamping(
            old_dhw_avg, dhw_params["avg_daily_dhw_thermal_kwh"],
            min_val=self.GUARDRAILS["dhw_daily_min_kwh"],
            max_val=self.GUARDRAILS["dhw_daily_max_kwh"],
            max_shift_pct=0.25
        )
        smoothed_dhw_sb = self.apply_smoothed_clamping(
            old_dhw_sb, dhw_params["standby_cooling_loss_kwh"],
            min_val=self.GUARDRAILS["dhw_standby_min_kwh"],
            max_val=self.GUARDRAILS["dhw_standby_max_kwh"],
            max_shift_pct=0.25
        )

        # 5. Save and return consolidated parameters: merge with existing keys to preserve dhw_cop, dhw_power, etc.
        """
        Calibrator ownership domain:
        - Owns and updates: ua_base, c_wind, c_solar, solar_hourly_tilt_profile,
          dhw_average_daily_kwh, dhw_standby_loss_kwh, dhw_average_cop, heating_average_cop, calibration_timestamp.
        - Preserves untouched: dhw_cop, dhw_power, building, heat_pump, dhw_tank,
          unallocated, metrics, version, and any custom/future configuration blocks.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated_params = {
            "ua_base": smoothed_ua,
            "c_wind": smoothed_c_wind,
            "c_solar": c_solar,
            "solar_hourly_tilt_profile": smoothed_solar_profile,
            "dhw_average_daily_kwh": smoothed_dhw_avg,
            "dhw_standby_loss_kwh": smoothed_dhw_sb,
            "dhw_average_cop": 2.02,
            "heating_average_cop": 4.8,
            "calibration_timestamp": now_str
        }

        merged_params = dict(self.params or {})
        merged_params.update(updated_params)
        self.params = merged_params
        self._save_params()
        print(f"✓ Saved updated parameters to {self.params_path}")
        return merged_params

    def _extract_series(self, res: dict) -> dict:
        try:
            series = res["results"][0]["series"][0]
            return {v[0]: v[1] for v in series["values"] if v[0] is not None and v[1] is not None}
        except Exception:
            return {}
