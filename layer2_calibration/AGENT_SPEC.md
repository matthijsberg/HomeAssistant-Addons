# Layer 2: Empirical Calibration — Agent Specification & Guidelines

## 🎯 Layer Scope & Responsibilities
Layer 2 is a **pure statistical and thermodynamic engine**.
It takes historical time-series arrays and calculates building physics factors, solar panel geometry factors, and empirical COP degradation.

### What Layer 2 DOES:
1. Hourly solar angle matrix $K(h)$: Ordinary Least Squares (OLS) regression between Wittboy global radiation ($W/m^2$) and measured PV production.
2. Building insulation ($UA_{\text{base}}$): Linear regression of heating energy vs. Degree-Days, filtered strictly for heating season ($T_{\text{ambient}} < 15^\circ\text{C}$).
3. Defrost penalty integration: Accounts for latent heat absorption during sub-zero humid conditions.
4. 350L DHW standby loss: Empirical decay rate ($\approx 1{,}5\text{ à }2{,}0\text{ kWh/etmaal}$).
5. **80/20 Smoothing & Physical Clamping:**
   - Damping: $0{,}80 \times \text{oud} + 0{,}20 \times \text{nieuw}$.
   - Max shift: $\pm 15\text{--}20\%$ per calibration run.
   - Hard physical bounds: $K(h) \in [0{,}5; 2{,}5]$, $UA \in [6{,}0; 11{,}0]\text{ kWh/}^\circ\text{C}\cdot\text{dag}$.

### What Layer 2 MUST NEVER DO:
- **NO Network I/O:** Never make HTTP calls or database queries directly inside the math routines. Historical data must be injected via arrays or parameters.
- **NO Hardware Actuation:** Never trigger relays or change setpoints.
- **NO Fabricated Sensor Values:** Never substitute placeholder data for periods flagged in `data_exclusion_windows`.

---

## 🔌 Defined Interfaces
See `interfaces.py`:
- `IModelCalibrator`: Core physics calculation (`calibrate_solar_matrix`, `calibrate_thermal_loss`, `evaluate_seasonal_cop`).
- `ISmoothedClamping`: Guardrails (`apply_smoothing`, `clamp_bounds`).

---

## 🧪 Unit Testing Directives
- Tests must live in `tests/unit/test_calibrator.py`.
- Must test: damping behavior, physical bound enforcement, exclusion window filtering, and OLS regression edge cases (zero irradiance, night-time).
