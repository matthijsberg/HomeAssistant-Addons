# Layer 4: Actuation & Safety Guards — Agent Specification & Guidelines

## 🎯 Layer Scope & Responsibilities
Layer 4 is the **critical safety gatekeeper and hardware executor**.
No command from Layer 3 reaches physical hardware without being validated by Layer 4 safety guardrails.

### What Layer 4 DOES:
1. **Priority 1 Emergency Comfort:**
   - If DHW tank temperature $< 38{,}0^\circ\text{C}$, trigger emergency reheat immediately, overriding all spot prices and peak lockouts.
2. **Compressor Dwell-Time Protection:**
   - Enforces a minimum dwell-time (default 20 minutes) between mode switches to prevent compressor wear from rapid cycling.
3. **Hydraulic Exclusivity (9 kW BUH Protection):**
   - The Daikin 3-way valve (`EKHY3PART`) physically isolates DHW and CV.
   - During forced DHW boost (SG4), space heating (`switch.hc_mode_altherma_on`) MUST be turned OFF to prevent the 9 kW backup heater (BUH) from engaging.
4. **Volatile RAM Relay Execution (Zero EEPROM Wear):**
   - Translates SG1..SG4 into binary contacts S10S and S11S:
     * `SG1` (Blokkade): S10S Open, S11S Dicht
     * `SG2` (Auto/Eco): S10S Open, S11S Open
     * `SG3` (Advies Aan): S10S Dicht, S11S Open
     * `SG4` (Geforceerd 60°C): S10S Dicht, S11S Dicht

### What Layer 4 MUST NEVER DO:
- **NO EEPROM Bus Writes:** Never continuously overwrite target temperatures over Modbus or P1P2 bus. All dynamic control must use physical SG-Ready relays evaluated in Daikin RAM.
- **NO Bypassing of Safety:** Never skip the 20-minute compressor dwell-time or the hydraulic BUH isolation.
- **NO Scheduling Logic:** Layer 4 does not determine which hour is cheapest; it only verifies and executes commands safely.

---

## 🔌 Defined Interfaces
See `interfaces.py`:
- `ISafetyGuard`: Comfort floor, compressor dwell-time, and hydraulic isolation (`evaluate_emergency_comfort`, `enforce_compressor_dwell_time`, `verify_hydraulic_isolation`).
- `IActuatorController`: Binary relay switcher (`apply_smart_grid_mode`, `execute_command`).

---

## 🧪 Unit Testing Directives
- Tests must verify:
  1. Tank $< 38{,}0^\circ\text{C}$ always generates Priority 1 command.
  2. Mode switch within 20 minutes is blocked.
  3. SG4 command automatically generates turn_off command for `switch.hc_mode_altherma_on`.
  4. S10S/S11S truth table mapping matches SG1..SG4 hardware specifications.
