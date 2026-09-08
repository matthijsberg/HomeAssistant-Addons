"""
Layer 3: Optimization & Policy Engine Interfaces
================================================
Defines the mathematical contracts for evaluating policies and solving
the 24-hour / 96-quarter multi-vector economic dispatch schedule.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from models.canonical import (
    ScheduleSlot,
    PolicyType,
    ShiftableConsumerPolicy,
    ThermalBufferPolicy,
    BatteryArbitragePolicy,
)


class IPolicyEvaluator(ABC):
    """Abstract interface for policy evaluation."""

    @abstractmethod
    def evaluate_shiftable_consumer(
        self, policy: ShiftableConsumerPolicy, prices: List[float], solar_forecast: List[float]
    ) -> List[float]:
        """Calculates allocated power slot for non-storage appliances."""
        pass

    @abstractmethod
    def evaluate_thermal_buffer(
        self,
        policy: ThermalBufferPolicy,
        current_temp_c: float,
        ambient_temps: List[float],
        prices: List[float],
        solar_forecast: List[float],
    ) -> List[float]:
        """Calculates heat pump / boiler power dispatch waterfall."""
        pass

    @abstractmethod
    def evaluate_battery_arbitrage(
        self,
        policy: BatteryArbitragePolicy,
        current_soc_pct: float,
        prices: List[float],
        solar_forecast: List[float],
    ) -> Tuple[List[float], str]:
        """Calculates charge/discharge allocation using the economic deadband."""
        pass


class IDispatchOptimizer(ABC):
    """Abstract interface for central 24h multi-asset economic solver."""

    @abstractmethod
    def solve_daily_dispatch(
        self,
        horizon_slots: int,
        market_prices: List[float],
        solar_production: List[float],
        baseload: List[float],
        policies: List[Any],
        hardware_constraints: Dict[str, Any],
    ) -> List[ScheduleSlot]:
        """Solves the prioritized multi-vector dispatch waterfall."""
        pass
