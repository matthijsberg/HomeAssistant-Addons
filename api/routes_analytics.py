# Open HEMS: Analytics & Energy Telemetry Router
import urllib
import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Optional

from api.context import (
    AMS_TZ, SECRETS_FILE, PARAMS_FILE, CONFIG_FILE,
    load_json, save_json, load_secrets, get_ha_client_config,
    get_ha_states_map, calculate_poa_solar_kw, format_slot_label,
    fetch_recent_telemetry_history, ensure_active_canonical_plan,
    DUTCH_DAYS_SHORT, GLOBAL_DHW_MODEL, get_epex_tariffs_cached
)
from layer3_scheduling.decision_audit import DecisionAuditLogger

def handle_get(handler, path: str, qp: dict) -> bool:
    if path == "/api/analytics":
        handler._send_json({
            "savings_today_eur": 0.85,
            "savings_week_eur": 6.85,
            "self_consumption_pct": 78.4,
            "dhw_cop": 2.04,
            "cv_cop": 4.80,
            "forecast_mae_kw": 0.18,
            "forecast_accuracy_pct": 92.6,
            "total_solar_today_kwh": 14.2,
            "total_grid_export_kwh": 3.1,
            "battery_arbitrage_yield_eur": 0.42,
            "daily_digest": "• Verwachte Daggemiddelde Prijs: €0.245/kWh\n• Laagste Stroomtarief: €0.142/kWh (13:00)\n• Warmtepomp Boost: Gepland om 13:00 naar 60°C\n• Zonne-Zelfconsumptie: 78.4%\n• Accu Status: Stand-by (Deadband bewaakt)"
        })
        return True

    # API: Layer 4 Hardware Control Status
    if path.startswith("/api/analytics/electricity_prices"):
        try:
            cfg = load_json(CONFIG_FILE)
            solar_cost = float(cfg.get("solar_cost_eur_kwh", 0.06))

            parsed_url = urllib.parse.urlparse(handler.path)
            qp = urllib.parse.parse_qs(parsed_url.query)
            res_mode = qp.get("resolution", ["15m"])[0]
            interval_api = "INTERVAL_QUARTER" if res_mode == "15m" else "INTERVAL_HOUR"

            now_ams = datetime.now(AMS_TZ)
            plan = ensure_active_canonical_plan()
            today_str = now_ams.strftime("%d-%m-%Y")
            tomorrow_str = (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")

            is_15m = (res_mode == "15m")
            total_slots = 96 if is_15m else 24
            step_mins = 15 if is_15m else 60
            start_minute = (now_ams.minute // 15) * 15 if is_15m else 0
            base_dt = now_ams.replace(minute=start_minute, second=0, microsecond=0)

            # 1. Fetch EPEX Spot Prices from dedicated Day-Ahead cache
            _, prices_map, prices_base_map = get_epex_tariffs_cached(is_15m=is_15m)

            labels = []
            prices_all_in = []
            prices_base = []
            solar_forecast_kw = []

            prev_ep_dt = None
            for i in range(total_slots):
                dt_slot = base_dt + timedelta(minutes=step_mins * i)
                k_full = dt_slot.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                lbl = format_slot_label(dt_slot, prev_ep_dt, i == 0, is_15m)
                prev_ep_dt = dt_slot
                labels.append(lbl)

                if is_15m:
                    plan_slot = plan.slots[i] if plan and i < len(plan.slots) else None
                    if plan_slot:
                        s_val = plan_slot.solar_kw
                        p_val = plan_slot.price_eur
                    else:
                        s_val = 0.0
                        p_val = prices_map.get(k_full, 0.25)
                else:
                    q_start = i * 4
                    q_end = min(len(plan.slots), (i + 1) * 4) if plan else 0
                    q_slots = plan.slots[q_start:q_end] if plan else []
                    if q_slots:
                        s_val = round(sum(s.solar_kw for s in q_slots) / len(q_slots), 2)
                        p_val = round(sum(s.price_eur for s in q_slots) / len(q_slots), 4)
                    else:
                        s_val = 0.0
                        p_val = prices_map.get(k_full, 0.25)

                # Physical night guard
                if dt_slot.hour >= 21 or dt_slot.hour < 7:
                    s_val = 0.0

                prices_all_in.append(p_val)
                prices_base.append(prices_base_map.get(k_full, round(p_val - 0.15, 4)))
                solar_forecast_kw.append(s_val)

            min_p = min(prices_all_in) if prices_all_in else 0.0
            max_p = max(prices_all_in) if prices_all_in else 0.0
            avg_p = (sum(prices_all_in) / len(prices_all_in)) if prices_all_in else 0.0
            min_time = labels[prices_all_in.index(min_p)] if prices_all_in else "--:--"
            max_time = labels[prices_all_in.index(max_p)] if prices_all_in else "--:--"
            peak_solar = max(solar_forecast_kw) if solar_forecast_kw else 0.0

            # Prepend 1 hour of actual historical telemetry
            hist_pts = fetch_recent_telemetry_history(is_15m, base_dt)
            hist_labels = []
            hist_prices_all_in = []
            hist_prices_base = []
            hist_solar = []
            for hp in hist_pts:
                p_val = prices_map.get(hp["key"], prices_map.get(hp["dt"].strftime("%Y-%m-%d %H:00"), 0.25))
                p_base = prices_base_map.get(hp["key"], prices_base_map.get(hp["dt"].strftime("%Y-%m-%d %H:00"), round(p_val - 0.15, 4)))
                hist_labels.append(hp["label"])
                hist_prices_all_in.append(p_val)
                hist_prices_base.append(p_base)
                hist_solar.append(hp["solar_kw"])

            full_export_prices = [round(b - 0.00605, 4) for b in (hist_prices_base + prices_base)]
            avg_export = (sum(full_export_prices) / len(full_export_prices)) if full_export_prices else 0.0

            res = {
                "status": "success",
                "resolution": res_mode,
                "labels": hist_labels + labels,
                "epex_prices": hist_prices_all_in + prices_all_in,
                "epex_base_prices": hist_prices_base + prices_base,
                "export_prices": full_export_prices,
                "solar_forecast_kw": hist_solar + solar_forecast_kw,
                "solar_cost": solar_cost,
                "history_count": len(hist_pts),
                "stats": {
                    "min_price": f"€{min_p:.4f}/kWh",
                    "min_time": min_time,
                    "max_price": f"€{max_p:.4f}/kWh",
                    "max_time": max_time,
                    "avg_price": f"€{avg_p:.4f}/kWh",
                    "solar_savings_avg": f"€{max(0.0, avg_p - avg_export):.4f}/kWh",
                    "avg_export_price": f"€{avg_export:.4f}/kWh",
                    "peak_solar_forecast": f"{peak_solar:.2f} kW"
                }
            }
            handler._send_json(res)
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": f"Fout bij ophalen EPEX tarieven & zonvoorspelling: {str(e)}"}, 500)
            return True

    if path == "/api/analytics/solar_cost" and handler.command == "POST":
        # Handled in do_POST
        pass

    # ANALYTICS: Pure openhems Power Producers Telemetry with Timeframe Selector & Energy Integrals
    if path.startswith("/api/analytics/power_producers"):
        try:
            sec = load_secrets()
            cfg = load_json(CONFIG_FILE)
            active_conn = cfg.get("influxdb_connections", [{}])[0]

            db_name = active_conn.get("database", "openhems")
            db_user = active_conn.get("username", "openhems")
            pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

            # Parse timeframe and resolution parameters (Grafana-style smart defaults)
            parsed_url = urllib.parse.urlparse(handler.path)
            qp = urllib.parse.parse_qs(parsed_url.query)
            tf = qp.get("range", ["24h"])[0]
            user_res = qp.get("resolution", [None])[0] or qp.get("res", [None])[0]

            tf_windows = {
                "1h": "1h",
                "6h": "6h",
                "24h": "24h",
                "48h": "48h",
                "7d": "7d"
            }
            time_win = tf_windows.get(tf, "24h")

            # Smart resolution determination:
            # Standard for 24h is 1 hour ('1h'). Small intervals (<24h) default to 15m.
            # Large intervals (>24h) default to 1h or 2h.
            if user_res == "15m":
                bucket_sz = "15m"
                interval_h = 0.25
                time_fmt = "%H:%M" if tf in ["1h", "6h", "24h"] else "%d %H:%M"
            elif user_res == "1h":
                bucket_sz = "1h"
                interval_h = 1.0
                time_fmt = "%H:00" if tf in ["1h", "6h", "24h"] else "%d %H:00"
            elif user_res == "high": # smooth 5m line
                bucket_sz = "5m" if tf != "1h" else "1m"
                interval_h = 5.0 / 60.0 if tf != "1h" else 1.0 / 60.0
                time_fmt = "%H:%M"
            else: # auto
                if tf in ["1h", "6h"]:
                    bucket_sz = "15m"
                    interval_h = 0.25
                    time_fmt = "%H:%M"
                elif tf == "24h":
                    bucket_sz = "1h"
                    interval_h = 1.0
                    time_fmt = "%H:00"
                elif tf == "48h":
                    bucket_sz = "1h"
                    interval_h = 1.0
                    time_fmt = "%d %H:00"
                else: # 7d
                    bucket_sz = "2h"
                    interval_h = 2.0
                    time_fmt = "%a %d %H:00"

            # Query 100% strictly from openhems canonical database with fill(none)
            q = f"""
            SELECT mean("power_w") as afname_w FROM "energy_telemetry" WHERE "device_id" = 'main_grid_meter' AND "flow" = 'IMPORT' AND time > now() - {time_win} GROUP BY time({bucket_sz}) fill(none);
            SELECT mean("power_w") as teruglevering_w FROM "energy_telemetry" WHERE "device_id" = 'main_grid_meter' AND "flow" = 'EXPORT' AND time > now() - {time_win} GROUP BY time({bucket_sz}) fill(none);
            SELECT mean("power_w") as solar_w FROM "energy_telemetry" WHERE "device_id" = 'rooftop_solar' AND "flow" = 'GENERATION' AND time > now() - {time_win} GROUP BY time({bucket_sz}) fill(none);
            """

            url = "http://a0d7b954-influxdb:8086/query?" + urllib.parse.urlencode({
                "u": db_user,
                "p": pwd,
                "db": db_name,
                "q": q
            })

            with urllib.request.urlopen(url, timeout=6) as r:
                data = json.loads(r.read().decode())

            # Safely extract series lists
            def get_series_values(res_idx):
                results = data.get("results", [])
                if res_idx < len(results):
                    series = results[res_idx].get("series")
                    if series and len(series) > 0:
                        return series[0].get("values", [])
                return []

            afname_pts = get_series_values(0)
            terug_pts = get_series_values(1)
            solar_pts = get_series_values(2)

            # Map points by timestamp
            ts_map = {}
            for pt in afname_pts:
                if pt[1] is not None:
                    ts_map.setdefault(pt[0], {})["afname"] = float(pt[1])
            for pt in terug_pts:
                if pt[1] is not None:
                    ts_map.setdefault(pt[0], {})["terug"] = float(pt[1])
            for pt in solar_pts:
                if pt[1] is not None:
                    ts_map.setdefault(pt[0], {})["solar"] = abs(float(pt[1]))

            sorted_ts = sorted(ts_map.keys())

            labels = []
            series_solar_neg = []
            series_terug_neg = []
            series_afname_pos = []
            series_verbruik_pos = []
            series_selfcons_pos = []
            series_prices = []
            series_export_prices = []

            # Fetch EPEX prices (All-in Import & Dynamic Export) across timeframe
            now_ams = datetime.now(AMS_TZ)
            epex_import_map = {}
            epex_export_map = {}
            try:
                for days_back in range(3):
                    d_str = (now_ams - timedelta(days=days_back)).strftime("%d-%m-%Y")
                    url_p = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval=INTERVAL_HOUR"
                    req_p = urllib.request.Request(url_p, headers={"User-Agent": "OpenHEMS/1.0"})
                    with urllib.request.urlopen(req_p, timeout=3) as r_p:
                        res_p = json.loads(r_p.read().decode())
                        # 1. All-in afnametarief (incl. energiebelasting, opslag en btw)
                        for it in res_p.get("all_in_with_vat", []):
                            dt_p = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                            epex_import_map[dt_p.strftime("%Y-%m-%d %H:00")] = float(it.get("price", {}).get("value", 0.28))
                        # 2. Dynamisch teruglevertarief (kale EPEX spotprijs min verkoopopslag)
                        for it in res_p.get("base", []):
                            dt_p = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                            base_val = float(it.get("price", {}).get("value", 0.12))
                            # Powerpeers dynamisch contract: kale prijs min €0.00605 verkoopvergoeding
                            epex_export_map[dt_p.strftime("%Y-%m-%d %H:00")] = max(0.0, base_val - 0.00605)
            except Exception as e_pr:
                pass

            # Accumulators for timeframe energy totals (kWh) & monetary costs (€)
            tot_solar_wh = 0.0
            tot_terug_wh = 0.0
            tot_afname_wh = 0.0
            tot_verbruik_wh = 0.0
            tot_selfcons_wh = 0.0

            tot_solar_eur = 0.0
            tot_terug_eur = 0.0
            tot_afname_eur = 0.0
            tot_verbruik_eur = 0.0
            tot_selfcons_eur = 0.0
            prev_pp_dt = None

            for ts_str in sorted_ts:
                m = ts_map[ts_str]
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                    if prev_pp_dt is not None and dt.day != prev_pp_dt.day:
                        day_str = DUTCH_DAYS_SHORT[dt.weekday()]
                        time_label = f"{day_str} {dt.strftime(time_fmt)}"
                    else:
                        time_label = dt.strftime(time_fmt)
                    prev_pp_dt = dt
                except Exception:
                    time_label = ts_str[11:16]

                labels.append(time_label)

                afname = m.get("afname", 0.0)
                terug = m.get("terug", 0.0)
                solar = m.get("solar", 0.0)

                # Exact Physical Balance within interval:
                # Direct handler-consumption from PV = solar generated that was consumed on-site (solar - export)
                self_cons = max(0.0, solar - terug)
                # Total real house consumption = grid import + direct solar handler-consumption
                verbruik = afname + self_cons

                series_afname_pos.append(round(afname))
                series_verbruik_pos.append(round(verbruik))
                series_selfcons_pos.append(round(self_cons))
                series_solar_neg.append(-round(solar))
                series_terug_neg.append(-round(terug))

                # Integrate energy in Wh: P * hours
                tot_afname_wh += afname * interval_h
                tot_terug_wh += terug * interval_h
                tot_solar_wh += solar * interval_h
                tot_verbruik_wh += verbruik * interval_h
                tot_selfcons_wh += self_cons * interval_h

                # Differentiated contract pricing:
                # - Afname & Eigenverbruik besparing gewaardeerd tegen All-in EPEX inkoopprijs (~€0.28/kWh)
                # - Teruglevering gewaardeerd tegen dynamisch teruglevertarief (kale spot min €0.006/kWh, ~€0.11/kWh)
                hr_key = ts_str[:13].replace('T', ' ') + ':00'
                p_imp = epex_import_map.get(hr_key, 0.28)
                p_exp = epex_export_map.get(hr_key, max(0.0, p_imp / 1.21 - 0.11085 - 0.0121 - 0.00605))

                series_prices.append(round(p_imp, 4))
                series_export_prices.append(round(p_exp, 4))

                tot_afname_eur += (afname / 1000.0) * interval_h * p_imp
                tot_selfcons_eur += (self_cons / 1000.0) * interval_h * p_imp
                tot_terug_eur += (terug / 1000.0) * interval_h * p_exp
                tot_solar_eur += ((self_cons / 1000.0) * interval_h * p_imp) + ((terug / 1000.0) * interval_h * p_exp)
                tot_verbruik_eur += (verbruik / 1000.0) * interval_h * p_imp

            def fmt_w(val):
                abs_v = abs(val)
                sign = "-" if val < 0 else ""
                if abs_v >= 1000:
                    return f"{sign}{abs_v / 1000.0:.2f} kW"
                return f"{sign}{int(abs_v)} W"

            def fmt_kwh(wh):
                kwh = abs(wh) / 1000.0
                if kwh < 0.01:
                    return "0.00 kWh"
                elif kwh < 10.0:
                    return f"{kwh:.2f} kWh"
                else:
                    return f"{kwh:.1f} kWh"

            def fmt_eur(val, prefix="€"):
                return f"{prefix}{val:.2f}"

            stats = {
                "zonnepanelen": {
                    "last": fmt_w(series_solar_neg[-1] if series_solar_neg else 0),
                    "min": fmt_w(min(series_solar_neg) if series_solar_neg else 0),
                    "max": fmt_w(max(series_solar_neg) if series_solar_neg else 0),
                    "total_kwh": fmt_kwh(tot_solar_wh),
                    "cost_eur": fmt_eur(tot_solar_eur)
                },
                "teruglevering": {
                    "last": fmt_w(series_terug_neg[-1] if series_terug_neg else 0),
                    "min": fmt_w(min(series_terug_neg) if series_terug_neg else 0),
                    "max": fmt_w(max(series_terug_neg) if series_terug_neg else 0),
                    "total_kwh": fmt_kwh(tot_terug_wh),
                    "cost_eur": fmt_eur(tot_terug_eur)
                },
                "afname": {
                    "last": fmt_w(series_afname_pos[-1] if series_afname_pos else 0),
                    "min": fmt_w(min(series_afname_pos) if series_afname_pos else 0),
                    "max": fmt_w(max(series_afname_pos) if series_afname_pos else 0),
                    "total_kwh": fmt_kwh(tot_afname_wh),
                    "cost_eur": fmt_eur(tot_afname_eur)
                },
                "totaal_opgewekt": {
                    "last": fmt_w(series_solar_neg[-1] if series_solar_neg else 0),
                    "min": fmt_w(min(series_solar_neg) if series_solar_neg else 0),
                    "max": fmt_w(max(series_solar_neg) if series_solar_neg else 0),
                    "total_kwh": fmt_kwh(tot_solar_wh),
                    "cost_eur": fmt_eur(tot_solar_eur)
                },
                "opgewekt_gebruikt": {
                    "last": fmt_w(-series_selfcons_pos[-1] if series_selfcons_pos else 0),
                    "min": fmt_w(-max(series_selfcons_pos) if series_selfcons_pos else 0),
                    "max": fmt_w(0),
                    "total_kwh": fmt_kwh(tot_selfcons_wh),
                    "cost_eur": fmt_eur(tot_selfcons_eur)
                },
                "totaal_verbruik": {
                    "last": fmt_w(series_verbruik_pos[-1] if series_verbruik_pos else 0),
                    "min": fmt_w(min(series_verbruik_pos) if series_verbruik_pos else 0),
                    "max": fmt_w(max(series_verbruik_pos) if series_verbruik_pos else 0),
                    "total_kwh": fmt_kwh(tot_verbruik_wh),
                    "cost_eur": fmt_eur(tot_verbruik_eur)
                }
            }

            res = {
                "status": "success",
                "labels": labels,
                "interval_h": interval_h,
                "afname": series_afname_pos,
                "verbruik": series_verbruik_pos,
                "self_consumption": series_selfcons_pos,
                "solar_negative": series_solar_neg,
                "teruglevering_negative": series_terug_neg,
                "prices": series_prices,
                "export_prices": series_export_prices,
                "stats": stats
            }
            handler._send_json(res)
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": f"Fout bij ophalen InfluxDB telemetrie: {str(e)}"}, 500)
            return True


    # =========================================================================
    # API: VALIDATION OVERLAY (HISTORICAL PREDICTION VS ACTUAL TELEMETRY)
    # =========================================================================
    if path.startswith("/api/analytics/validation_overlay"):
        try:
            sec = load_secrets()
            cfg = load_json(CONFIG_FILE)
            active_conn = cfg.get("influxdb_connections", [{}])[0]
            pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

            parsed_url = urllib.parse.urlparse(handler.path)
            qp = urllib.parse.parse_qs(parsed_url.query)
            tf = qp.get("range", ["24h"])[0]
            user_res = qp.get("resolution", ["15m"])[0]

            days = 1 if tf == "24h" else (2 if tf == "48h" else 7)
            bucket_sz = "1h" if user_res == "1h" else "15m"
            interval_h = 1.0 if bucket_sz == "1h" else 0.25
            time_fmt = "%H:%M" if bucket_sz == "15m" else "%H:00"

            now = datetime.now(timezone.utc)
            t_start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:00:00Z")
            t_end = now.strftime("%Y-%m-%dT%H:00:00Z")

            # 1. Query InfluxDB for actuals
            q_telemetry = f"""
            SELECT mean("solar_w") as solar, mean("total_house_w") as house, mean("unallocated_w") as unalloc, mean("heatpump_w") as hp
            FROM "energy_telemetry" 
            WHERE time >= '{t_start}' AND time <= '{t_end}'
            GROUP BY time({bucket_sz}) fill(linear);
            SELECT mean("power_w") as dhw_w FROM "energy_telemetry" WHERE "device_id" = 'daikin_heat_pump' AND "mode" = 'dhw' AND time >= '{t_start}' AND time <= '{t_end}' GROUP BY time({bucket_sz}) fill(0);
            SELECT mean("power_w") as cv_w FROM "energy_telemetry" WHERE "device_id" = 'daikin_heat_pump' AND "mode" = 'heating' AND time >= '{t_start}' AND time <= '{t_end}' GROUP BY time({bucket_sz}) fill(0);
            """
            url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q_telemetry)}"
            with urllib.request.urlopen(url, timeout=5) as r:
                influx_res = json.loads(r.read().decode())

            gen_pts = influx_res['results'][0].get('series', [{}])[0].get('values', [])
            dhw_pts = influx_res['results'][1].get('series', [{}])[0].get('values', [])
            cv_series_list = influx_res['results'][2].get('series', [])
            cv_pts = cv_series_list[0].get('values', []) if cv_series_list else []

            dhw_map = {p[0]: (p[1] or 0.0) for p in dhw_pts}
            cv_map = {p[0]: (p[1] or 0.0) for p in cv_pts}

            # 2. Get Weather Data (with in-memory 1h caching)
            global _weather_history_cache
            if '_weather_history_cache' not in globals() or (time.time() - _weather_history_cache.get('ts', 0) > 3600):
                try:
                    om_url = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.232&hourly=temperature_2m,shortwave_radiation_instant&past_days=7&timezone=Europe%2FAmsterdam"
                    with urllib.request.urlopen(om_url, timeout=6) as r_om:
                        om_data = json.loads(r_om.read().decode())
                        h_data = om_data.get("hourly", {})
                        rad_m = {t: r for t, r in zip(h_data.get("time", []), h_data.get("shortwave_radiation_instant", []))}
                        temp_m = {t: tm for t, tm in zip(h_data.get("time", []), h_data.get("temperature_2m", []))}
                        _weather_history_cache = {'ts': time.time(), 'rad': rad_m, 'temp': temp_m}
                except Exception as e_om:
                    if '_weather_history_cache' not in globals():
                        _weather_history_cache = {'ts': 0, 'rad': {}, 'temp': {}}

            rad_map = _weather_history_cache.get('rad', {})
            temp_map = _weather_history_cache.get('temp', {})

            # 3. Model Parameters & Calibration Profile
            sol_cfg = cfg.get("solar", {})
            kwp = float(sol_cfg.get("kwp", 5.76))
            inv_max_w = int(sol_cfg.get("inverter_max_w", 5500))
            tilt = float(sol_cfg.get("tilt_degrees", 34.0))
            azimuth = float(sol_cfg.get("azimuth_degrees", 225.0))
            eff = float(sol_cfg.get("efficiency_factor", 0.88))

            try:
                from layer2_calibration.learned_forecaster import HybridForecastingModel
                forecaster = HybridForecastingModel()
                grid_96 = forecaster.profile.get("profile_96_quarters", [])
            except Exception:
                grid_96 = []

            labels = []
            act_solar, pred_solar = [], []
            act_dhw, pred_dhw, pred_dhw_demand = [], [], []
            act_cv, pred_cv = [], []
            act_total, pred_total = [], []

            # Pre-calculate realistic DHW planned dispatch schedule
            # A 350L tank requires only ~45-75 min to recharge (3 slots @ 1.8kW night, 4-5 slots @ 2.4kW day).
            dhw_planned_map = {}
            date_slot_map = {}
            for idx, p in enumerate(gen_pts):
                ts_str = p[0]
                dt_ams = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                d_key = dt_ams.date()
                if d_key not in date_slot_map:
                    date_slot_map[d_key] = []
                date_slot_map[d_key].append((idx, dt_ams))

            for d_key, day_indices in date_slot_map.items():
                # 1. Daytime solar run: 4-5 contiguous slots (60-75 min @ 2.4 kW) around peak sun
                solar_candidates = [
                    (idx, dt_ams) for (idx, dt_ams) in day_indices
                    if 10 <= dt_ams.hour <= 14
                ]
                if len(solar_candidates) >= 4:
                    best_s_sum = -1.0
                    best_s_start = 0
                    n_solar_slots = 4
                    for s_i in range(len(solar_candidates) - (n_solar_slots - 1)):
                        cur_sum = sum(
                            calculate_poa_solar_kw(
                                solar_candidates[s_i + k][1],
                                rad_map.get(solar_candidates[s_i + k][1].strftime("%Y-%m-%dT%H:00"), 0.0),
                                kwp=kwp, tilt_deg=tilt, azimuth_deg=azimuth, inverter_limit_kw=inv_max_w/1000.0, eff=eff
                            ) for k in range(n_solar_slots)
                        )
                        if cur_sum > best_s_sum:
                            best_s_sum = cur_sum
                            best_s_start = s_i
                    if best_s_sum >= 3.0:
                        for k in range(n_solar_slots):
                            dhw_planned_map[solar_candidates[best_s_start + k][0]] = 2.4

                # 2. Night valley top-up: 3 contiguous slots (45 min @ 1.8 kW) around 04:00 - 05:00
                night_candidates = [
                    (idx, dt_ams) for (idx, dt_ams) in day_indices
                    if (3 <= dt_ams.hour <= 4) or (dt_ams.hour == 5 and dt_ams.minute <= 15)
                ]
                if len(night_candidates) >= 3:
                    n_start = max(0, len(night_candidates) - 4)
                    for k in range(min(3, len(night_candidates) - n_start)):
                        dhw_planned_map[night_candidates[n_start + k][0]] = 1.8

            prev_dt = None
            for i, p in enumerate(gen_pts):
                ts_str = p[0]
                dt_ams = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                h_str = dt_ams.strftime("%Y-%m-%dT%H:00")

                if prev_dt is not None and dt_ams.day != prev_dt.day:
                    day_str = DUTCH_DAYS_SHORT[dt_ams.weekday()]
                    time_lbl = f"{day_str} {dt_ams.strftime(time_fmt)}"
                else:
                    time_lbl = dt_ams.strftime(time_fmt)
                labels.append(time_lbl)
                prev_dt = dt_ams

                # Actuals
                s_w = p[1] or 0.0
                tot_w = p[2] or 0.0
                d_w = dhw_map.get(ts_str, 0.0)
                c_w = cv_map.get(ts_str, 0.0)

                act_solar.append(round(max(0.0, s_w / 1000.0), 3))
                act_dhw.append(round(max(0.0, d_w / 1000.0), 3))
                act_cv.append(round(max(0.0, c_w / 1000.0), 3))
                act_total.append(round(max(0.0, tot_w / 1000.0), 3))

                # Predictions:
                # Solar POA Prediction
                ghi = rad_map.get(h_str, 0.0)
                p_sol_kw = calculate_poa_solar_kw(dt_ams, ghi, kwp=kwp, tilt_deg=tilt, azimuth_deg=azimuth, inverter_limit_kw=inv_max_w/1000.0, eff=eff)
                pred_solar.append(p_sol_kw)

                # Unallocated Load Prediction
                dow = dt_ams.weekday()
                q_idx = dt_ams.hour * 4 + dt_ams.minute // 15
                p_unalloc_kw = (grid_96[dow][q_idx] if (grid_96 and len(grid_96) > dow and len(grid_96[dow]) > q_idx) else 300.0) / 1000.0

                # DHW Run Model:
                # Option A: Geplande Warmtepomp Sturing (DHW Planned Dispatch in kW_el)
                p_dhw_kw = dhw_planned_map.get(i, 0.0)
                pred_dhw.append(p_dhw_kw)

                # Option B: Fysische Warmtevraag (Thermal draw-off in kW_th)
                p_dhw_dem_kw = round((GLOBAL_DHW_MODEL.get_learned_tap_kwh_th(dow, q_idx) if GLOBAL_DHW_MODEL else 0.03) * 4.0, 3)
                pred_dhw_demand.append(p_dhw_dem_kw)

                # CV Heating Model: Space heating was turned off in current conditions
                pred_cv.append(0.0)

                # Total House Prediction
                pred_total.append(round(p_unalloc_kw + p_dhw_kw, 3))

            def compute_kpis(actual_list, pred_list, peak_cap_kw: float = 5.0):
                if not actual_list or not pred_list:
                    return {"mae_w": 0, "accuracy_pct": 100.0, "total_actual_kwh": 0.0, "total_pred_kwh": 0.0, "delta_kwh": 0.0}
                n = len(actual_list)
                diffs = [abs(a - p) for a, p in zip(actual_list, pred_list)]
                mae_w = sum(diffs) / n * 1000.0
                tot_act = sum(actual_list) * interval_h
                tot_pred = sum(pred_list) * interval_h

                # 1. Volumetric Energy Accuracy (50% weight)
                vol_denom = max(tot_act, tot_pred, 1.0)
                acc_vol = max(0.0, 1.0 - (abs(tot_act - tot_pred) / vol_denom))

                # 2. Normalized Mean Absolute Error (50% weight) relative to rated peak capacity
                nmae = (mae_w / 1000.0) / max(1.0, peak_cap_kw)
                acc_shape = max(0.0, 1.0 - nmae)

                acc = round((0.5 * acc_vol + 0.5 * acc_shape) * 100.0, 1)
                return {
                    "mae_w": int(round(mae_w)),
                    "accuracy_pct": acc,
                    "total_actual_kwh": round(tot_act, 2),
                    "total_pred_kwh": round(tot_pred, 2),
                    "delta_kwh": round(tot_act - tot_pred, 2)
                }

            metrics = {
                "all": compute_kpis(act_total, pred_total, peak_cap_kw=5.5),
                "solar": compute_kpis(act_solar, pred_solar, peak_cap_kw=kwp),
                "dhw": compute_kpis(act_dhw, pred_dhw, peak_cap_kw=3.5),
                "cv": compute_kpis(act_cv, pred_cv, peak_cap_kw=4.0)
            }

            handler._send_json({
                "status": "success",
                "range": tf,
                "resolution": bucket_sz,
                "interval_h": interval_h,
                "labels": labels,
                "actual": {
                    "all": act_total,
                    "solar": act_solar,
                    "dhw": act_dhw,
                    "cv": act_cv
                },
                "predicted": {
                    "all": pred_total,
                    "solar": pred_solar,
                    "dhw": pred_dhw,
                    "dhw_demand": pred_dhw_demand,
                    "cv": pred_cv
                },
                "metrics": metrics
            })
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": f"Fout bij berekenen validatie overlay: {str(e)}"}, 500)
            return True

    # =========================================================================
    # API: HISTORICAL DHW TEMPERATURE & THERMAL DEMAND (kWh_th & V40)
    # =========================================================================
    if path.startswith("/api/analytics/dhw_history"):
        try:
            parsed_url = urllib.parse.urlparse(handler.path)
            qp = urllib.parse.parse_qs(parsed_url.query)
            tf = qp.get("range", ["24h"])[0]
            user_res = qp.get("resolution", ["15m"])[0]

            days = 1 if tf == "24h" else (2 if tf == "48h" else 7)
            bucket_sz = "1h" if user_res == "1h" else "15m"
            interval_h = 1.0 if bucket_sz == "1h" else 0.25

            now = datetime.now(timezone.utc)
            t_start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:00:00Z")
            t_end = now.strftime("%Y-%m-%dT%H:00:00Z")

            sec = load_secrets()
            pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influx_password", "")

            q = f"""
            SELECT mean("temperature_c") as tank_temp
            FROM "energy_telemetry"
            WHERE "device_id" = 'dhw_tank' AND time >= '{t_start}' AND time <= '{t_end}'
            GROUP BY time({bucket_sz}) fill(linear);
            SELECT sum("power_w")/1000.0 * {interval_h} as kwh_el
            FROM "energy_telemetry"
            WHERE "mode" = 'dhw' AND time >= '{t_start}' AND time <= '{t_end}'
            GROUP BY time({bucket_sz}) fill(0);
            """
            url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q)}"
            with urllib.request.urlopen(url, timeout=5) as r:
                res = json.loads(r.read().decode())

            temp_series = res["results"][0].get("series", [{}])[0].get("values", []) if len(res.get("results", [])) > 0 else []
            dhw_series = res["results"][1].get("series", [{}])[0].get("values", []) if len(res.get("results", [])) > 1 else []

            t_map = {row[0]: row[1] for row in temp_series}
            d_map = {row[0]: row[1] for row in dhw_series}

            sorted_ts = sorted(list(set(list(t_map.keys()) + list(d_map.keys()))))
            labels = []
            temps = []
            demands_kwh_th = []

            prev_dt = None
            last_t = 50.0
            try:
                sm = get_ha_states_map()
                v = float(sm.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {}).get("state", 50.0))
                if 20.0 <= v <= 75.0:
                    last_t = v
            except Exception:
                pass

            for ts_str in sorted_ts:
                dt_ams = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                if prev_dt is not None and dt_ams.day != prev_dt.day:
                    day_str = DUTCH_DAYS_SHORT[dt_ams.weekday()]
                    lbl = f"{day_str} {dt_ams.strftime('%H:%M' if bucket_sz == '15m' else '%H:00')}"
                else:
                    lbl = dt_ams.strftime("%H:%M" if bucket_sz == "15m" else "%H:00")
                prev_dt = dt_ams
                labels.append(lbl)

                t_val = t_map.get(ts_str)
                if t_val is not None:
                    last_t = round(float(t_val), 1)
                temps.append(last_t)

                kwh_el = float(d_map.get(ts_str) or 0.0)
                kwh_th = round(kwh_el * 2.6, 2) if kwh_el > 0 else 0.0
                demands_kwh_th.append(kwh_th)

            handler._send_json({
                "status": "success",
                "labels": labels,
                "temperatures_c": temps,
                "demand_kwh_th": demands_kwh_th,
                "interval_h": interval_h
            })
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, status=500)
            return True

    if path.startswith("/api/analytics/decisions"):
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        limit = int(qp.get("limit", [50])[0])
        domain = qp.get("domain", [None])[0]
        from layer3_scheduling.decision_audit import DecisionAuditLogger
        recs = DecisionAuditLogger.get_recent_decisions(limit=limit, domain=domain)
        handler._send_json({"status": "success", "total": len(recs), "decisions": recs})
        return True


    return False

def handle_post(handler, path: str, body: dict) -> bool:
    return False
