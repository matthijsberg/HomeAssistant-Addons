"""
Integrations: Daikin Altherma Hydraulic Interlocks & Relay Mapping
==================================================================
Hardware-level safety invariants for Daikin Altherma 3 H HT + Hydrobox.

Physical Invariants:
1. Hydraulic Interlock: During active DHW heating (Smart Grid Stand 4),
   the central heating master switch must be forced OFF to prevent the
   9 kW backup heater (BUH) from firing simultaneously on the space heating circuit.
2. Lossy State Mapping: Daikin SG interface provides only 4 physical relay combinations:
   - SG1 (S10S=1, S11S=0): Forced Off
   - SG2 (S10S=0, S11S=0): Normal
   - SG3 (S10S=1, S11S=1): Recommended On (Space heating boost)
   - SG4 (S10S=0, S11S=1): Forced Run (DHW heating)
   ADVISED_OFF is mapped to SG2 (Normal) with an explicit downgrade reason.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple


@dataclass(frozen=True)
class DaikinHardwareCommand:
    s10s_relay_on: bool
    s11s_relay_on: bool
    cv_master_switch_on: bool
    effective_mode: str
    dhw_master_switch_on: bool = True
    target_dhw_temp_c: Optional[float] = None
    target_room_temp_c: Optional[float] = None
    downgrade_reason: Optional[str] = None


class DaikinInterlock:
    """
    Translates programmatic mode codes into physical relay and switch commands,
    enforcing safety interlocks at the hardware boundary.
    """

    @classmethod
    def resolve_command(
        cls,
        requested_mode: str,
        current_cv_switch_state: bool = True,
        target_temp: Optional[float] = None,
        current_continuous_lockout_mins: float = 0.0
    ) -> DaikinHardwareCommand:
        """
        Resolves physical states and enforces hydraulic interlocks.
        Includes hard circuit breaker: max 150m continuous forced lockout cap.
        """
        # 1. Forced Off (SG Stand 1: S10S=OFF, S11S=ON)
        if requested_mode == "forced_off":
            if current_continuous_lockout_mins >= 150.0:
                return DaikinHardwareCommand(
                    s10s_relay_on=False,
                    s11s_relay_on=False,
                    cv_master_switch_on=True,
                    effective_mode="normal",
                    downgrade_reason=f"Maximale aaneengesloten spitsduur bereikt ({current_continuous_lockout_mins:.0f}m >= 150m); relais SG1 vrijgegeven naar SG2 normaal tegen afkoeling"
                )
            return DaikinHardwareCommand(
                s10s_relay_on=False,
                s11s_relay_on=True,
                cv_master_switch_on=False,
                effective_mode="forced_off",
                downgrade_reason=None
            )

        # 2. Recommended On / Pre-heat (SG Stand 3: S10S=ON, S11S=OFF)
        elif requested_mode == "advised_on":
            return DaikinHardwareCommand(
                s10s_relay_on=True,
                s11s_relay_on=False,
                cv_master_switch_on=True,
                effective_mode="advised_on",
                downgrade_reason=None
            )

        # 3. Forced On (DHW Run 50°C) or Max On (DHW Solar Boost 60°C) (SG Stand 4: S10S=ON, S11S=ON)
        elif requested_mode in ["forced_on", "max_on", "forced_solar_boost_60", "forced_night_50"]:
            target_dhw = 60.0 if requested_mode in ["max_on", "forced_solar_boost_60"] else 50.0
            # CRITICAL HYDRAULIC INTERLOCK: CV Master Switch MUST be turned OFF
            return DaikinHardwareCommand(
                s10s_relay_on=True,
                s11s_relay_on=True,
                cv_master_switch_on=False,  # Enforce OFF so 3-way valve routes to DHW
                dhw_master_switch_on=True,
                effective_mode=requested_mode if requested_mode in ["forced_on", "max_on"] else "forced_on",
                target_dhw_temp_c=target_dhw,
                downgrade_reason=None
            )

        # 3b. Forced Space Heating (Vloerverwarming Boost): SG Stand 4 (S10S=ON, S11S=ON) with DHW turned OFF!
        elif requested_mode == "forced_space_heating":
            return DaikinHardwareCommand(
                s10s_relay_on=True,
                s11s_relay_on=True,
                cv_master_switch_on=True,
                dhw_master_switch_on=False,  # Enforce DHW OFF so 3-way valve stays on CV underfloor
                effective_mode="forced_space_heating",
                target_room_temp_c=target_temp,
                downgrade_reason=None
            )

        # 4. Advised Off (Lossy Mapping: Daikin SG has no Stand for soft advice)
        elif requested_mode == "advised_off":
            # Downgrade to SG2 (Normal) and report downgrade
            return DaikinHardwareCommand(
                s10s_relay_on=False,
                s11s_relay_on=False,
                cv_master_switch_on=current_cv_switch_state,
                effective_mode="normal",
                downgrade_reason="Daikin SG contacts lack soft-lockout state; downgraded to SG2 normal with setpoint bias"
            )

        # 5. Normal (SG Stand 2)
        else:
            return DaikinHardwareCommand(
                s10s_relay_on=False,
                s11s_relay_on=False,
                cv_master_switch_on=current_cv_switch_state,
                effective_mode="normal",
                downgrade_reason=None
            )
