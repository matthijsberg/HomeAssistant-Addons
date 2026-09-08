"""
Layer 2: Empirical Calibration & Physics Interfaces
===================================================
Defines the mathematical and thermodynamic contracts for physical
modeling, regression, parameter damping, and physical clamping.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple


class IModelCalibrator(ABC):
    """Abstract interface for physical model calibration."""

    @abstractmethod
    def calibrate_solar_matrix(
        self, historical_samples: List[Dict[str, Any]]
    ) -> Dict[int, float]:
        """Calculates hourly solar incidence angle matrix K(h) via OLS regression.

        K(h) scales global horizontal solar irradiance to actual array output.
        """
        pass

    @abstractmethod
    def calibrate_thermal_loss(
        self, historical_samples: List[Dict[str, Any]]
    ) -> Tuple[float, float]:
        """Calculates building insulation factor UA_base (kWh/°C·day) and standby loss.

        Filters out non-heating season samples (ambient temp >= 15°C) and
        applies defrost penalty coefficients.
        """
        pass

    @abstractmethod
    def evaluate_seasonal_cop(
        self, cumulative_thermal_kwh: float, cumulative_electrical_kwh: float
    ) -> float:
        """Calculates actual thermodynamic Coefficient of Performance."""
        pass


class ISmoothedClamping(ABC):
    """Abstract interface for parameter update smoothing and physical safety boundaries."""

    @abstractmethod
    def apply_smoothing(
        self, old_val: float, new_val: float, weight_old: float = 0.80, max_shift_pct: float = 0.20
    ) -> float:
        """Applies 80/20 EMA damping and clamps maximum step shift."""
        pass

    @abstractmethod
    def clamp_bounds(self, value: float, min_bound: float, max_bound: float) -> float:
        """Enforces hard physical bounds."""
        pass
