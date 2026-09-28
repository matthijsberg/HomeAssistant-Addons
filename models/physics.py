"""
Open HEMS Core Physics & Thermodynamic Engine
=============================================
Pure, stateless mathematical and physical functions for thermodynamic modeling.
Belongs to the Core Layer 0 contract layer (models/) — accessible by all layers (1-5)
without violating unidirectional architecture (Laag 1 -> 2 -> 3 -> 4/5).
"""

from typing import Optional, Any, Tuple


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


_LOGGED_COP_FALLBACK = False
_LOGGED_POWER_FALLBACK = False


def get_dhw_cop_params(params: Optional[dict] = None) -> tuple:
    """Extracts DHW COP parameters from params dict with canonical defaults."""
    global _LOGGED_COP_FALLBACK
    p = params or {}
    has_custom = False
    if "dhw_cop" in p and isinstance(p["dhw_cop"], dict):
        cop_cfg = p["dhw_cop"]
        has_custom = True
    elif "dhw_tank" in p and isinstance(p["dhw_tank"], dict) and "cop_50" in p["dhw_tank"]:
        cop_cfg = p["dhw_tank"]
        has_custom = True
    else:
        cop_cfg = {}
        if not _LOGGED_COP_FALLBACK:
            print("[WARN] get_dhw_cop_params: geen 'dhw_cop' blok in modelparameters; teruggevallen op standaarden (cop_50=2.0).")
            _LOGGED_COP_FALLBACK = True

    cop_50 = float(cop_cfg.get("cop_50", cop_cfg.get("COP_50", 2.0)))
    k_t = float(cop_cfg.get("k_t", cop_cfg.get("k_T", 0.07)))
    k_out = float(cop_cfg.get("k_out", cop_cfg.get("k_OUT", 0.05)))
    cop_min = float(cop_cfg.get("cop_min", cop_cfg.get("COP_min", 1.4)))
    cop_max = float(cop_cfg.get("cop_max", cop_cfg.get("COP_max", 3.2)))
    return cop_50, k_t, k_out, cop_min, cop_max


def load_dhw_cop_params(path: Optional[Any] = None) -> dict:
    """Loads dhw_cop configuration dictionary from model parameter file or canonical defaults."""
    import json
    from pathlib import Path
    default_paths = [
        Path(path) if path else None,
        Path("/config/heatpump_model_parameters.json"),
        Path(__file__).parent.parent / "data" / "heatpump_model_parameters.json"
    ]
    for p in default_paths:
        if p and p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "dhw_cop" in data and isinstance(data["dhw_cop"], dict):
                        return data["dhw_cop"]
                    elif "dhw_tank" in data and isinstance(data["dhw_tank"], dict):
                        return data["dhw_tank"]
            except Exception:
                pass
    return {
        "cop_50": 2.0,
        "k_t": 0.07,
        "k_out": 0.05,
        "cop_min": 1.4,
        "cop_max": 3.2
    }


def get_dhw_power_params(params: Optional[dict] = None) -> Tuple[float, float, float, float, float]:
    """
    Returns (p_50, k_t_tank, k_out, p_min, p_max).
    Default calibration from 60-day InfluxDB empirical data:
        P_el(50C, 10C out) = 2.72 kW
        k_t_tank = 0.074 kW / K
        k_out = 0.057 kW / K
        clamp = [1.6 kW, 3.5 kW]
    """
    if params and isinstance(params, dict):
        p_cfg = params.get("dhw_power")
        if isinstance(p_cfg, dict):
            return (
                float(p_cfg.get("p_nom_50", 2.72)),
                float(p_cfg.get("k_t_tank", 0.074)),
                float(p_cfg.get("k_out", 0.057)),
                float(p_cfg.get("p_min_kw", 1.6)),
                float(p_cfg.get("p_max_kw", 3.5)),
            )
    global _LOGGED_POWER_FALLBACK
    if not _LOGGED_POWER_FALLBACK:
        print("[WARN] get_dhw_power_params: geen 'dhw_power' blok in modelparameters; teruggevallen op standaarden (p_nom_50=2.72).")
        _LOGGED_POWER_FALLBACK = True
    return (2.72, 0.074, 0.057, 1.6, 3.5)


def dhw_electric_power_kw(
    t_tank_c: float,
    t_outdoor_c: Optional[float] = None,
    params: Optional[dict] = None
) -> float:
    """
    Pure function: empirical tank-temperature and outdoor-temperature-dependent DHW electrical power (kW).
    Formula:
        dhw_electric_power_kw(t_tank_c, t_outdoor_c, params) = clamp(
            P_50 + k_t_tank * (t_tank_c - 50.0) - k_out * (t_outdoor_c - 10.0),
            P_min,
            P_max
        )
    """
    p_50, k_t_tank, k_out, p_min, p_max = get_dhw_power_params(params)
    t_out = 10.0 if t_outdoor_c is None else float(t_outdoor_c)
    raw_p = p_50 + (k_t_tank * (float(t_tank_c) - 50.0)) - (k_out * (t_out - 10.0))
    clamped = max(p_min, min(p_max, raw_p))
    return round(clamped, 3)


def dhw_cop(
    t_tank_c: float,
    t_outdoor_c: Optional[float] = None,
    params: Optional[dict] = None
) -> float:
    """
    Pure function: empirical tank-temperature and outdoor-temperature-dependent DHW COP.
    Formula:
        dhw_cop(t_tank_c, t_outdoor_c, params) = clamp(
            COP_50 - k_T * (t_tank_c - 50.0) + k_out * (t_outdoor_c - 10.0),
            COP_min,
            COP_max
        )
    Baseline start values:
        COP_50 = 2.85
        k_T = 0.07 per K (yields 2.15 at 60°C)
        k_out = 0.05 per K
        clamp: [1.6, 3.6]
    All five values come from params dict (e.g. heatpump_model_parameters.json).
    If t_outdoor_c is None, nominal 10.0°C reference condition is assumed.
    """
    cop_50, k_t, k_out, cop_min, cop_max = get_dhw_cop_params(params)
    t_out = 10.0 if t_outdoor_c is None else float(t_outdoor_c)
    raw_cop = cop_50 - (k_t * (float(t_tank_c) - 50.0)) + (k_out * (t_out - 10.0))
    clamped = max(cop_min, min(cop_max, raw_cop))
    return round(clamped, 4)


def dhw_heat_delivered_kwh(
    t_tank_c: float,
    t_outdoor_c: Optional[float],
    p_el_kw: float,
    dt_h: float = 0.25,
    params: Optional[dict] = None
) -> float:
    """
    Pure function: Delivered thermal energy (kWh_th) during time window dt_h.
    q_geleverd = p_el_kw * dhw_cop(t_tank_c, t_outdoor_c, params) * dt_h
    """
    cop = dhw_cop(t_tank_c, t_outdoor_c, params=params)
    return p_el_kw * cop * dt_h


def dhw_step(
    t_tank_c: float,
    u: float,
    q_tap_kwh: float,
    t_outdoor_c: Optional[float] = None,
    dt_h: float = 0.25,
    spec: Any = None,
    params: Optional[dict] = None,
    t_max_c: Optional[float] = None,
    t_amb_c: float = 18.0,
) -> float:
    """
    Pure function: 1D lumped-capacitance DHW tank energy balance step.
    t_next = t + ( u * q_geleverd - UA * (t - T_amb) * dt_h / 1000 - q_tap_kwh ) / C
    Bounded on [15.0, T_max]; above T_max no heat is added.
    """
    # 1. Thermal capacity C (kWh / K)
    c_tank = 0.407
    if spec is not None:
        if hasattr(spec, "thermal_capacity_kwh_per_k"):
            c_tank = float(spec.thermal_capacity_kwh_per_k)
        elif isinstance(spec, dict) and "thermal_capacity_kwh_per_k" in spec:
            c_tank = float(spec["thermal_capacity_kwh_per_k"])
        elif hasattr(spec, "c_tank_kwh_per_c"):
            c_tank = float(spec.c_tank_kwh_per_c)

    # 2. Standby loss UA (W / K)
    ua = 2.5
    if spec is not None:
        if hasattr(spec, "ua_w_per_k") and getattr(spec, "ua_w_per_k") is not None:
            ua = float(spec.ua_w_per_k)
        elif hasattr(spec, "standby_loss_w_per_k") and getattr(spec, "standby_loss_w_per_k") is not None:
            ua = float(spec.standby_loss_w_per_k)
        elif isinstance(spec, dict):
            if "ua_w_per_k" in spec:
                ua = float(spec["ua_w_per_k"])
            elif "standby_loss_w_per_k" in spec:
                ua = float(spec["standby_loss_w_per_k"])
    elif params is not None:
        p_tank = params.get("dhw_tank", {})
        if isinstance(p_tank, dict) and "standby_loss_w_per_k" in p_tank:
            ua = float(p_tank["standby_loss_w_per_k"])
        elif "standby_loss_w_per_k" in params:
            ua = float(params["standby_loss_w_per_k"])

    # 3. Cutoff / maximum temperature T_max (°C)
    t_max = 60.0
    if t_max_c is not None:
        t_max = float(t_max_c)
    elif spec is not None:
        if hasattr(spec, "target_temp_c") and getattr(spec, "target_temp_c") is not None:
            t_max = float(spec.target_temp_c)
        elif hasattr(spec, "boost_setpoint_c") and getattr(spec, "boost_setpoint_c") is not None:
            t_max = float(spec.boost_setpoint_c)
        elif hasattr(spec, "target_setpoint_c") and getattr(spec, "target_setpoint_c") is not None:
            t_max = float(spec.target_setpoint_c)
        elif hasattr(spec, "t_max_c") and getattr(spec, "t_max_c") is not None:
            t_max = float(spec.t_max_c)
        elif isinstance(spec, dict):
            if "target_temp_c" in spec:
                t_max = float(spec["target_temp_c"])
            elif "t_max_c" in spec:
                t_max = float(spec["t_max_c"])

    # 4. Compressor electric power P_el (kW)
    p_el_kw = dhw_electric_power_kw(t_tank_c, t_outdoor_c, params=params)
    if spec is not None and not isinstance(spec, dict):
        if hasattr(spec, "get_electric_power_kw"):
            try:
                p_el_kw = float(getattr(spec, "get_electric_power_kw")(t_tank_c, outdoor_temp_c=t_outdoor_c, params=params))
            except TypeError:
                p_el_kw = float(getattr(spec, "get_electric_power_kw")(t_tank_c))
        elif hasattr(spec, "heat_pump_power_kw") and getattr(spec, "heat_pump_power_kw") is not None:
            p_el_kw = float(spec.heat_pump_power_kw)
        elif hasattr(spec, "heat_pump_electric_kw") and getattr(spec, "heat_pump_electric_kw") is not None:
            p_el_kw = float(spec.heat_pump_electric_kw)
            if t_tank_c > 52.0 and hasattr(spec, "solar_boost_electric_kw"):
                p_el_kw = float(spec.solar_boost_electric_kw)
    elif isinstance(spec, dict):
        p_el_kw = float(spec.get("heat_pump_power_kw", spec.get("heat_pump_electric_kw", p_el_kw)))

    # 5. Delivered heat q_geleverd
    # Above T_max no heat is added; heat addition cannot exceed T_max
    if u > 0 and t_tank_c < t_max:
        raw_heat = dhw_heat_delivered_kwh(t_tank_c, t_outdoor_c, p_el_kw, dt_h, params)
        max_heat_possible = max(0.0, (t_max - t_tank_c) * c_tank)
        q_delivered = min(u * raw_heat, max_heat_possible)
    else:
        q_delivered = 0.0

    # 6. Standby heat loss
    q_standby = (ua * max(0.0, t_tank_c - t_amb_c) * dt_h) / 1000.0

    # 7. Energy balance & bounding
    t_next_raw = t_tank_c + (q_delivered - q_standby - float(q_tap_kwh)) / c_tank
    max_bound = max(t_tank_c, t_max)
    t_next = max(15.0, min(max_bound, t_next_raw))
    return t_next


def calculate_dhw_cop(
    target_temp_c: float,
    outdoor_temp_c: Optional[float] = None,
    min_cop: float = 1.4,
    max_cop: float = 3.2,
    params: Optional[dict] = None,
) -> float:
    """
    Thin wrapper around canonical dhw_cop for backward compatibility with existing callers.
    """
    cop = dhw_cop(
        t_tank_c=target_temp_c,
        t_outdoor_c=outdoor_temp_c,
        params=params,
    )
    return round(max(min_cop, min(max_cop, cop)), 2)


def calculate_dhw_thermal_output_kw(
    target_temp_c: float,
    heat_pump_electric_kw: float = 3.0,
    solar_boost_electric_kw: float = 3.0,
    outdoor_temp_c: Optional[float] = None,
    params: Optional[dict] = None,
) -> float:
    """
    Thermodynamically consistent thermal output (kW_th) delivered by heat pump for DHW heating.
    P_th = P_el * dhw_cop(target, outdoor_temp_c).
    """
    p_el = solar_boost_electric_kw if target_temp_c > 52.0 else heat_pump_electric_kw
    cop = dhw_cop(t_tank_c=target_temp_c, t_outdoor_c=outdoor_temp_c, params=params)
    return round(p_el * cop, 3)
