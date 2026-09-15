"""
Layer 1: Telemetry Sanitizer & Quality Assurance
================================================
Validates raw sensory data from InfluxDB, Modbus, Open-Meteo, and EPEX.
Applies freshness checks, physical boundary validation, and uniform
quarter-hourly (15-minute) grid alignment.

Strictly enforces: No mock data in production, clear quality flags.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from models.canonical import Quality


@dataclass
class TelemetrySlot:
    """A single 15-minute sanitized telemetry slot."""
    slot_idx: int
    dt: datetime
    label: str
    price_all_in: float
    solar_kw: float
    outdoor_temp_c: float
    unallocated_kw: float
    dhw_temp_c: Optional[float] = None
    room_temp_c: Optional[float] = None
    floor_temp_c: Optional[float] = None
    quality: Quality = Quality.GOOD
    issues: List[str] = field(default_factory=list)


@dataclass
class CleanTelemetryFrame:
    """Sanitized, time-aligned, and verified telemetry frame for central planning."""
    timestamp: datetime
    is_fresh: bool
    freshness_age_seconds: float
    resolution_minutes: int
    slots: List[TelemetrySlot]
    metadata: Dict[str, Any] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)

    @property
    def prices(self) -> List[float]:
        return [s.price_all_in for s in self.slots]

    @property
    def solar_forecast(self) -> List[float]:
        return [s.solar_kw for s in self.slots]

    @property
    def outdoor_temperatures(self) -> List[float]:
        return [s.outdoor_temp_c for s in self.slots]

    @property
    def unallocated_kw(self) -> List[float]:
        return [s.unallocated_kw for s in self.slots]

    @property
    def current_room_temp(self) -> Optional[float]:
        return self.slots[0].room_temp_c if self.slots else None

    @property
    def current_floor_temp(self) -> Optional[float]:
        return self.slots[0].floor_temp_c if self.slots else None

    @property
    def target_room_temp(self) -> float:
        return float(self.metadata.get("target_room_temp_c", 20.0))


class TelemetrySanitizer:
    """
    Sanitizes and aligns multi-vector sensory streams into a contractual CleanTelemetryFrame.
    """

    MAX_FRESHNESS_AGE_SECONDS = 900  # 15 minutes max for real-time telemetry

    @classmethod
    def sanitize(
        cls,
        now: datetime,
        raw_prices: List[Dict[str, Any]],
        raw_solar: List[Dict[str, Any]],
        raw_weather: List[Dict[str, Any]],
        raw_unallocated_matrix: Any,
        current_dhw_temp: Optional[float] = None,
        current_room_temp: Optional[float] = None,
        current_floor_temp: Optional[float] = None,
        target_room_temp: Optional[float] = None,
        last_hardware_reading_time: Optional[datetime] = None,
        horizon_slots: int = 96,
        step_mins: int = 15
    ) -> CleanTelemetryFrame:
        """
        Processes raw streams into a standardized CleanTelemetryFrame.
        """
        issues = []
        validation_errors = []

        # 1. Freshness Check
        if last_hardware_reading_time is None:
            age_s = 0.0
            is_fresh = True
        else:
            if last_hardware_reading_time.tzinfo is None:
                last_hardware_reading_time = last_hardware_reading_time.replace(tzinfo=now.tzinfo)
            age_s = max(0.0, (now - last_hardware_reading_time).total_seconds())
            is_fresh = age_s <= cls.MAX_FRESHNESS_AGE_SECONDS
            if not is_fresh:
                issues.append(f"Hardware-telemetrie is {int(age_s / 60)} min oud (overschrijdt 15m limiet)")

        # 2. Align start to quarter-hour boundary
        aligned_minute = (now.minute // step_mins) * step_mins
        grid_start = now.replace(minute=aligned_minute, second=0, microsecond=0)

        # Lookup maps by rounded datetime
        price_map = {}
        for p in raw_prices:
            dt = p.get("dt") or p.get("timestamp")
            if dt:
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    except Exception:
                        continue
                if dt.tzinfo is None and now.tzinfo is not None:
                    dt = dt.replace(tzinfo=now.tzinfo)
                p_val = float(p.get("price", p.get("all_in_price", 0.30)))
                # Clamp extreme price spikes / negative errors
                p_val = max(-1.0, min(5.0, p_val))
                key = dt.strftime("%Y-%m-%d %H:%M")
                price_map[key] = p_val

        solar_map = {}
        for s in raw_solar:
            dt = s.get("dt") or s.get("timestamp")
            if dt:
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    except Exception:
                        continue
                if dt.tzinfo is None and now.tzinfo is not None:
                    dt = dt.replace(tzinfo=now.tzinfo)
                s_val = max(0.0, float(s.get("solar_kw", s.get("power_kw", 0.0))))
                # Physical bounds: max 5.76 kWp on Matthijs's roof
                s_val = min(6.0, s_val)
                key = dt.strftime("%Y-%m-%d %H:%M")
                solar_map[key] = s_val

        weather_map = {}
        for w in raw_weather:
            dt = w.get("dt") or w.get("timestamp")
            if dt:
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    except Exception:
                        continue
                if dt.tzinfo is None and now.tzinfo is not None:
                    dt = dt.replace(tzinfo=now.tzinfo)
                t_val = float(w.get("temperature", w.get("temp_c", 15.0)))
                # Physical sanity bounds: -30°C to +50°C
                t_val = max(-30.0, min(50.0, t_val))
                key = dt.strftime("%Y-%m-%d %H:%M")
                weather_map[key] = t_val

        # Sanitize current temperatures
        clean_dhw = None
        if current_dhw_temp is not None:
            if 10.0 <= current_dhw_temp <= 95.0:
                clean_dhw = float(current_dhw_temp)
            else:
                validation_errors.append(f"DHW sensor out of physical bounds: {current_dhw_temp}°C")

        clean_room = None
        if current_room_temp is not None:
            if 5.0 <= current_room_temp <= 40.0:
                clean_room = float(current_room_temp)

        clean_floor = None
        if current_floor_temp is not None:
            if 10.0 <= current_floor_temp <= 50.0:
                clean_floor = float(current_floor_temp)

        # 3. Build standardized slots
        slots: List[TelemetrySlot] = []
        last_known_p = 0.30
        last_known_t = 15.0

        for i in range(horizon_slots):
            slot_dt = grid_start + timedelta(minutes=i * step_mins)
            key_exact = slot_dt.strftime("%Y-%m-%d %H:%M")
            key_hour = slot_dt.strftime("%Y-%m-%d %H:00")

            # Price matching with hour fallback
            if key_exact in price_map:
                p_val = price_map[key_exact]
                p_q = Quality.GOOD
            elif key_hour in price_map:
                p_val = price_map[key_hour]
                p_q = Quality.GOOD
            else:
                p_val = last_known_p
                p_q = Quality.INTERPOLATED
            last_known_p = p_val

            # Solar matching
            if key_exact in solar_map:
                s_val = solar_map[key_exact]
            elif key_hour in solar_map:
                s_val = solar_map[key_hour]
            else:
                s_val = 0.0

            # Outdoor temp matching
            if key_exact in weather_map:
                t_val = weather_map[key_exact]
            elif key_hour in weather_map:
                t_val = weather_map[key_hour]
            else:
                t_val = last_known_t
            last_known_t = t_val

            # Unallocated demand matching (7x96 matrix support)
            # Map slot to weekday (0=Ma..6=Zo) and quarter (0-95)
            weekday_idx = slot_dt.weekday()
            quarter_idx = slot_dt.hour * 4 + (slot_dt.minute // 15)
            u_w = None

            if raw_unallocated_matrix:
                # Shape A: 2D list of 7 days x 96 quarters (Watts)
                if len(raw_unallocated_matrix) == 7 and isinstance(raw_unallocated_matrix[0], (list, tuple)):
                    day_q = raw_unallocated_matrix[weekday_idx]
                    if quarter_idx < len(day_q):
                        u_w = float(day_q[quarter_idx])
                # Shape B: 1D flat list of 672 quarters
                elif len(raw_unallocated_matrix) == 672:
                    matrix_idx = weekday_idx * 96 + quarter_idx
                    u_w = float(raw_unallocated_matrix[matrix_idx])
                # Shape C: 1D list of 96 quarters (single day)
                elif len(raw_unallocated_matrix) == 96:
                    u_w = float(raw_unallocated_matrix[quarter_idx])

            if u_w is not None:
                # Convert Watts to kW if > 10.0
                u_kw = (u_w / 1000.0) if u_w > 10.0 else u_w
                u_val = max(0.05, round(u_kw, 3))
            else:
                u_val = 0.35  # baseline standby household power ~350W

            lbl = "Nu (" + slot_dt.strftime("%H:%M") + ")" if i == 0 else slot_dt.strftime("%H:%M")

            slot_issues = []
            if p_q == Quality.INTERPOLATED:
                slot_issues.append("EPEX prijs geïnterpoleerd")

            slots.append(
                TelemetrySlot(
                    slot_idx=i,
                    dt=slot_dt,
                    label=lbl,
                    price_all_in=round(p_val, 4),
                    solar_kw=round(s_val, 3),
                    outdoor_temp_c=round(t_val, 1),
                    unallocated_kw=round(u_val, 3),
                    dhw_temp_c=clean_dhw if i == 0 else None,
                    room_temp_c=clean_room if i == 0 else None,
                    floor_temp_c=clean_floor if i == 0 else None,
                    quality=p_q if is_fresh else Quality.STALE,
                    issues=slot_issues
                )
            )

        return CleanTelemetryFrame(
            timestamp=now,
            is_fresh=is_fresh,
            freshness_age_seconds=age_s,
            resolution_minutes=step_mins,
            slots=slots,
            metadata={
                "aligned_grid_start": grid_start.isoformat(),
                "slot_count": len(slots),
                "horizon_hours": (horizon_slots * step_mins) / 60.0,
                "target_room_temp_c": round(max(15.0, min(25.0, target_room_temp)), 1) if target_room_temp is not None else 20.0
            },
            validation_errors=validation_errors + issues
        )
