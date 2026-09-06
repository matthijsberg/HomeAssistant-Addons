#!/usr/bin/env python3
"""Dynamic Power Slotting and Energy Management Engine."""

import sys
import os
import json
import ssl
import urllib.request
from datetime import datetime, date

# Import HeatPumpCore
sys.path.insert(0, "/config/lib")
from heatpump_core import HeatPumpCore

class PowerSlotter:
    def __init__(self, core=None):
        self.core = core or HeatPumpCore()

    def calculate_boiler_duration(self, thermal_kwh: float, cop: float, compressor_kw: float = 3.0):
        """Calculate heat pump boiler runtime based on thermal demand, COP, and compressor power."""
        if cop <= 0 or compressor_kw <= 0:
            return 0.0, 0
        elec_kwh = thermal_kwh / cop
        dur_hours = elec_kwh / compressor_kw
        dur_mins = int(round(dur_hours * 60))
        return round(dur_hours, 2), dur_mins

    def allocate_hourly_power(
        self,
        gross_solar_kw: float,
        baseload_kw: float = 0.3,
        boiler_active: bool = False,
        boiler_power_kw: float = 3.0,
        cv_elec_kw: float = 0.0,
        battery_max_kw: float = 3.5
    ) -> dict:
        """Dynamically waterfall-allocates gross solar power across baseload, boiler, CV heating, battery, and grid."""
        # 1. Standby Baseload gets absolute first priority
        solar_to_baseload = min(gross_solar_kw, baseload_kw)
        grid_for_baseload = max(0.0, baseload_kw - solar_to_baseload)
        remaining_solar = max(0.0, gross_solar_kw - solar_to_baseload)

        # 2. Boiler Priority (Rigid DHW load when scheduled)
        boiler_kw_actual = 0.0
        grid_for_boiler = 0.0
        if boiler_active:
            solar_to_boiler = min(remaining_solar, boiler_power_kw)
            grid_for_boiler = max(0.0, boiler_power_kw - solar_to_boiler)
            remaining_solar = max(0.0, remaining_solar - solar_to_boiler)
            boiler_kw_actual = boiler_power_kw

        # 3. Space Heating Priority (CV Heat pump demand based on weather, temp, defrost & building model)
        cv_kw_actual = cv_elec_kw
        solar_to_cv = min(remaining_solar, cv_kw_actual)
        grid_for_cv = max(0.0, cv_kw_actual - solar_to_cv)
        remaining_solar = max(0.0, remaining_solar - solar_to_cv)

        # 4. Battery Modulation (Traploos vullen van de kieren met overgebleven zonnestroom)
        battery_charge_kw = min(remaining_solar, battery_max_kw)
        remaining_solar = max(0.0, remaining_solar - battery_charge_kw)

        # 5. Grid Export (Residual surplus)
        grid_export_kw = remaining_solar
        total_grid_import = grid_for_baseload + grid_for_boiler + grid_for_cv

        return {
            "baseload_kw": round(baseload_kw, 3),
            "boiler_kw": round(boiler_kw_actual, 3),
            "cv_elec_kw": round(cv_kw_actual, 3),
            "battery_charge_kw": round(battery_charge_kw, 3),
            "grid_export_kw": round(grid_export_kw, 3),
            "grid_import_kw": round(total_grid_import, 3)
        }

    def detect_peak_lockout_hours(self, prices_list: list, num_hours: int = 4) -> list:
        """Identifies peak lockout hours for Smart Grid SG1 across BOTH morning and evening peaks.
        - Morning peak window: 06:00 - 09:00 (highest 1-2 hours)
        - Evening peak window: 17:00 - 22:00 (highest 2-3 hours)
        """
        if len(prices_list) < 24:
            return [7, 19, 20]

        # Only lockout if morning price is elevated above the day median
        median_price = sorted(prices_list)[len(prices_list) // 2]
        morning_hours = [(h, prices_list[h]) for h in range(6, 10)]
        morning_sorted = sorted(morning_hours, key=lambda x: x[1], reverse=True)
        morning_peaks = []
        if morning_sorted[0][1] >= (median_price * 1.15):
            morning_peaks.append(morning_sorted[0][0])
            if len(morning_sorted) > 1 and morning_sorted[1][1] >= (morning_sorted[0][1] * 0.95):
                morning_peaks.append(morning_sorted[1][0])

        # Evening window: hours 17, 18, 19, 20, 21
        evening_hours = [(h, prices_list[h]) for h in range(17, 22)]
        evening_sorted = sorted(evening_hours, key=lambda x: x[1], reverse=True)
        evening_count = max(2, num_hours - len(morning_peaks))
        evening_peaks = [h for h, _ in evening_sorted[:evening_count] if _ >= median_price]

        lockout_hours = sorted(list(set(morning_peaks + evening_peaks)))
        return lockout_hours

    def generate_24h_schedule(
        self,
        target_date: date,
        cost_matrix: list,
        dhw_info: dict,
        battery_max_kw: float = 3.5,
        mode: str = "auto",
        manual_boiler_hour: int = None
    ) -> dict:
        """Generates the comprehensive 24-hour power slotting schedule across all 4 buffers."""
        prices = [slot["all_in_import"] for slot in cost_matrix]
        peak_lockout_hours = self.detect_peak_lockout_hours(prices)

        # Determine scheduled boiler hour
        if mode == "manual" and manual_boiler_hour is not None:
            scheduled_boiler_hour = manual_boiler_hour
        else:
            scheduled_boiler_hour = dhw_info.get("optimal_hour", 14)

        boiler_active_overall = dhw_info.get("active", True)
        compressor_kw = self.core.config.get("dhw_boiler", {}).get("compressor_power_kw", 3.0)
        baseload_kw = self.core.config.get("house", {}).get("baseload_watts", 300) / 1000.0

        hourly_slots = []
        for slot in cost_matrix:
            h = slot["hour"]
            hour_str = slot["hour_str"]
            gross_solar = slot["solar_kw"]
            spot_price = slot["spot_price"]
            import_price = slot["all_in_import"]

            is_boiler_slot = (h == scheduled_boiler_hour) and boiler_active_overall
            is_peak_lockout = h in peak_lockout_hours

            # Space heating demand from weather, building model, and defrost
            cv_th = slot.get("cv_thermal_kw", 0.0)
            cv_el = slot.get("cv_elec_kw", 0.0)
            temp = slot.get("temp", 15.0)
            rh = slot.get("rh", 75.0)
            wind = slot.get("wind", 10.0)
            cop = slot.get("cop", 4.0)
            is_summer = slot.get("is_summer_lockout", False)

            # SG-Ready state & 3-Way Valve exclusivity rule:
            # 1: Blocked (Peak lockout - morning & evening)
            # 2: Normal
            # 3: Recommended Boost / Floor buffer
            # 4: Forced ON (DHW Boiler Boost to 60°C)
            if is_boiler_slot:
                sg_state = 4
                sg_label = "SG4 (Boiler 60°C)"
                # EXCLUSIVITY: When 3-way valve switches to DHW, CV cannot run
                cv_el = 0.0
            elif is_peak_lockout:
                sg_state = 1
                sg_label = "SG1 (Blokkade / Piek)"
                # During peak lockout, compressor heating is suppressed (house coasts on thermal mass)
                cv_el = 0.0
            elif (12 <= h <= 16) and (slot["solar_surplus_kw"] > 1.2 or spot_price < 0.10):
                sg_state = 3
                sg_label = "SG3 (Bufferen / Zon)"
            else:
                sg_state = 2
                sg_label = "SG2 (Normaal)"

            # Allocate power across baseload, boiler, CV heating, battery, and grid
            allocation = self.allocate_hourly_power(
                gross_solar_kw=gross_solar,
                baseload_kw=baseload_kw,
                boiler_active=is_boiler_slot,
                boiler_power_kw=compressor_kw,
                cv_elec_kw=cv_el,
                battery_max_kw=battery_max_kw
            )

            hourly_slots.append({
                "hour": h,
                "time": hour_str,
                "spot_price": spot_price,
                "import_price": import_price,
                "gross_solar_kw": gross_solar,
                "temp": temp,
                "rh": rh,
                "wind": wind,
                "cop": cop,
                "cv_thermal_kw": cv_th,
                "cv_elec_kw": cv_el,
                "is_summer_lockout": is_summer,
                "sg_state": sg_state,
                "sg_label": sg_label,
                "boiler_active": is_boiler_slot,
                "baseload_kw": allocation["baseload_kw"],
                "boiler_kw": allocation["boiler_kw"],
                "cv_elec_allocated_kw": allocation["cv_elec_kw"],
                "battery_charge_kw": allocation["battery_charge_kw"],
                "grid_export_kw": allocation["grid_export_kw"],
                "grid_import_kw": allocation["grid_import_kw"]
            })

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "mode": mode,
            "scheduled_boiler_hour": scheduled_boiler_hour,
            "peak_lockout_hours": peak_lockout_hours,
            "battery_max_kw": battery_max_kw,
            "slots": hourly_slots
        }

    def format_influx_line(self, slot: dict, timestamp_ns: int) -> str:
        """Formats a single hourly slot into InfluxDB line protocol format with CV heating & defrost."""
        fields = [
            f"spot_price={slot.get('spot_price', 0.0):.5f}",
            f"import_price={slot.get('import_price', 0.0):.5f}",
            f"export_price={slot.get('export_price', 0.0):.5f}",
            f"solar_kw={slot.get('gross_solar_kw', 0.0):.3f}",
            f"solar_surplus_kw={slot.get('solar_surplus_kw', max(0.0, slot.get('gross_solar_kw', 0.0) - 0.3)):.3f}",
            f"outdoor_temp={slot.get('temp', 15.0):.1f}",
            f"rh={slot.get('rh', 75.0):.1f}",
            f"cop={slot.get('cop', 4.0):.2f}",
            f"cv_thermal_kw={slot.get('cv_thermal_kw', 0.0):.3f}",
            f"cv_elec_kw={slot.get('cv_elec_kw', 0.0):.3f}",
            f"boiler_kw={slot.get('boiler_kw', 0.0):.3f}",
            f"battery_charge_kw={slot.get('battery_charge_kw', 0.0):.3f}",
            f"baseload_kw={slot.get('baseload_kw', 0.3):.3f}",
            f"grid_import_kw={slot.get('grid_import_kw', 0.0):.3f}",
            f"grid_export_kw={slot.get('grid_export_kw', 0.0):.3f}",
            f"sg_state={int(slot.get('sg_state', 2))}i"
        ]
        return f"hems_hourly_schedule,source=energy_scheduler {','.join(fields)} {timestamp_ns}"

    def write_schedule_to_influx(self, schedule_data: dict) -> bool:
        """Writes the complete 24-hour schedule to InfluxDB in the 'hermes' database."""
        target_date_str = schedule_data.get("date")
        lines = []
        for slot in schedule_data.get("slots", []):
            h = slot["hour"]
            dt_slot = datetime.strptime(f"{target_date_str} {h:02d}:00:00", "%Y-%m-%d %H:%M:%S")
            # Epoch nanoseconds
            ts_ns = int(dt_slot.timestamp() * 1e9)
            lines.append(self.format_influx_line(slot, ts_ns))

        if not lines:
            return False

        payload = "\n".join(lines)
        influx_cfg = self.core.config.get("influxdb", {})
        u = influx_cfg.get("username", "hermes")
        p = influx_cfg.get("password", "")
        base_url = influx_cfg.get("url", "http://a0d7b954-influxdb:8086")
        params = {"u": u, "p": p, "db": "hermes"}
        url = f"{base_url}/write?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, data=payload.encode("utf-8"), method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in [200, 204]
        except Exception as e:
            print(f"Warning: Failed to write schedule to InfluxDB hermes db: {e}")
            return False

    def sync_to_google_sheets(self, schedule_data: dict, dhw_info: dict, fixed_cost: float, dynamic_cost: float, savings: float):
        """Syncs the 24-hour schedule and daily summary to Google Sheets in tab 'HEMS_Planning_En_Prijzen'."""
        spreadsheet_id = "1wXmYfuY6BL3vuS02tbps6VWZb2ArmhYjoYDndh8Kibc"
        tab_name = "HEMS_Planning_En_Prijzen"
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials.from_authorized_user_file("/config/.hermes/google_token.json")
            service = build("sheets", "v4", credentials=creds)

            # 1. Fetch existing data to avoid duplicates in daily summary
            res = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{tab_name}!A1:M100").execute()
            rows = res.get("values", [])

            header_summary = [
                "Datum", "Boiler Start", "DHW Warmte (kWh)", "DHW Stroom (kWh)", "CV Warmte (kWh)", "CV Stroom (kWh)",
                "Zon Totaal (kWh)", "Zon in Boiler (kWh)", "Zon in Accu (kWh)", "SG Piek Blokkades", "Kosten Vast (€)",
                "Kosten Dynamisch (€)", "Besparing (€)", "Modus", "Bijgewerkt"
            ]

            # Calculate daily totals
            slots = schedule_data.get("slots", [])
            total_solar = sum(s["gross_solar_kw"] for s in slots)
            total_solar_in_boiler = sum(min(s["boiler_kw"], max(0.0, s["gross_solar_kw"] - 0.3)) for s in slots)
            total_solar_in_batt = sum(s["battery_charge_kw"] for s in slots)
            total_cv_th = sum(s.get("cv_thermal_kw", 0.0) for s in slots)
            total_cv_el = sum(s.get("cv_elec_kw", 0.0) for s in slots)
            lockouts_str = ", ".join(f"{h:02d}:00" for h in schedule_data.get("peak_lockout_hours", []))

            today_summary_row = [
                schedule_data.get("date"),
                f"{schedule_data.get('scheduled_boiler_hour', 15):02d}:00",
                f"{dhw_info.get('thermal_kwh', 0.0):.2f}",
                f"{dhw_info.get('elec_kwh', 0.0):.2f}",
                f"{total_cv_th:.2f}",
                f"{total_cv_el:.2f}",
                f"{total_solar:.1f}",
                f"{total_solar_in_boiler:.2f}",
                f"{total_solar_in_batt:.2f}",
                lockouts_str,
                f"€{fixed_cost:.2f}",
                f"€{dynamic_cost:.2f}",
                f"€{savings:.2f}",
                schedule_data.get("mode", "auto"),
                datetime.now().strftime("%H:%M:%S")
            ]

            # Clean historical summary rows (upsert today, only rows with YYYY-MM-DD, strip accidental € from kWh columns)
            summary_dict = {}
            for r in rows:
                if len(r) >= 1 and isinstance(r[0], str) and r[0].startswith("202"):
                    cleaned_r = list(r)
                    # Ensure kWh columns (indices 2 to 8) don't have € symbols
                    for col_idx in range(2, min(len(cleaned_r), 9)):
                        if isinstance(cleaned_r[col_idx], str) and "€" in cleaned_r[col_idx]:
                            cleaned_r[col_idx] = cleaned_r[col_idx].replace("€", "").strip()
                    summary_dict[r[0]] = cleaned_r
            summary_dict[schedule_data.get("date")] = today_summary_row

            sorted_dates = sorted(summary_dict.keys())[-30:]
            summary_table = [header_summary] + [summary_dict[d] for d in sorted_dates]

            # 2. Detailed 24-hour table
            header_detail = [
                "Uur", "EPEX Spot (€)", "All-in Inkoop (€/kWh)", "Zon Opwek (kW)",
                "Buiten Temp (°C)", "COP (Ontdooi)", "CV Warmte (kW th)", "CV Stroom (kW e)",
                "Boiler Vraag (kW)", "Accu Lading (kW)", "Net Import (kW)", "Net Export (kW)", "Smart Grid Toestand"
            ]
            detail_rows = [
                [""],
                ["24-UURS DETAIL PLANNING VANDAAG"],
                header_detail
            ]
            for s in slots:
                detail_rows.append([
                    s["time"],
                    f"€{s['spot_price']:.4f}",
                    f"€{s['import_price']:.4f}",
                    f"{s['gross_solar_kw']:.2f}",
                    f"{s.get('temp', 0.0):.1f}°C",
                    f"{s.get('cop', 0.0):.2f}",
                    f"{s.get('cv_thermal_kw', 0.0):.2f}",
                    f"{s.get('cv_elec_kw', 0.0):.2f}",
                    f"{s['boiler_kw']:.1f}",
                    f"{s['battery_charge_kw']:.1f}",
                    f"{s['grid_import_kw']:.2f}",
                    f"{s['grid_export_kw']:.2f}",
                    s["sg_label"]
                ])

            full_sheet_values = summary_table + detail_rows

            # Clear existing values first to avoid ghost headers when table shifts
            service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=f"{tab_name}!A1:Z200"
            ).execute()

            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{tab_name}!A1:O{len(full_sheet_values) + 1}",
                valueInputOption="USER_ENTERED",
                body={"values": full_sheet_values}
            ).execute()
            print("Successfully synced HEMS Schedule to Google Sheets tab 'HEMS_Planning_En_Prijzen'!")
        except Exception as e:
            print(f"Warning: Failed to sync to Google Sheets: {e}")

    def publish_to_home_assistant(self, schedule_data: dict) -> bool:
        """Publishes the 24-hour schedule, numeric graph sensors, and markdown table to Home Assistant."""
        now_str = datetime.now().isoformat()
        curr_hour = datetime.now().hour
        curr_slot = schedule_data["slots"][curr_hour] if curr_hour < len(schedule_data["slots"]) else schedule_data["slots"][0]

        # 1. Main schedule status sensor
        entity_id = "sensor.energy_power_schedule"
        state_val = f"Boiler: {schedule_data['scheduled_boiler_hour']:02d}:00 | {curr_slot['sg_label']}"
        attrs = {
            "friendly_name": "Dynamisch Vermogens- & Smart Grid Schema",
            "icon": "mdi:chart-timeline-variant-shimmer",
            "mode": schedule_data["mode"],
            "scheduled_boiler_hour": schedule_data["scheduled_boiler_hour"],
            "peak_lockout_hours": schedule_data["peak_lockout_hours"],
            "current_hour_allocation": curr_slot,
            "hourly_schedule": schedule_data["slots"],
            "updated_at": now_str
        }
        self.core.publish_ha_state(entity_id, state_val, attrs)

        # 2. Numeric sensors for native graphing in Home Assistant
        # A. Current All-in Electricity Import Price
        self.core.publish_ha_state("sensor.hems_current_import_price", curr_slot["import_price"], {
            "unit_of_measurement": "EUR/kWh",
            "device_class": "monetary",
            "state_class": "measurement",
            "friendly_name": "HEMS Actuele Stroomprijs (All-in)",
            "icon": "mdi:cash"
        })

        # B. Scheduled Boiler Power (kW)
        self.core.publish_ha_state("sensor.hems_scheduled_boiler_power", curr_slot["boiler_kw"], {
            "unit_of_measurement": "kW",
            "device_class": "power",
            "state_class": "measurement",
            "friendly_name": "HEMS Gepland Boiler Vermogen",
            "icon": "mdi:water-boiler"
        })

        # C. Scheduled Battery Charge Power (kW)
        self.core.publish_ha_state("sensor.hems_scheduled_battery_power", curr_slot["battery_charge_kw"], {
            "unit_of_measurement": "kW",
            "device_class": "power",
            "state_class": "measurement",
            "friendly_name": "HEMS Gepland Accu Laadvermogen",
            "icon": "mdi:battery-charging"
        })

        # D. Scheduled CV Space Heating Demand (Thermal & Electric kW)
        self.core.publish_ha_state("sensor.hems_scheduled_heating_thermal_kw", curr_slot.get("cv_thermal_kw", 0.0), {
            "unit_of_measurement": "kW",
            "device_class": "power",
            "state_class": "measurement",
            "friendly_name": "HEMS Verwarming Vraag (Thermisch)",
            "icon": "mdi:home-thermometer"
        })
        self.core.publish_ha_state("sensor.hems_scheduled_heating_elec_kw", curr_slot.get("cv_elec_kw", 0.0), {
            "unit_of_measurement": "kW",
            "device_class": "power",
            "state_class": "measurement",
            "friendly_name": "HEMS Warmtepomp Stroom (CV)",
            "icon": "mdi:heat-pump"
        })
        self.core.publish_ha_state("sensor.hems_cop_heating", curr_slot.get("cop", 4.0), {
            "unit_of_measurement": "COP",
            "state_class": "measurement",
            "friendly_name": "HEMS COP Warmtepomp (Ontdooi-gecorrigeerd)",
            "icon": "mdi:gauge"
        })

        # E. Smart Grid State (1=SG1 Lockout, 2=SG2 Normal, 3=SG3 Boost, 4=SG4 Forced)
        self.core.publish_ha_state("sensor.hems_smart_grid_state", curr_slot["sg_state"], {
            "friendly_name": "HEMS Smart Grid Toestand (1-4)",
            "icon": "mdi:transmission-tower",
            "sg_label": curr_slot["sg_label"]
        })

        # 3. Clean Markdown Table Card for Lovelace UI
        md_lines = [
            "| Uur | Prijs/kWh | Zon | Buiten | COP | CV Warmte | Boiler | Accu | SG Status |",
            "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
        ]
        for s in schedule_data.get("slots", []):
            h_marker = f"**{s['time']}**" if s["hour"] == curr_hour else s["time"]
            b_val = f"🔥 {s['boiler_kw']:.1f}kW" if s["boiler_kw"] > 0 else "-"
            a_val = f"🔋 +{s['battery_charge_kw']:.1f}kW" if s["battery_charge_kw"] > 0 else "-"
            z_val = f"☀️ {s['gross_solar_kw']:.1f}kW" if s["gross_solar_kw"] > 0 else "-"
            cv_val = f"🏠 {s.get('cv_thermal_kw', 0.0):.1f}kW" if s.get("cv_thermal_kw", 0.0) > 0 else ("- (Zomer)" if s.get("is_summer_lockout") else "-")
            p_val = f"€{s['import_price']:.2f}"
            t_val = f"{s.get('temp', 15.0):.1f}°C"
            cop_val = f"{s.get('cop', 4.0):.2f}"
            sg_short = "SG1 Piek" if s["sg_state"] == 1 else ("SG3 Zon" if s["sg_state"] == 3 else ("SG4 Force" if s["sg_state"] == 4 else "SG2 Norm"))
            md_lines.append(f"| {h_marker} | {p_val} | {z_val} | {t_val} | {cop_val} | {cv_val} | {b_val} | {a_val} | {sg_short} |")

        self.core.publish_ha_state("sensor.hems_schedule_markdown", f"{curr_slot['time']} actief", {
            "friendly_name": "24-Uurs HEMS Dagschema Tabel",
            "icon": "mdi:table-clock",
            "table_markdown": "\n".join(md_lines),
            "updated_at": now_str
        })

        return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run Dynamic Power Slotter")
    parser.add_argument("--dry-run", action="store_true", help="Run without updating Home Assistant")
    args = parser.parse_args()

    core = HeatPumpCore()
    slotter = PowerSlotter(core)

    now = datetime.now()
    today = now.date()

    print("==================================================")
    print("      DYNAMIC POWER SLOTTER & HEMS ENGINE")
    print("==================================================")
    print(f"Date: {today} | Execution Time: {now.strftime('%H:%M:%S')}\n")

    # 1. Fetch live inputs
    spot_prices = core.fetch_epex_spot_prices(today)
    _, _, _, solar_kw_map, _ = core.fetch_weather_and_solar()
    cost_matrix = core.build_hourly_cost_matrix(today, spot_prices, solar_kw_map)

    is_vac, dhw_off, _, vac_name = core.check_vacation(today)
    dhw_info = core.calculate_dhw_dynamic(today, cost_matrix, is_vac, dhw_off, vac_name)

    # 1b. Fetch GUI Mode and Battery Limit from HA if present
    scheduler_mode = "auto"
    manual_boiler_hour = None
    battery_max_kw = 3.5

    if core.ha_token:
        headers = {"Authorization": f"Bearer {core.ha_token}", "content-type": "application/json"}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            req_m = urllib.request.Request(f"{core.ha_url}/api/states/input_select.energie_planner_modus", headers=headers)
            with urllib.request.urlopen(req_m, timeout=3, context=ctx) as r:
                ha_m = json.loads(r.read().decode("utf-8")).get("state", "Automatisch (Optimaal)")
                if "handmatig" in ha_m.lower():
                    scheduler_mode = "manual"
                    # fetch manual boiler time
                    req_dt = urllib.request.Request(f"{core.ha_url}/api/states/input_datetime.geplande_starttijd_boiler", headers=headers)
                    with urllib.request.urlopen(req_dt, timeout=3, context=ctx) as r_dt:
                        dt_val = json.loads(r_dt.read().decode("utf-8")).get("state", "15:00:00")
                        manual_boiler_hour = int(dt_val.split(":")[0])
        except Exception:
            pass

        try:
            req_b = urllib.request.Request(f"{core.ha_url}/api/states/input_number.max_laadvermogen_accu", headers=headers)
            with urllib.request.urlopen(req_b, timeout=3, context=ctx) as r:
                b_val = float(json.loads(r.read().decode("utf-8")).get("state", 3500))
                battery_max_kw = b_val / 1000.0
        except Exception:
            pass

    # 2. Generate schedule
    schedule = slotter.generate_24h_schedule(
        target_date=today,
        cost_matrix=cost_matrix,
        dhw_info=dhw_info,
        battery_max_kw=battery_max_kw,
        mode=scheduler_mode,
        manual_boiler_hour=manual_boiler_hour
    )

    # Update input_datetime.geplande_starttijd_boiler & heatpump_optimal_runtime in Auto mode
    if scheduler_mode == "auto" and not args.dry_run and core.ha_token:
        for eid in ["input_datetime.geplande_starttijd_boiler", "input_datetime.heatpump_optimal_runtime"]:
            try:
                slot_h_str = f"{schedule['scheduled_boiler_hour']:02d}:00:00"
                dt_payload = {"entity_id": eid, "time": slot_h_str}
                req_set = urllib.request.Request(f"{core.ha_url}/api/services/input_datetime/set_datetime", data=json.dumps(dt_payload).encode("utf-8"), headers=headers, method="POST")
                with urllib.request.urlopen(req_set, timeout=5, context=ctx) as r_s:
                    pass
            except Exception:
                pass

    print("--- 24-HOUR POWER SLOTTING SUMMARY ---")
    print(f"Scheduled Boiler Run:     {schedule['scheduled_boiler_hour']:02d}:00:00 (3.0 kW)")
    print(f"Smart Grid Peak Lockouts: {', '.join([f'{h:02d}:00' for h in schedule['peak_lockout_hours']])}")
    print("\nKey Hours Preview:")
    for slot in schedule["slots"][12:18]:
        b_kw = f"Boiler: {slot['boiler_kw']:.1f} kW" if slot['boiler_kw'] > 0 else "Boiler: 0.0 kW"
        batt_kw = f"Accu: +{slot['battery_charge_kw']:.1f} kW" if slot['battery_charge_kw'] > 0 else "Accu: 0.0 kW"
        print(f"  {slot['time']} | Zon {slot['gross_solar_kw']:.1f}kW | {b_kw:<16} | {batt_kw:<14} | {slot['sg_label']}")

    # Calculate comparison figures for reporting
    fixed_dhw = core.calculate_dhw(today, solar_kw_map, is_vac, dhw_off, vac_name)
    fixed_cost = fixed_dhw.get("cost", 0.23)
    dynamic_cost = dhw_info.get("cost", 0.10)
    savings = round(fixed_cost - dynamic_cost, 2)

    if not args.dry_run:
        print("\n1. Writing schedule to InfluxDB (database: hermes)...")
        if slotter.write_schedule_to_influx(schedule):
            print("  ✓ Successfully wrote 24 hours of HEMS data to InfluxDB 'hermes' db!")

        print("2. Syncing day/week details to Google Sheets...")
        slotter.sync_to_google_sheets(schedule, dhw_info, fixed_cost, dynamic_cost, savings)

        print("3. Publishing schedule and visual sensors to Home Assistant...")
        res = slotter.publish_to_home_assistant(schedule)
        if res:
            print("  ✓ Successfully published all sensors and schedule to Home Assistant!")
        else:
            print("Warning: Failed to publish to Home Assistant.")
    else:
        print("\n[DRY_RUN] Skipped Home Assistant, InfluxDB, and Google Sheets write.")

if __name__ == "__main__":
    main()
