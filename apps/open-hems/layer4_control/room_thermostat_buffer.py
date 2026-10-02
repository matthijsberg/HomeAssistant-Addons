"""
Layer 4: Room Thermostat Floor Buffer Controller
================================================
Manages thermal floor pre-buffering via living room thermostat setpoint modulation
(climate.woonkamer_climate_daikin) with strict chattering prevention and rate limiting.

Key Invariants:
1. Rate Limiting:
   - Maximum 2 setpoint adjustments per rolling hour (at most 1 UP and 1 DOWN).
   - Maximum 6 setpoint adjustments per rolling 24-hour window (at most 3 complete cycles).
2. Minimum Run Duration:
   - Once elevated for pre-heat, the setpoint must remain elevated for at least 45 minutes
     to prevent short-cycling the compressor on the massive concrete floor.
3. Baseline Restoration & Watchdog:
   - Once buffering completes, the setpoint is strictly restored to the pre-elevation baseline.
   - The watchdog audits every cycle: if no floor buffer is active, setpoint must be at baseline.
"""

from typing import List, Tuple, Optional
from datetime import datetime, timedelta, timezone


class RoomThermostatBufferController:
    MAX_CHANGES_PER_HOUR = 2
    MAX_CHANGES_PER_24H = 6
    MIN_RUN_DURATION_SECONDS = 45 * 60  # 45 minutes minimum run
    MAX_PREHEAT_SAFETY_TEMP_C = 22.5    # Upper thermal safety ceiling for room air

    def __init__(self, default_baseline_c: float = 20.5):
        self.baseline_temp_c: float = default_baseline_c
        self.is_buffering: bool = False
        self.buffering_started_at: Optional[datetime] = None
        self.change_history: List[datetime] = []

    def clean_history(self, now: datetime) -> None:
        """Prunes historical change timestamps older than 24 hours."""
        cutoff = now - timedelta(hours=24)
        self.change_history = [t for t in self.change_history if t > cutoff]

    def can_change(self, now: datetime) -> Tuple[bool, str]:
        """Verifies rate-limiting invariants: max 2 changes/hour, max 6 changes/24h."""
        self.clean_history(now)
        one_hour_ago = now - timedelta(hours=1)
        changes_1h = sum(1 for t in self.change_history if t > one_hour_ago)
        if changes_1h >= self.MAX_CHANGES_PER_HOUR:
            return False, f"Uurlimiet bereikt ({changes_1h}/{self.MAX_CHANGES_PER_HOUR} wijzigingen in het afgelopen uur)"

        changes_24h = len(self.change_history)
        if changes_24h >= self.MAX_CHANGES_PER_24H:
            return False, f"24-uurs limiet bereikt ({changes_24h}/{self.MAX_CHANGES_PER_24H} wijzigingen in de afgelopen 24 uur)"

        return True, "OK"

    def request_preheat(
        self,
        current_room_temp_c: float,
        current_setpoint_c: float,
        now: Optional[datetime] = None
    ) -> Optional[float]:
        """
        Requests elevating room thermostat by at least +1.0°C above current temperature
        to trigger active floor buffering. Returns the target temperature, or None if rejected.
        """
        now = now or datetime.now(timezone.utc)
        if self.is_buffering:
            return None  # Already in buffering state

        allowed, reason = self.can_change(now)
        if not allowed:
            return None

        # Capture baseline before elevation
        if current_setpoint_c and current_setpoint_c > 15.0:
            self.baseline_temp_c = float(current_setpoint_c)

        # Target: at least 1.0°C above current room temperature AND at least 1.0°C above baseline
        elevated_target = max(current_room_temp_c + 1.0, self.baseline_temp_c + 1.0)
        elevated_target = min(self.MAX_PREHEAT_SAFETY_TEMP_C, round(elevated_target, 1))

        self.change_history.append(now)
        self.is_buffering = True
        self.buffering_started_at = now
        return elevated_target

    def request_release(
        self,
        now: Optional[datetime] = None,
        force: bool = False
    ) -> Optional[float]:
        """
        Requests releasing preheat and returning room thermostat to baseline setpoint.
        Enforces minimum 45-minute run duration unless force=True.
        Returns the baseline temperature to restore, or None if release is delayed.
        """
        now = now or datetime.now(timezone.utc)
        if not self.is_buffering:
            return None

        if not force:
            if self.buffering_started_at:
                elapsed_sec = (now - self.buffering_started_at).total_seconds()
                if elapsed_sec < self.MIN_RUN_DURATION_SECONDS:
                    # Minimum run duration not yet satisfied; hold elevation
                    return None

            allowed, reason = self.can_change(now)
            if not allowed:
                return None

        self.change_history.append(now)
        self.is_buffering = False
        self.buffering_started_at = None
        return self.baseline_temp_c

    def watchdog_check(
        self,
        current_setpoint_c: float,
        now: Optional[datetime] = None
    ) -> Optional[float]:
        """
        Watchdog failsafe: If no buffering run is active but setpoint is elevated
        above baseline, forces restoration to baseline setpoint.
        """
        now = now or datetime.now(timezone.utc)
        if not self.is_buffering:
            if current_setpoint_c > self.baseline_temp_c + 0.2:
                # Setpoint is lingering in elevated state without active buffering run!
                self.change_history.append(now)
                return self.baseline_temp_c
        return None
