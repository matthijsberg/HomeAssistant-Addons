"""
Layer 5: Analytics & Reporting Interfaces
=========================================
Defines the contracts for performance tracking, financial savings calculations,
seasonal COP aggregation, and forecast-vs-actual telemetry auditing.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime


class IAnalyticsEngine(ABC):
    """Abstract interface for high-level energy and financial analytics."""

    @abstractmethod
    def calculate_daily_savings(
        self, actual_consumption_kwh: float, scheduled_consumption_kwh: float, price_curve: List[float]
    ) -> Dict[str, float]:
        """Calculates financial savings achieved by dynamic dispatch vs unoptimized baseload."""
        pass

    @abstractmethod
    def evaluate_forecast_accuracy(
        self, forecasted_series: List[float], actual_series: List[float]
    ) -> Dict[str, float]:
        """Calculates Mean Absolute Error (MAE) and Root Mean Square Error (RMSE)

        between Day-Ahead forecast and verified InfluxDB hardware readings.
        """
        pass

    @abstractmethod
    def calculate_self_consumption_ratio(
        self, solar_production_kwh: float, grid_export_kwh: float
    ) -> float:
        """Calculates the percentage of on-site solar energy utilized directly (0.0 to 100.0%)."""
        pass


class IReportGenerator(ABC):
    """Abstract interface for formatted operational reporting."""

    @abstractmethod
    def generate_daily_digest(self, date: datetime, metrics: Dict[str, Any]) -> str:
        """Generates a concise markdown/text summary of 24-hour performance."""
        pass

    @abstractmethod
    def generate_weekly_performance_report(
        self, week_number: int, aggregated_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generates a comprehensive weekly audit report with COP and cost breakdowns."""
        pass
