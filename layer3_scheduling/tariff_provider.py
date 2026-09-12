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
    net_metering_active: bool = True
    feed_in_penalty_eur: float = 0.0  # e.g. terugleverkosten per kWh
    export_fixed_markup_eur: float = 0.0


class TariffProvider:
    """
    Computes all-in import prices and net export values from raw EPEX spot prices.
    """

    def __init__(self, config: Optional[TariffConfig] = None):
        self.config = config or TariffConfig()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TariffProvider":
        t_cfg = d.get("tariffs", d.get("tariff", {}))
        return cls(
            TariffConfig(
                supplier_markup_eur=float(t_cfg.get("supplier_markup_eur", 0.01210)),
                energy_tax_eur=float(t_cfg.get("energy_tax_eur", 0.11085)),
                vat_rate=float(t_cfg.get("vat_rate", 0.21)),
                fixed_monthly_eur=float(t_cfg.get("fixed_monthly_eur", 6.25)),
                net_metering_active=bool(t_cfg.get("net_metering_active", True)),
                feed_in_penalty_eur=float(t_cfg.get("feed_in_penalty_eur", 0.0)),
                export_fixed_markup_eur=float(t_cfg.get("export_fixed_markup_eur", 0.0))
            )
        )

    def calculate_import_price(self, spot_eur_per_kwh: float) -> float:
        """
        Calculates the gross consumer price including markup, energy tax, and VAT.
        Formula: (spot + markup + tax) * (1 + vat)
        """
        base = spot_eur_per_kwh + self.config.supplier_markup_eur + self.config.energy_tax_eur
        total = base * (1.0 + self.config.vat_rate)
        return round(total, 5)

    def calculate_export_value(self, spot_eur_per_kwh: float) -> float:
        """
        Calculates the net economic value of exporting 1 kWh of solar PV to the grid.
        When net metering is active, 1 kWh export offsets 1 kWh import (full retail value).
        When net metering is abolished, export earns wholesale spot minus fees.
        """
        if self.config.net_metering_active:
            # Full retail offset
            return self.calculate_import_price(spot_eur_per_kwh)
        else:
            # Pure wholesale return minus feed-in fees
            net = spot_eur_per_kwh - self.config.feed_in_penalty_eur + self.config.export_fixed_markup_eur
            return round(max(0.0, net), 5)
