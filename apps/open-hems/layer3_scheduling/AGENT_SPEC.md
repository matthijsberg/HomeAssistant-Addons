# Layer 3: Optimization & Policies — Agent Specification & Guidelines

## 🎯 Layer Scope & Responsibilities
Layer 3 is the **central brain and economic optimizer**.
It takes market prices, weather/solar predictions (from Layer 1), and calibrated physical parameters (from Layer 2), and solves the 24-hour / 96-quarter multi-asset dispatch schedule.

### What Layer 3 DOES:
1. Evaluates all 3 policy archetypes:
   * **ShiftableConsumerPolicy:** Evaluates optimal non-storage runtime slots (dishwashers, washing machines) matching lowest price blocks or solar surplus.
   * **ThermalBufferPolicy:** Solves DHW boost to 60°C during minimum tariff / maximum solar slots, while respecting peak lockouts (SG1 morning & evening).
   * **BatteryArbitragePolicy:** Implements the **Economic Deadband ($\Delta P_{\text{minimaal}} = €0{,}115/\text{kWh}$)**.
     - If price delta $< €0{,}115$: Enforces `HOLD / STANDBY` (only charges from free solar surplus).
     - If price delta $\ge €0{,}115$: Charges in minimum price slots, discharges in evening peak.
2. Solves the power priority waterfall:
   $$\text{Solar} \rightarrow \text{Baseload} \rightarrow \text{Thermal Storage (350L SWW)} \rightarrow \text{Battery} \rightarrow \text{Grid}$$
3. Generates output as a list of canonical `ScheduleSlot` objects.

### What Layer 3 MUST NEVER DO:
- **NO Hardware Switching:** Layer 3 never communicates with switches or relays directly. It only produces setpoints and schedules.
- **NO Raw External API Calls:** Layer 3 never calls EPEX or Open-Meteo directly; it consumes inputs provided by Layer 1.
- **NO Mocking of Tariffs:** Real EPEX market prices and configured statutory taxes must always be used.

---

## 🔌 Defined Interfaces
See `interfaces.py`:
- `IPolicyEvaluator`: Evaluates rules per archetype.
- `IDispatchOptimizer`: Solves global 24h schedule (`solve_daily_dispatch`).

---

## 🧪 Unit Testing Directives
- Tests must live in `tests/unit/test_power_slotter.py`.
- Must test: deadband boundary conditions ($\Delta P = €0{,}114$ vs. $€0{,}116$), peak lockout adherence, and multi-asset priority waterfall.
