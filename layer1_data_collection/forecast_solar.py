"""
Layer 1: Forecast.Solar Provider & Calibrator
==============================================
Fetches solar PV production estimates from Forecast.Solar API or Home Assistant,
interpolates to universal 15-minute kwartieren, and applies empirical calibration.

Does NOT use Wittboy local assimilation, relying strictly on satellite updates
and the calibrated scale factor from historical InfluxDB telemetric analysis.
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from pathlib import Path


class ForecastSolarProvider:
    """
    Ingests and calibrates solar generation forecasts from Forecast.Solar.
    """

    DEFAULT_CALIBRATION_FACTOR = 1.18  # Empirical 7-day fit against Inepro 103 (+18% peak headroom)
    DEFAULT_INVERTER_CEILING_KW = 5.5

    def __init__(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        tilt: float = 34.0,
        azimuth_deg_south: float = 45.0,  # 225 deg NOAA SW = 45 deg West of South in Forecast.Solar
        kwp: float = 5.76,
        inverter_max_kw: float = 5.5,
        calibration_factor: float = 1.18,
        cache_ttl_seconds: int = 1800  # 30 min cache
    ):
        if lat is None or lon is None:
            from layer1_data_collection.geo_location import get_geo_coordinates
            def_lat, def_lon = get_geo_coordinates()
            lat = lat if lat is not None else def_lat
            lon = lon if lon is not None else def_lon
        self.lat = lat
        self.lon = lon
        self.tilt = tilt
        self.azimuth = azimuth_deg_south
        self.kwp = kwp
        self.inverter_max_kw = inverter_max_kw
        self.calibration_factor = calibration_factor
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_watts: Dict[str, float] = {}
        self._last_fetch_time: Optional[datetime] = None

    def fetch_forecast_watts(self, force: bool = False) -> Dict[str, float]:
        """
        Fetches hourly/quarterly power estimates in Watts from Forecast.Solar.
        """
        now = datetime.now(ZoneInfo("Europe/Amsterdam"))
        if not force and self._last_fetch_time and (now - self._last_fetch_time).total_seconds() < self.cache_ttl_seconds:
            if self._cached_watts:
                return self._cached_watts

        url = f"https://api.forecast.solar/estimate/{self.lat:.4f}/{self.lon:.4f}/{int(self.tilt)}/{int(self.azimuth)}/{self.kwp:.2f}"
        req = urllib.request.Request(url, headers={"User-Agent": "OpenHEMS/1.0"})

        cache_file = Path("/config/forecast_solar_cache.json")
        if not self._cached_watts and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    disk_d = json.load(f)
                    f_time = datetime.fromisoformat(disk_d.get("timestamp", ""))
                    if (now - f_time).total_seconds() < 7200:
                        self._cached_watts = {k: float(v) for k, v in disk_d.get("watts", {}).items()}
                        self._last_fetch_time = f_time
                        if self._cached_watts:
                            return self._cached_watts
            except Exception:
                pass

        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                res = json.loads(r.read().decode())
                raw_watts = res.get("result", {}).get("watts", {})
                if raw_watts:
                    self._cached_watts = {k: float(v) for k, v in raw_watts.items()}
                    self._last_fetch_time = now
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump({"timestamp": now.isoformat(), "watts": self._cached_watts}, f)
                    except Exception:
                        pass
                return self._cached_watts
        except Exception as e:
            return self._cached_watts

    def get_calibrated_quarter_slots(
        self,
        start_dt: datetime,
        horizon_slots: int = 96,
        step_mins: int = 15
    ) -> List[Dict[str, Any]]:
        """
        Builds 15-minute calibrated solar forecast slots starting from start_dt.
        Applies empirical calibration factor and clamps to inverter capacity.
        """
        watts_map = self.fetch_forecast_watts()
        if not watts_map:
            return []  # Return empty list so callers safely trigger Open-Meteo fallback
        ams_tz = ZoneInfo("Europe/Amsterdam")

        # Convert timestamps in watts_map to parsed datetime
        parsed_points = []
        for ts_str, w in watts_map.items():
            try:
                # e.g. "2026-09-12 11:00:00"
                pt_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ams_tz)
                parsed_points.append((pt_dt, w))
            except Exception:
                pass

        parsed_points.sort(key=lambda x: x[0])

        # Automatically align start_dt to quarter-hour boundary
        aligned_min = (start_dt.minute // step_mins) * step_mins
        aligned_start = start_dt.replace(minute=aligned_min, second=0, microsecond=0)

        slots = []
        for i in range(horizon_slots):
            slot_dt = aligned_start + timedelta(minutes=i * step_mins)
            
            # Find nearest or linear interpolation between points
            w_val = 0.0
            if parsed_points:
                # Find interval [p0, p1]
                if slot_dt <= parsed_points[0][0]:
                    w_val = parsed_points[0][1] if (parsed_points[0][0] - slot_dt).total_seconds() < 1800 else 0.0
                elif slot_dt > parsed_points[-1][0]:
                    # Beyond Forecast.Solar coverage: break so Open-Meteo fallback cleanly covers remaining slots
                    break
                else:
                    for j in range(len(parsed_points) - 1):
                        p0 = parsed_points[j]
                        p1 = parsed_points[j + 1]
                        if p0[0] <= slot_dt <= p1[0]:
                            span = (p1[0] - p0[0]).total_seconds()
                            if span > 0:
                                frac = (slot_dt - p0[0]).total_seconds() / span
                                w_val = p0[1] + (p1[1] - p0[1]) * frac
                            else:
                                w_val = p0[1]
                            break

            # Apply empirical calibration factor
            calibrated_w = w_val * self.calibration_factor
            calibrated_kw = min(self.inverter_max_kw, calibrated_w / 1000.0)

            slots.append({
                "slot_idx": i,
                "dt": slot_dt,
                "timestamp": slot_dt.isoformat(),
                "raw_watts": round(w_val, 1),
                "solar_kw": round(max(0.0, calibrated_kw), 3)
            })

        return slots
