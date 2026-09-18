"""
Layer 3: Dynamic Tariff & Feed-In Provider
==========================================
Encapsulates all formulas for gross electricity import prices and net export
compensation according to Dutch dynamic energy contract rules (e.g. Powerpeers).
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from pathlib import Path
import json


@dataclass
class TariffConfig:
    supplier_markup_eur: float = 0.01210
    energy_tax_eur: float = 0.11085
    vat_rate: float = 0.21
    fixed_monthly_eur: float = 6.25
    feed_in_penalty_eur: float = 0.00605  # Terugleververgoeding aftrek / kosten
    export_fixed_markup_eur: float = 0.0


class TariffProvider:
    """
    Computes all-in import prices and net export values from raw EPEX spot prices.
    """

    def __init__(self, config: Optional[TariffConfig] = None):
        self.config = config or TariffConfig()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TariffProvider":
        # Support both dynamic_tariffs, tariffs, and tariff
        t_cfg = d.get("dynamic_tariffs", d.get("tariffs", d.get("tariff", {})))
        return cls(
            TariffConfig(
                supplier_markup_eur=float(t_cfg.get("fallback_markup_import", t_cfg.get("supplier_markup_eur", 0.01210))),
                energy_tax_eur=float(t_cfg.get("fallback_tax_electricity", t_cfg.get("energy_tax_eur", 0.11085))),
                vat_rate=float(t_cfg.get("vat_rate", 0.21)),
                fixed_monthly_eur=float(t_cfg.get("fallback_fixed_monthly_fee", t_cfg.get("fixed_monthly_eur", 6.25))),
                # feed_in_penalty_eur is currently not a configured field in heatpump_config.json;
                # falls back to statutory default €0.00605/kWh (Powerpeers €0.005 ex BTW -> €0.00605 incl BTW)
                feed_in_penalty_eur=float(t_cfg.get("fallback_feed_in_penalty", t_cfg.get("feed_in_penalty_eur", 0.00605))),
                export_fixed_markup_eur=float(t_cfg.get("fallback_markup_export", t_cfg.get("export_fixed_markup_eur", 0.0)))
            )
        )

    def calculate_spot_from_import(self, price_all_in: float) -> float:
        """
        Derives raw EPEX spot price from an all-in consumer import price.
        Formula: (price_all_in / (1 + vat)) - markup - energy_tax
        """
        vat_factor = 1.0 + self.config.vat_rate
        spot = (price_all_in / vat_factor) - self.config.supplier_markup_eur - self.config.energy_tax_eur
        return max(0.0, spot)

    def calculate_export_value_from_import(self, price_all_in: float) -> float:
        """
        Calculates the net avoided feed-in price (opportunity cost of self-consuming solar)
        directly from the current slot's all-in price.
        Formula: max(0, spot - feed_in_penalty + export_markup)
        """
        spot = self.calculate_spot_from_import(price_all_in)
        net_export = spot - self.config.feed_in_penalty_eur + self.config.export_fixed_markup_eur
        return max(0.0, net_export)

    def calculate_import_price(self, spot_eur_per_kwh: float) -> float:
        """
        Calculates the gross consumer price including markup, energy tax, and VAT.
        Formula: (spot + markup + tax) * (1 + vat)
        """
        base = spot_eur_per_kwh + self.config.supplier_markup_eur + self.config.energy_tax_eur
        total = base * (1.0 + self.config.vat_rate)
        return round(total, 5)
