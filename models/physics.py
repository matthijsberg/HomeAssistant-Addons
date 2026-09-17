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
