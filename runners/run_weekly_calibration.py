#!/usr/bin/env python3
"""
Runner: Weekly Model Calibration & Auto-Tuning
==============================================
Fires weekly on Sunday at 08:00 AM via Cronjob 58c8edc27071.
Workflow:
  1. Compares 28-day PV actuals vs. radiation to calibrate solar_hourly_tilt_profile.
  2. Compares 60-day DHW heat pump runs to calibrate tank standby loss and tapwater demand.
  3. Solves 180-day OLS regression for building envelope (UA_base, c_wind) with summer freeze check.
  4. Applies Exponential Moving Average (80/20) smoothing and physical boundary clamping.
  5. Updates /config/heatpump_model_parameters.json.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, "/config/lib")

from layer2_calibration.calibrator import ModelCalibrationEngine
from heatpump_core import HeatPumpCore

def main():
    core = HeatPumpCore()
    calibrator = ModelCalibrationEngine()
    results = calibrator.run_full_calibration(core)
    print("\n[Weekly Calibration Complete]")
    print(f"  Building Envelope UA: {results['ua_base']} kW/K")
    print(f"  Wind Factor c_wind:  {results['c_wind']}")
    print(f"  Solar Afternoon Yield (16:00): {results['solar_hourly_tilt_profile'].get('16', 1.0)}x")
    print(f"  DHW Daily Demand:     {results['dhw_average_daily_kwh']} kWh (Standby: {results['dhw_standby_loss_kwh']} kWh)")

if __name__ == "__main__":
    main()
