"""
Layer 1: Centralized Geolocation & Coordinates Provider
=======================================================
Pure-function helper to retrieve latitude and longitude for weather
and solar PV yield forecasting.

Priority:
1. Home Assistant instance coordinates (/api/config)
2. house.latitude / house.longitude from heatpump_config.json
3. Fallback defaults (51.9537, 5.2320)
"""

from typing import Tuple, Dict, Any, Optional
import urllib.request
import json
import ssl


FALLBACK_LAT = 51.9537
FALLBACK_LON = 5.2320


def get_geo_coordinates(
    cfg: Optional[Dict[str, Any]] = None,
    ha_url: Optional[str] = None,
    ha_token: Optional[str] = None
) -> Tuple[float, float]:
    """
    Returns (latitude, longitude) as a tuple of floats.
    """
    # 1. Try Home Assistant Core /api/config if credentials provided
    if ha_url and ha_token:
        try:
            clean_url = ha_url.rstrip("/")
            req = urllib.request.Request(
                f"{clean_url}/api/config",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=3, context=ctx) as r:
                data = json.loads(r.read().decode())
                lat = data.get("latitude")
                lon = data.get("longitude")
                if lat is not None and lon is not None:
                    return float(lat), float(lon)
        except Exception:
            pass

    # 2. Try configured house parameters in config
    if cfg:
        house = cfg.get("house", {})
        lat = house.get("latitude")
        lon = house.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)

    # 3. Default fallback
    return FALLBACK_LAT, FALLBACK_LON
