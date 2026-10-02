"""
Layer 5: Analytics & Reporting Implementation
=============================================
Calculates financial savings, solar self-consumption, seasonal heat pump COP,
and forecast-vs-actual variance auditing.
"""

from datetime import datetime
from typing import Dict, Any, List
import math
from layer5_analytics.interfaces import IAnalyticsEngine, IReportGenerator


class AnalyticsEngine(IAnalyticsEngine):
    """Core analytics calculator for financial and energetic performance."""

    def calculate_daily_savings(
        self, actual_consumption_kwh: float, scheduled_consumption_kwh: float, price_curve: List[float]
    ) -> Dict[str, float]:
        """Calculates cost difference between flat average price and optimized dispatch."""
        if not price_curve:
            return {"unoptimized_cost_eur": 0.0, "optimized_cost_eur": 0.0, "savings_eur": 0.0}

        avg_price = sum(price_curve) / len(price_curve)
        unoptimized_cost = round(actual_consumption_kwh * avg_price, 3)
        # Optimized cost assumes load was shifted into cheapest 25th percentile
        sorted_prices = sorted(price_curve)
        cheapest_avg = sum(sorted_prices[:max(1, len(sorted_prices)//4)]) / max(1, len(sorted_prices)//4)
        optimized_cost = round(scheduled_consumption_kwh * cheapest_avg, 3)
        savings = round(max(0.0, unoptimized_cost - optimized_cost), 3)

        return {
            "unoptimized_cost_eur": unoptimized_cost,
            "optimized_cost_eur": optimized_cost,
            "savings_eur": savings,
            "savings_pct": round((savings / unoptimized_cost * 100.0), 1) if unoptimized_cost > 0 else 0.0
        }

    def evaluate_forecast_accuracy(
        self, forecasted_series: List[float], actual_series: List[float]
    ) -> Dict[str, float]:
        """Computes MAE and RMSE between predictions and verified production readings."""
        n = min(len(forecasted_series), len(actual_series))
        if n == 0:
            return {"mae": 0.0, "rmse": 0.0, "accuracy_pct": 100.0}

        abs_errors = [abs(f - a) for f, a in zip(forecasted_series[:n], actual_series[:n])]
        sq_errors = [(f - a) ** 2 for f, a in zip(forecasted_series[:n], actual_series[:n])]

        mae = round(sum(abs_errors) / n, 3)
        rmse = round(math.sqrt(sum(sq_errors) / n), 3)

        # Baseline accuracy
        mean_actual = sum(actual_series[:n]) / n if n > 0 else 1.0
        acc_pct = round(max(0.0, min(100.0, 100.0 - (mae / (mean_actual + 1e-6) * 100.0))), 1)

        return {"mae": mae, "rmse": rmse, "accuracy_pct": acc_pct}

    def calculate_self_consumption_ratio(
        self, solar_production_kwh: float, grid_export_kwh: float
    ) -> float:
        """Calculates percentage of solar electricity kept on-site."""
        if solar_production_kwh <= 0.0:
            return 0.0
        utilized = max(0.0, solar_production_kwh - grid_export_kwh)
        return round(min(100.0, (utilized / solar_production_kwh) * 100.0), 1)


class ReportGenerator(IReportGenerator):
    """Generates concise operational and audit reports."""

    def generate_daily_digest(self, date: datetime, metrics: Dict[str, Any]) -> str:
        date_str = date.strftime("%d-%m-%Y")
        lines = [
            f"📊 **Open HEMS Dagrapport — {date_str}**",
            f"• Verwachte Kosten: €{metrics.get('total_cost_eur', 0.0):.2f}",
            f"• Gerealiseerde Besparing: €{metrics.get('savings_eur', 0.0):.2f} ({metrics.get('savings_pct', 0.0)}%)",
            f"• Zonne-zelfconsumptie: {metrics.get('self_consumption_pct', 0.0)}%",
            f"• Warmtepomp Status: COP Tapwater {metrics.get('dhw_cop', 2.04)} · COP CV {metrics.get('space_cop', 4.80)}"
        ]
        return "\n".join(lines)

    def generate_weekly_performance_report(
        self, week_number: int, aggregated_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "week_number": week_number,
            "total_savings_eur": aggregated_metrics.get("total_savings_eur", 0.0),
            "avg_daily_solar_kwh": aggregated_metrics.get("avg_daily_solar_kwh", 0.0),
            "dhw_thermal_total_kwh": aggregated_metrics.get("dhw_thermal_total_kwh", 0.0),
            "space_heating_thermal_total_kwh": aggregated_metrics.get("space_heating_thermal_total_kwh", 0.0),
            "calibrated_ua_base": aggregated_metrics.get("calibrated_ua_base", 7.76)
        }
