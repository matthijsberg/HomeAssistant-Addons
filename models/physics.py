"""
Open HEMS Core Physics & Thermodynamic Engine
=============================================
Pure, stateless mathematical and physical functions for thermodynamic modeling.
Belongs to the Core Layer 0 contract layer (models/) — accessible by all layers (1-5)
without violating unidirectional architecture (Laag 1 -> 2 -> 3 -> 4/5).
"""

import math


def calculate_carnot_cop(
    outdoor_temp_c: float,
    flow_temp_c: float = 35.0,
    carnot_efficiency: float = 0.48,
    defrost_min_temp_c: float = -2.0,
    defrost_max_temp_c: float = 4.0,
    defrost_penalty: float = 0.85,
    min_cop: float = 2.2,
    max_cop: float = 6.8
) -> float:
    """
    Calculates temperature-dependent Carnot COP with empirical heat pump scaling.
    
    Formula:
        COP_Carnot = T_flow_k / max(8.0, T_flow_k - T_source_k)
        COP_empirical = carnot_efficiency * COP_Carnot
    
    Includes defrost cycle penalty near freezing (-2°C to +4°C).
    Bounds: [min_cop, max_cop] (default [2.2, 6.8]).
    """
    t_flow_k = flow_temp_c + 273.15
    t_source_k = outdoor_temp_c + 273.15
    delta_t = max(8.0, t_flow_k - t_source_k)
    theoretical_cop = t_flow_k / delta_t
    cop = carnot_efficiency * theoretical_cop

    # Defrost penalty near freezing (-2°C to +4°C)
    if defrost_min_temp_c <= outdoor_temp_c <= defrost_max_temp_c:
        cop *= defrost_penalty

    return round(max(min_cop, min(max_cop, cop)), 2)


def calculate_dhw_cop(target_temp_c: float) -> float:
    """
    Empirical COP for DHW tank heating:
    - Nominal 50°C cycle: COP 2.85
    - Boost 60°C cycle: COP 2.15 (high condensing temperature degradation)
    """
    return 2.85 if target_temp_c <= 52.0 else 2.15


def calculate_dhw_thermal_output_kw(
    target_temp_c: float,
    heat_pump_electric_kw: float = 1.8,
    solar_boost_electric_kw: float = 2.4,
) -> float:
    """
    Thermodynamically consistent thermal output (kW_th) delivered by heat pump for DHW heating.
    P_th = P_el * COP(target).
    - For target <= 52°C: 1.8 kW * 2.85 COP = 5.13 kW_th.
    - For target > 52°C:  2.4 kW * 2.15 COP = 5.16 kW_th.
    """
    p_el = solar_boost_electric_kw if target_temp_c > 52.0 else heat_pump_electric_kw
    cop = calculate_dhw_cop(target_temp_c)
    return round(p_el * cop, 3)
