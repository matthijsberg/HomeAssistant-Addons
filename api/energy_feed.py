"""
Open HEMS: Energy Data Feeds & Weather Forecasting
==================================================
Handles wholesale electricity prices (EPEX), POA solar modeling,
Wittboy weather assimilation, and recent telemetry retrieval.
"""

import os
import sys
import time
import math
import json
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from api.secrets_store import load_secrets
from integrations.homeassistant.client import get_ha_client_config, get_ha_states_map
from layer1_data_collection.geo_location import get_geo_coordinates


AMS_TZ = ZoneInfo("Europe/Amsterdam")
DUTCH_DAYS_SHORT = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]


def format_slot_label(dt_slot: datetime, prev_dt: Optional[datetime], is_first: bool, is_15m: bool) -> str:
    """Formats standardized Dutch slot labels for chart axes."""
    time_str = dt_slot.strftime("%H:%M" if is_15m else "%H:00")
    if is_first:
        return f"Nu ({time_str})"
    if prev_dt is not None and dt_slot.day != prev_dt.day:
        day_str = DUTCH_DAYS_SHORT[dt_slot.weekday()]
        return f"{day_str} {time_str}"
    return time_str


def calculate_poa_solar_kw(
    dt_ams: datetime,
    ghi_w_m2: float,
    kwp: float = 5.76,
    tilt_deg: float = 34.0,
    azimuth_deg: float = 225.0,
    inverter_limit_kw: float = 5.5,
    eff: float = 0.88,
    lat: Optional[float] = None,
    lon: Optional[float] = None
) -> float:
    """Calculates Plane-of-Array (POA) solar generation in AC kW based on NOAA solar geometry."""
    if lat is None or lon is None:
        lat, lon = get_geo_coordinates()
    if ghi_w_m2 <= 1.0:
        return 0.0
    doy = dt_ams.timetuple().tm_yday
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284.0 + doy) / 365.0)))
    b = math.radians(360.0 * (doy - 81) / 364.0)
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    utcoff = dt_ams.utcoffset()
    tz_offset = utcoff.total_seconds() / 3600.0 if utcoff is not None else 2.0
    solar_time_h = dt_ams.hour + dt_ams.minute / 60.0 + dt_ams.second / 3600.0 + (4.0 * lon + eot) / 60.0 - tz_offset
    omega = math.radians((solar_time_h - 12.0) * 15.0)
    lat_r = math.radians(lat)
    sin_alpha = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(omega)
    alpha = math.asin(max(-1.0, min(1.0, sin_alpha)))
    cos_alpha = math.cos(alpha)
    if math.degrees(alpha) <= 1.0:
        return 0.0
    cos_az = (math.sin(decl) * math.cos(lat_r) - math.cos(decl) * math.sin(lat_r) * math.cos(omega)) / max(0.001, cos_alpha)
    cos_az = max(-1.0, min(1.0, cos_az))
    az_deg = math.degrees(math.acos(cos_az))
    if omega > 0:
        az_deg = 360.0 - az_deg
    beta_r = math.radians(tilt_deg)
    gamma_diff_r = math.radians(az_deg - azimuth_deg)
    cos_aoi = math.cos(alpha) * math.sin(beta_r) * math.cos(gamma_diff_r) + math.sin(alpha) * math.cos(beta_r)
    kt = min(1.0, ghi_w_m2 / (1367.0 * max(0.05, math.sin(alpha))))
    if kt <= 0.22:
        df_frac = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        df_frac = 0.9511 - 0.1604 * kt + 4.388 * (kt**2) - 16.638 * (kt**3) + 12.336 * (kt**4)
    else:
        df_frac = 0.165
    diffuse_horiz = ghi_w_m2 * max(0.15, min(1.0, df_frac))
    direct_horiz = max(0.0, ghi_w_m2 - diffuse_horiz)
    direct_normal = direct_horiz / max(0.05, math.sin(alpha))
    poa_beam = direct_normal * max(0.0, cos_aoi)
    poa_diffuse = diffuse_horiz * (1.0 + math.cos(beta_r)) / 2.0
    poa_ground = ghi_w_m2 * 0.20 * (1.0 - math.cos(beta_r)) / 2.0
    poa_total = max(0.0, poa_beam + poa_diffuse + poa_ground)
    p_dc = (poa_total / 1000.0) * kwp * eff
    return round(min(inverter_limit_kw, p_dc), 3)


_GLOBAL_WEATHER_FORECAST_CACHE: Dict[str, Any] = {}

def get_anchored_weather_forecast(base_dt: datetime) -> tuple:
    """
    Fetches Open-Meteo weather forecast for Culemborg (51.9537, 5.2320),
    reads live local Wittboy weather station from Home Assistant,
    and performs smooth observation nudging (analysis assimilation) from local measurements
    into the regional forecast over a 3-hour decay window.
    Returns (temp_map, solar_map, wind_map, rh_map) keyed by "%Y-%m-%d %H:00".
    """
    temp_map, solar_map, wind_map, rh_map = {}, {}, {}, {}

    wb_temp = None
    wb_wind = None
    wb_solar = None
    wb_rh = None
    ha_url, ha_tok = None, None
    try:
        ha_url, ha_tok = get_ha_client_config()
        if ha_tok and ha_url:
            ctx_ssl = ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = ssl.CERT_NONE

            entities_to_query = [
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature", "temp"),
                ("sensor.temperatuur_buiten", "temp_fallback"),
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_wind_speed", "wind"),
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_solar_radiation", "solar"),
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_humidity", "rh")
            ]
            for ent, var in entities_to_query:
                try:
                    req = urllib.request.Request(f"{ha_url}/api/states/{ent}", headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=1.5, context=ctx_ssl) as r:
                        st = json.loads(r.read().decode())
                        val = float(st.get("state", 0.0))
                        if var == "temp" and wb_temp is None:
                            wb_temp = val
                        elif var == "temp_fallback" and wb_temp is None:
                            wb_temp = val
                        elif var == "wind" and wb_wind is None:
                            wb_wind = val / 3.6  # km/h to m/s
                        elif var == "solar" and wb_solar is None:
                            wb_solar = val  # W/m2
                        elif var == "rh" and wb_rh is None:
                            wb_rh = val
                except Exception:
                    pass
    except Exception:
        pass

    global _GLOBAL_WEATHER_FORECAST_CACHE
    m_data = None
    try:
        if _GLOBAL_WEATHER_FORECAST_CACHE.get("m_data") and (time.time() - _GLOBAL_WEATHER_FORECAST_CACHE.get("ts", 0)) < 900.0:
            m_data = _GLOBAL_WEATHER_FORECAST_CACHE["m_data"]
        else:
            w_url = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.2320&hourly=temperature_2m,shortwave_radiation,wind_speed_10m,relative_humidity_2m&timezone=Europe%2FAmsterdam&forecast_days=2"
            req_m = urllib.request.Request(w_url, headers={"User-Agent": "OpenHEMS/1.0"})
            with urllib.request.urlopen(req_m, timeout=4) as r_m:
                m_data = json.loads(r_m.read().decode())
                _GLOBAL_WEATHER_FORECAST_CACHE = {"m_data": m_data, "ts": time.time()}
    except Exception as e:
        if _GLOBAL_WEATHER_FORECAST_CACHE.get("m_data"):
            m_data = _GLOBAL_WEATHER_FORECAST_CACHE["m_data"]
            print(f"[Open HEMS Weather] Externe weer-API niet bereikbaar ({e}); hergebruikt gecachte voorspelling.")
        else:
            print(f"Warning fetching anchored weather forecast: {e}")

    if m_data and "hourly" in m_data:
        try:
            h_times = m_data["hourly"]["time"]
            h_temps = m_data["hourly"]["temperature_2m"]
            h_rads = m_data["hourly"]["shortwave_radiation"]
            h_winds = m_data["hourly"]["wind_speed_10m"]
            h_rhs = m_data["hourly"]["relative_humidity_2m"]

            start_hour_iso = base_dt.strftime("%Y-%m-%dT%H:00")
            idx_start = h_times.index(start_hour_iso) if start_hour_iso in h_times else 0

            raw_cur_temp = float(h_temps[idx_start]) if idx_start < len(h_temps) else 15.0
            delta_temp = (wb_temp - raw_cur_temp) if wb_temp is not None else 0.0

            raw_cur_wind = float(h_winds[idx_start]) if idx_start < len(h_winds) else 3.0
            delta_wind = (wb_wind - raw_cur_wind) if wb_wind is not None else 0.0

            raw_cur_solar = float(h_rads[idx_start]) if idx_start < len(h_rads) else 0.0
            delta_solar = (wb_solar - raw_cur_solar) if wb_solar is not None else 0.0

            tau_hours = 3.0  # Smooth assimilation window of 3 hours
            for offset_h in range(len(h_times) - idx_start):
                idx = idx_start + offset_h
                t_str = h_times[idx]
                k_t = t_str.replace('T', ' ')[:13] + ':00'

                weight = math.exp(-offset_h / tau_hours)

                nudge_temp = round(float(h_temps[idx]) + delta_temp * weight, 1)
                nudge_wind = round(max(0.0, float(h_winds[idx]) + delta_wind * weight), 1)
                nudge_solar = round(max(0.0, float(h_rads[idx]) + delta_solar * weight), 1)
                nudge_rh = round(float(h_rhs[idx]), 1)

                temp_map[k_t] = nudge_temp
                wind_map[k_t] = nudge_wind
                solar_map[k_t] = nudge_solar
                rh_map[k_t] = nudge_rh
        except Exception as e_proc:
            print(f"Warning processing Open-Meteo data: {e_proc}")

    return temp_map, solar_map, wind_map, rh_map


def fetch_recent_telemetry_history(is_15m: bool, base_dt: datetime) -> list:
    """Fetches real historical telemetry for the 1 hour immediately preceding the forecast horizon."""
    num_slots = 4 if is_15m else 1
    step_mins = 15 if is_15m else 60
    start_dt = base_dt - timedelta(minutes=num_slots * step_mins)

    start_utc = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
    end_utc = base_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
    bucket = "15m" if is_15m else "1h"

    en_map, tank_map, out_map = {}, {}, {}
    try:
        sec = load_secrets()
        pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

        q = f"""
        SELECT mean("solar_w")/1000.0 as solar, mean("total_house_w")/1000.0 as house, mean("unallocated_w")/1000.0 as unalloc, mean("heatpump_w")/1000.0 as hp
        FROM "energy_telemetry" 
        WHERE time >= '{start_utc}' AND time < '{end_utc}'
        GROUP BY time({bucket}) fill(linear);
        SELECT mean("temperature_c") as tank_temp
        FROM "energy_telemetry" 
        WHERE "device_id" = 'dhw_tank' AND time >= '{start_utc}' AND time < '{end_utc}'
        GROUP BY time({bucket}) fill(linear);
        SELECT mean("temperature_c") as outdoor_temp
        FROM "energy_telemetry" 
        WHERE "device_id" = 'outdoor_weather' AND time >= '{start_utc}' AND time < '{end_utc}'
        GROUP BY time({bucket}) fill(linear);
        """
        url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q)}"
        with urllib.request.urlopen(url, timeout=4) as r:
            res = json.loads(r.read().decode())

        res_list = res.get("results", [])
        if len(res_list) > 0 and "series" in res_list[0]:
            en_map = {row[0]: row[1:] for row in res_list[0]["series"][0].get("values", [])}
        if len(res_list) > 1 and "series" in res_list[1]:
            tank_map = {row[0]: row[1] for row in res_list[1]["series"][0].get("values", [])}
        if len(res_list) > 2 and "series" in res_list[2]:
            out_map = {row[0]: row[1] for row in res_list[2]["series"][0].get("values", [])}
    except Exception as e:
        print(f"Warning fetching history telemetry: {e}")

    # Read live tank and outdoor temperatures from HA to avoid artificial cliffs at the boundary
    cur_tank_default = 42.8
    cur_room_default = 24.5
    cur_outdoor_default = 17.2
    live_solar_default = 0.0
    try:
        sm = get_ha_states_map()
        v = float(sm.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {}).get("state", 42.8))
        if 20.0 <= v <= 75.0:
            cur_tank_default = v
        cl_t = sm.get("climate.woonkamer_climate_daikin", {}).get("attributes", {}).get("current_temperature")
        if cl_t is not None and 15.0 <= float(cl_t) <= 35.0:
            cur_room_default = float(cl_t)
        else:
            r_t = float(sm.get("sensor.hc_sensors_temperature_room", {}).get("state", 24.5))
            if 15.0 <= r_t <= 35.0:
                cur_room_default = r_t

        for s_id in ["sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature", "sensor.temperatuur_buiten"]:
            s_st = sm.get(s_id, {}).get("state")
            if s_st and s_st not in ["unavailable", "unknown"]:
                try:
                    cur_outdoor_default = float(s_st)
                    break
                except ValueError:
                    pass

        s_pow = sm.get("sensor.zonnepanelen_power_avg_5_minutes", {}).get("state")
        if s_pow and s_pow not in ["unavailable", "unknown"]:
            try:
                live_solar_default = round(abs(float(s_pow)) / 1000.0, 3)
            except ValueError:
                pass
    except Exception:
        pass

    history_pts = []
    last_tank = cur_tank_default
    for i in range(num_slots):
        slot_dt = start_dt + timedelta(minutes=step_mins * i)
        slot_utc_str = slot_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")

        en_row = en_map.get(slot_utc_str, [0.0, 0.45, 0.45, 0.033])
        solar_kw = round(max(0.0, float(en_row[0] or 0.0)), 3)
        if solar_kw == 0.0 and i == (num_slots - 1) and live_solar_default > 0:
            solar_kw = live_solar_default

        house_kw = round(max(0.0, float(en_row[1] or 0.45)), 3)
        unalloc_kw = round(max(0.0, float(en_row[2] or 0.35)), 3)
        hp_kw = round(max(0.0, float(en_row[3] or 0.033)), 3)

        tank_t = tank_map.get(slot_utc_str)
        if tank_t is not None:
            last_tank = round(float(tank_t), 1)
        tank_t = last_tank

        out_t = out_map.get(slot_utc_str)
        out_t = round(float(out_t), 1) if out_t is not None else cur_outdoor_default

        dhw_kw = hp_kw if hp_kw > 0.5 else 0.0
        cv_kw = hp_kw if hp_kw > 0.5 and dhw_kw == 0.0 else 0.0

        lbl = slot_dt.strftime("%H:%M" if is_15m else "%H:00")
        k_full = slot_dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")

        history_pts.append({
            "idx": -num_slots + i,
            "dt": slot_dt,
            "key": k_full,
            "label": lbl,
            "is_history": True,
            "solar_kw": solar_kw,
            "total_house_kw": house_kw,
            "unallocated_kw": unalloc_kw,
            "heatpump_kw": hp_kw,
            "dhw_kw": dhw_kw,
            "heating_kw": cv_kw,
            "tank_temp_c": tank_t,
            "outdoor_temp_c": out_t,
            "indoor_temp_c": cur_room_default
        })

    return history_pts


EPEX_CACHE_FILE = Path("/config/open_hems_epex_cache.json")
_EPEX_CACHE_DATA: Dict[str, Any] = {}
_LAST_EPEX_POLL_TS = 0.0

STROOMVOORSPELLER_CACHE_FILE = Path("/config/open_hems_stroomvoorspeller_cache.json")
_STROOMVOORSPELLER_CACHE_DATA: Dict[str, Any] = {}
_LAST_STROOMVOORSPELLER_POLL_TS = 0.0
_TARIFF_SOURCES_MAP: Dict[str, str] = {}


def _load_stroomvoorspeller_cache() -> None:
    global _STROOMVOORSPELLER_CACHE_DATA
    if not _STROOMVOORSPELLER_CACHE_DATA and STROOMVOORSPELLER_CACHE_FILE.exists():
        try:
            with open(STROOMVOORSPELLER_CACHE_FILE, "r", encoding="utf-8") as f:
                _STROOMVOORSPELLER_CACHE_DATA = json.load(f)
        except Exception:
            _STROOMVOORSPELLER_CACHE_DATA = {}


def _save_stroomvoorspeller_cache() -> None:
    try:
        tmp = f"{STROOMVOORSPELLER_CACHE_FILE}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_STROOMVOORSPELLER_CACHE_DATA, f, indent=2)
        os.replace(tmp, STROOMVOORSPELLER_CACHE_FILE)
    except Exception as e:
        print(f"Warning saving Stroomvoorspeller cache: {e}")


def fetch_stroomvoorspeller_tariffs_cached(force: bool = False) -> Dict[str, Any]:
    """
    Fetches and caches up to 7-day ahead hourly electricity price predictions from Stroomvoorspeller.nl.
    Open Data JSON endpoint: https://stroomvoorspeller.nl/data/forecast.json
    Refreshes at most once every 3 hours unless force=True.
    """
    global _LAST_STROOMVOORSPELLER_POLL_TS, _STROOMVOORSPELLER_CACHE_DATA
    _load_stroomvoorspeller_cache()
    now_ts = time.time()

    need_poll = force or not _STROOMVOORSPELLER_CACHE_DATA or (now_ts - _LAST_STROOMVOORSPELLER_POLL_TS >= 10800.0)

    if need_poll:
        _LAST_STROOMVOORSPELLER_POLL_TS = now_ts
        try:
            url = "https://stroomvoorspeller.nl/data/forecast.json"
            req = urllib.request.Request(url, headers={"User-Agent": "OpenHEMS/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
                forecasts = data.get("forecasts", [])
                if forecasts:
                    parsed_map = {}
                    for fc in forecasts:
                        t_str = fc.get("time")
                        if not t_str:
                            continue
                        dt = datetime.fromisoformat(t_str).astimezone(ZoneInfo("Europe/Amsterdam"))
                        k_h = dt.strftime("%Y-%m-%d %H:00")
                        base_eur = round(float(fc.get("predicted", 0.0)) / 1000.0, 4)
                        all_in_eur = round((base_eur + 0.0121 + 0.11085) * 1.21, 4)
                        export_eur = round(max(0.0, base_eur - 0.00605), 4)
                        parsed_map[k_h] = {
                            "dt": dt.isoformat(),
                            "base": base_eur,
                            "all_in": all_in_eur,
                            "export": export_eur,
                            "regime": fc.get("regime", "normaal"),
                            "p_negative": fc.get("P_negative", 0.0)
                        }
                    _STROOMVOORSPELLER_CACHE_DATA = {
                        "generated_at": data.get("generated_at"),
                        "horizon_end": data.get("horizon_end"),
                        "hours": parsed_map
                    }
                    _save_stroomvoorspeller_cache()
                    print(f"[Open HEMS] Stroomvoorspeller 7-daagse prijsprognose opgehaald ({len(parsed_map)} uren).")
        except Exception as e:
            print(f"[Open HEMS] Ophalen Stroomvoorspeller data gaf fout: {e}")

    return _STROOMVOORSPELLER_CACHE_DATA


def get_tariff_sources_map() -> Dict[str, str]:
    """Returns the mapping of timestamp -> 'epex' | 'stroomvoorspeller'."""
    global _TARIFF_SOURCES_MAP
    return dict(_TARIFF_SOURCES_MAP)


def _load_epex_cache() -> None:
    global _EPEX_CACHE_DATA
    if not _EPEX_CACHE_DATA and EPEX_CACHE_FILE.exists():
        try:
            with open(EPEX_CACHE_FILE, "r", encoding="utf-8") as f:
                _EPEX_CACHE_DATA = json.load(f)
        except Exception:
            _EPEX_CACHE_DATA = {}


def _save_epex_cache() -> None:
    try:
        tmp = f"{EPEX_CACHE_FILE}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_EPEX_CACHE_DATA, f, indent=2)
        os.replace(tmp, EPEX_CACHE_FILE)
    except Exception as e:
        print(f"Warning saving EPEX cache: {e}")


def get_epex_tariffs_cached(is_15m: bool = True, force: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, float], Dict[str, float]]:
    """
    Authoritative EPEX Day-Ahead price caching & polling manager:
    - Today's prices are served instantly from disk/memory cache.
    - Tomorrow's prices are polled every 5 minutes between 12:30 and 14:00 (active auction release window).
    - Fallback: polls every 15 minutes between 14:00 and 16:00, then hourly after 16:00.
    - Once tomorrow's prices are retrieved, polling stops completely until tomorrow 12:30.
    - Returns (raw_price_list, map_all_in, map_base).
    """
    global _LAST_EPEX_POLL_TS, _EPEX_CACHE_DATA
    _load_epex_cache()
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    today_key = now_ams.strftime("%Y-%m-%d")
    tomorrow_key = (now_ams + timedelta(days=1)).strftime("%Y-%m-%d")
    cache_type = "15m" if is_15m else "1h"
    interval_str = "INTERVAL_QUARTER" if is_15m else "INTERVAL_HOUR"

    # Purge keys older than yesterday to keep cache lean
    yesterday_key = (now_ams - timedelta(days=1)).strftime("%Y-%m-%d")
    _EPEX_CACHE_DATA = {k: v for k, v in _EPEX_CACHE_DATA.items() if k >= yesterday_key}

    now_ts = time.time()
    hour = now_ams.hour
    minute = now_ams.minute
    time_float = hour + minute / 60.0  # e.g. 12:30 = 12.5, 14:00 = 14.0

    need_today = (today_key not in _EPEX_CACHE_DATA or cache_type not in _EPEX_CACHE_DATA[today_key])
    has_tomorrow = (tomorrow_key in _EPEX_CACHE_DATA and cache_type in _EPEX_CACHE_DATA[tomorrow_key] and len(_EPEX_CACHE_DATA[tomorrow_key][cache_type].get("all_in", [])) >= (96 if is_15m else 24))
    need_tomorrow = False
    if force or not has_tomorrow:
        if force:
            need_tomorrow = True
        elif 12.5 <= time_float < 14.0:
            if (now_ts - _LAST_EPEX_POLL_TS) >= 300.0:  # Every 5 min between 12:30 and 14:00
                need_tomorrow = True
        elif 14.0 <= time_float < 16.0:
            if (now_ts - _LAST_EPEX_POLL_TS) >= 900.0:  # Every 15 min between 14:00 and 16:00
                need_tomorrow = True
        elif time_float >= 16.0:
            if (now_ts - _LAST_EPEX_POLL_TS) >= 3600.0: # Every 60 min after 16:00 until published
                need_tomorrow = True

    dates_to_fetch = []
    if need_today:
        dates_to_fetch.append((today_key, now_ams.strftime("%d-%m-%Y")))
    if need_tomorrow:
        dates_to_fetch.append((tomorrow_key, (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")))

    if dates_to_fetch:
        _LAST_EPEX_POLL_TS = now_ts
        dirty = False
        for date_key, d_str in dates_to_fetch:
            try:
                url_p = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval={interval_str}"
                req_p = urllib.request.Request(url_p, headers={"User-Agent": "OpenHEMS/1.0"})
                with urllib.request.urlopen(req_p, timeout=6) as r_p:
                    res_p = json.loads(r_p.read().decode())
                    all_in_items = res_p.get("all_in_with_vat", [])
                    base_items = res_p.get("base", [])
                    if len(all_in_items) >= (96 if is_15m else 24):
                        _EPEX_CACHE_DATA.setdefault(date_key, {})[cache_type] = {
                            "all_in": [{"start": it["start"], "val": float(it.get("price", {}).get("value", 0.25))} for it in all_in_items],
                            "base": [{"start": it["start"], "val": float(it.get("price", {}).get("value", 0.12))} for it in base_items]
                        }
                        dirty = True
                        if date_key == tomorrow_key:
                            print(f"[Open HEMS] EPEX Day-Ahead prijzen voor morgen ({tomorrow_key}) binnengehaald ({len(all_in_items)} slots). Polling stopt tot morgen 12:30.")
            except Exception as e:
                print(f"[Open HEMS] Polling EPEX tarieven voor {d_str} gaf nog geen data: {e}")
        if dirty:
            _save_epex_cache()

    raw_prices = []
    map_all_in = {}
    map_base = {}
    _TARIFF_SOURCES_MAP.clear()

    # 1. Base Layer (Tier 2): Stroomvoorspeller 7-day model predictions
    sv_data = fetch_stroomvoorspeller_tariffs_cached()
    sv_hours = sv_data.get("hours", {}) if isinstance(sv_data, dict) else {}
    for k_h, it in sv_hours.items():
        if is_15m:
            for m in [0, 15, 30, 45]:
                k_q = f"{k_h[:13]}:{m:02d}"
                map_all_in[k_q] = it["all_in"]
                map_base[k_q] = it["base"]
                _TARIFF_SOURCES_MAP[k_q] = "stroomvoorspeller"
        else:
            map_all_in[k_h] = it["all_in"]
            map_base[k_h] = it["base"]
            _TARIFF_SOURCES_MAP[k_h] = "stroomvoorspeller"

    # 2. Authoritative Top Layer (Tier 1): EPEX Day-Ahead ALWAYS overwrites Stroomvoorspeller
    for d_k in [today_key, tomorrow_key]:
        if d_k in _EPEX_CACHE_DATA and cache_type in _EPEX_CACHE_DATA[d_k]:
            blob = _EPEX_CACHE_DATA[d_k][cache_type]
            for it in blob.get("all_in", []):
                dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Amsterdam"))
                k_dt = dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                p_val = round(it["val"], 4)
                map_all_in[k_dt] = p_val
                _TARIFF_SOURCES_MAP[k_dt] = "epex"  # EPEX WINS!
                raw_prices.append({"dt": dt, "price": p_val, "source": "epex"})
            for it in blob.get("base", []):
                dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Amsterdam"))
                k_dt = dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                map_base[k_dt] = round(it["val"], 4)

    return raw_prices, map_all_in, map_base
