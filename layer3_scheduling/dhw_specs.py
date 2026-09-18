"""
Layer 3: DHW Boiler & Heat Pump Specifications
===============================================
Encapsulates all physical properties of the domestic hot water (DHW) tank,
heat capacity equations (C_vat = V * c_w / 3600), standby loss ratings,
and compressor electrical/thermal operating capabilities.

Guarantees full configurability: replacing the tank or heat pump model
only requires updating config.json/heatpump_config.json without touching logic.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from models.physics import calculate_dhw_cop, calculate_dhw_thermal_output_kw


@dataclass
class DhwTankSpec:
    """
    Physical and operational specifications for a DHW storage tank and heat pump.
    """
    volume_liters: float = 350.0
    specific_heat_water: float = 4.184        # kJ / (kg * K)
    standby_loss_50_kw: float = 0.0589         # kW standby heat loss at 50°C
    standby_loss_60_kw: float = 0.0850         # kW standby heat loss at 60°C
    heat_pump_electric_kw: float = 1.8         # Nominal compressor electrical power (50°C run)
    solar_boost_electric_kw: float = 2.4       # Boost compressor electrical power (60°C run)
    thermal_output_kw: float = 6.5             # Nominal thermal heat output (kW_th)
    comfort_min_temp_c: float = 40.0           # Minimum acceptable shower temperature
    target_setpoint_c: float = 50.0            # Nominal comfort target setpoint
    boost_setpoint_c: float = 60.0             # Solar/economic buffer setpoint

    @property
    def thermal_capacity_kwh_per_k(self) -> float:
        """
        Thermodynamic heat capacity of the water mass in kWh_th per Kelvin:
        C_tank = (Volume_liters * rho * c_w) / 3600  (with rho = 1.0 kg/L)
        For 350L: (350 * 4.184) / 3600 = 0.40678 kWh/K
        """
        return round((self.volume_liters * self.specific_heat_water) / 3600.0, 4)

    def get_cop(self, target_temp_c: float) -> float:
        """Returns empirical COP for given target temperature."""
        return calculate_dhw_cop(target_temp_c)

    def get_electric_power_kw(self, target_temp_c: float) -> float:
        """Returns compressor electrical draw (kW) for target temperature."""
        return self.solar_boost_electric_kw if target_temp_c > 52.0 else self.heat_pump_electric_kw

    def get_thermal_output_kw(self, target_temp_c: float) -> float:
        """
        Thermodynamically consistent thermal output (kW_th): P_th = P_el * COP(target).
        For 50°C: 1.8 kW * 2.85 COP = 5.13 kW_th.
        For 60°C: 2.4 kW * 2.15 COP = 5.16 kW_th.
        """
        return calculate_dhw_thermal_output_kw(
            target_temp_c,
            heat_pump_electric_kw=self.heat_pump_electric_kw,
            solar_boost_electric_kw=self.solar_boost_electric_kw,
        )

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]] = None) -> "DhwTankSpec":
        """
        Builds a DhwTankSpec directly from the loaded system configuration dictionary.
        """
        if not cfg:
            return cls()

        b_cfg = cfg.get("dhw_boiler", {})
        vol = float(b_cfg.get("tank_volume_liters", 350.0))
        sh = float(b_cfg.get("specific_heat_water", 4.184))
        s50 = float(b_cfg.get("standby_loss_50_kw", 0.0589))
        s60 = float(b_cfg.get("standby_loss_60_kw", 0.0850))
        p_nom = float(b_cfg.get("compressor_power_kw", 1.8))
        p_boost = float(b_cfg.get("solar_boost_power_kw", 2.4))
        th_cap = float(b_cfg.get("thermal_output_kw", 6.5))
        t_comf = float(b_cfg.get("min_comfort_temp_c", 40.0))
        t_set = float(b_cfg.get("fallback_setpoint_temp", 50.0))
        t_boost = float(b_cfg.get("boost_setpoint_temp", 60.0))

        return cls(
            volume_liters=vol,
            specific_heat_water=sh,
            standby_loss_50_kw=s50,
            standby_loss_60_kw=s60,
            heat_pump_electric_kw=p_nom,
            solar_boost_electric_kw=p_boost,
            thermal_output_kw=th_cap,
            comfort_min_temp_c=t_comf,
            target_setpoint_c=t_set,
            boost_setpoint_c=t_boost
        )
