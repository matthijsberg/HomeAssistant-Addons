"""
Open HEMS: Model Validation & Telemetry Verification Engine
==========================================================
Layer 2 Physical Model Validation:
  - Compares ground-truth multi-vector telemetry (P1, Inepro, Daikin) against physical models
  - Computes Normalized MAE, Volumetric Energy Accuracy, and Unified Quality KPI scores
  - Enforces Single Source of Truth: uses CanonicalDispatchPlan for scheduled forward slots
"""
import math
import time
import urllib.parse
import urllib.request
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple

from models.canonical import CanonicalDispatchPlan


class ModelValidator:
    """Evaluates physical model predictions against historical InfluxDB telemetry."""

    @staticmethod
    def compute_kpis(actual_list: List[float], pred_list: List[float], interval_h: float = 0.25, peak_cap_kw: float = 5.0) -> Dict[str, Any]:
        """
        Computes standardized KPIs:
        1. Volumetric Energy Accuracy (50% weight): |E_act - E_pred| / max(E_act, E_pred)
        2. Normalized MAE (50% weight): (MAE in kW) / peak_cap_kw
        Quality Score = 0.5 * Acc_vol + 0.5 * Acc_shape
        """
        if not actual_list or not pred_list:
            return {"mae_w": 0, "accuracy_pct": 100.0, "total_actual_kwh": 0.0, "total_pred_kwh": 0.0, "delta_kwh": 0.0}
        n = len(actual_list)
        diffs = [abs(a - p) for a, p in zip(actual_list, pred_list)]
        mae_w = sum(diffs) / n * 1000.0
        tot_act = sum(actual_list) * interval_h
        tot_pred = sum(pred_list) * interval_h

        vol_denom = max(tot_act, tot_pred, 1.0)
        acc_vol = max(0.0, 1.0 - (abs(tot_act - tot_pred) / vol_denom))

        nmae = (mae_w / 1000.0) / max(1.0, peak_cap_kw)
        acc_shape = max(0.0, 1.0 - nmae)

        acc = round((0.5 * acc_vol + 0.5 * acc_shape) * 100.0, 1)
        return {
            "mae_w": int(round(mae_w)),
            "accuracy_pct": acc,
            "total_actual_kwh": round(tot_act, 2),
            "total_pred_kwh": round(tot_pred, 2),
            "delta_kwh": round(tot_act - tot_pred, 2)
        }
