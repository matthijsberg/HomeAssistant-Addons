"""
Open HEMS — DHW Financials & Electricity Cost Calculator
========================================================
Layer 3 Scheduling Submodule:
Pure calculation of electricity cost, solar self-consumption, and effective price per slot.
"""

from typing import Tuple, Optional
from layer3_scheduling.tariff_provider import TariffProvider


def calculate_slot_financials(
    solar_kw: float,
    unalloc_kw: float,
    el_demand_kw: float,
    price_all_in: float,
    step_hours: float = 0.25,
    tariff_provider: Optional[TariffProvider] = None
) -> Tuple[float, float, float, float]:
    """
    Pure function: Calculates electricity costs for a single quarter-hour slot.
    - Solar/battery self-consumption is valued at avoided feed-in price (derived via TariffProvider).
    - Grid import is valued at all-in consumer price (EPEX spot + energy tax + opslag + VAT).

    Returns: (cost_eur, self_kwh, grid_kwh, p_effective)
    """
    demand_kwh = max(0.0, el_demand_kw * step_hours)
    if demand_kwh <= 0.0:
        return 0.0, 0.0, 0.0, price_all_in

    surplus_kw = max(0.0, solar_kw - unalloc_kw)
    surplus_kwh = surplus_kw * step_hours

    self_kwh = min(demand_kwh, surplus_kwh)
    grid_kwh = max(0.0, demand_kwh - self_kwh)

    # Net feed-in tariff derived dynamically via TariffProvider
    tp = tariff_provider or TariffProvider()
    p_export = tp.calculate_export_value_from_import(price_all_in)
    cost_eur = (grid_kwh * price_all_in) + (self_kwh * p_export)
    p_effective = cost_eur / demand_kwh if demand_kwh > 0 else price_all_in

    return cost_eur, self_kwh, grid_kwh, p_effective


class DhwFinancials:
    """Class wrapper for backward compatibility with existing classmethod calls."""
    calculate_slot_financials = staticmethod(calculate_slot_financials)
