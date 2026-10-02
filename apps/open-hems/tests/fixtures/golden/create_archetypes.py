"""
Generate 5 seasonal reference archetypes for Culemborg site in exact production replay format:
1. golden_summer_solar_heavy.json
2. golden_winter_sunny_peak.json
3. golden_winter_dunkelflaute.json
4. golden_winter_defrost_humid.json
5. golden_shoulder_season.json
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FIXTURES_DIR = Path("/config/addons/open-hems/tests/fixtures/golden")

# Base unallocated wattage pattern for Matthijs's residence (350W standby, morning/evening peaks)
BASE_UNALLOC_96 = [
    320, 310, 305, 300, 300, 295, 290, 290, 285, 285, 280, 280, 285, 290, 295, 300, # 00:00 - 04:00
    310, 320, 330, 340, 350, 380, 420, 480, 580, 720, 850, 920, 810, 650, 520, 450, # 04:00 - 08:00
    420, 400, 390, 380, 370, 365, 360, 355, 360, 380, 410, 430, 450, 440, 420, 400, # 08:00 - 12:00
    390, 385, 380, 380, 385, 390, 400, 420, 450, 480, 520, 560, 620, 700, 780, 850, # 12:00 - 16:00
    920, 1050, 1180, 1250, 1320, 1400, 1280, 1150, 980, 850, 750, 680, 620, 580, 540, 500, # 16:00 - 20:00
    480, 460, 440, 420, 400, 390, 380, 370, 360, 350, 340, 335, 330, 325, 320, 320  # 20:00 - 24:00
]


def generate_archetype_dataset(
    name: str,
    date_str: str,
    temp_profile_24: list,
    solar_rad_profile_24: list,
    price_profile_96: list,
    dhw_tank_temp: float,
    room_temp: float,
    notes: str
):
    tz = ZoneInfo("Europe/Amsterdam")
    start_dt = datetime.fromisoformat(f"{date_str}T00:00:00").replace(tzinfo=tz)

    prices = []
    # 96 slots for the target day
    for i in range(96):
        slot_dt = start_dt + timedelta(minutes=15 * i)
        prices.append({
            "timestamp": slot_dt.isoformat(),
            "price": round(price_profile_96[i], 5)
        })

    weather = []
    for h in range(24):
        h_dt = start_dt + timedelta(hours=h)
        weather.append({
            "timestamp": h_dt.isoformat(),
            "temperature_c": round(temp_profile_24[h], 1),
            "solar_radiation_w_m2": round(solar_rad_profile_24[h], 1),
            "wind_speed_m_s": 3.8
        })

    dataset = {
        "archetype": name,
        "recorded_date": date_str,
        "site": "Culemborg",
        "latitude": 51.9537,
        "longitude": 5.232,
        "source": f"Culemborg Archetype Benchmark ({notes})",
        "dhw_tank_temp_c": dhw_tank_temp,
        "room_temp_c": room_temp,
        "prices": prices,
        "weather": weather,
        "unallocated_profile_96": BASE_UNALLOC_96
    }

    out_file = FIXTURES_DIR / f"{name}.json"
    out_file.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    print(f"Created fixture: {out_file.name}")


# 1. Hoogzomer (Zonrijk & Negatieve Prijzen)
# Temps 18°C night to 29°C day. Radiation up to 920 W/m2.
temp_summer = [19, 18.5, 18, 18, 18.5, 19, 21, 23, 25, 26.5, 27.5, 28.2, 29.0, 29.2, 28.8, 28.0, 27.0, 25.5, 24.0, 23.0, 22.0, 21.0, 20.0, 19.5]
solar_summer = [0, 0, 0, 0, 0, 20, 120, 310, 520, 720, 860, 920, 910, 850, 740, 580, 390, 180, 50, 5, 0, 0, 0, 0]
# Negative prices between 12:00 and 15:30 (slots 48 to 62)
prices_summer = [0.24] * 96
for i in range(48, 63):
    prices_summer[i] = -0.045
for i in range(70, 82):
    prices_summer[i] = 0.36

generate_archetype_dataset(
    name="golden_summer_solar_heavy",
    date_str="2026-06-25",
    temp_profile_24=temp_summer,
    solar_rad_profile_24=solar_summer,
    price_profile_96=prices_summer,
    dhw_tank_temp=42.0,
    room_temp=22.5,
    notes="Hoogzomer 35kWh PV met negatieve middagprijzen en actieve zomersluiting CV"
)

# 2. Koude Winterdag met Heldere Zon (De 'Winterzon'-casus)
# Temps 1°C night to 5.5°C day. Radiation up to 520 W/m2.
temp_winter_sun = [1.8, 1.5, 1.2, 1.0, 1.0, 1.2, 1.5, 2.0, 2.8, 3.8, 4.8, 5.5, 5.4, 5.0, 4.2, 3.2, 2.5, 2.0, 1.8, 1.6, 1.5, 1.4, 1.2, 1.0]
solar_winter_sun = [0, 0, 0, 0, 0, 0, 0, 0, 60, 220, 410, 520, 510, 430, 260, 80, 0, 0, 0, 0, 0, 0, 0, 0]
prices_winter_sun = [0.22] * 96
for i in range(8, 20):  # Night valley
    prices_winter_sun[i] = 0.15
for i in range(28, 38): # Morning peak
    prices_winter_sun[i] = 0.38
for i in range(44, 60): # Cheap solar midday
    prices_winter_sun[i] = 0.18
for i in range(68, 83): # Sharp evening peak
    prices_winter_sun[i] = 0.44

generate_archetype_dataset(
    name="golden_winter_sunny_peak",
    date_str="2026-01-18",
    temp_profile_24=temp_winter_sun,
    solar_rad_profile_24=solar_winter_sun,
    price_profile_96=prices_winter_sun,
    dhw_tank_temp=39.0,
    room_temp=20.0,
    notes="Koude heldere winterdag met middagbuffer en harde avondspitsblokkade"
)

# 3. Grijze Winterdag (Dunkelflaute)
# Freezing overcast day, temps -2.5°C to +0.5°C.
temp_dunkel = [-2.5, -2.4, -2.2, -2.0, -2.0, -1.8, -1.5, -1.2, -0.8, -0.4, 0.0, 0.5, 0.4, 0.2, -0.2, -0.6, -1.0, -1.4, -1.8, -2.0, -2.2, -2.4, -2.5, -2.5]
solar_dunkel = [0] * 24  # Completely overcast (< 20 W/m2)
solar_dunkel[11] = 25
solar_dunkel[12] = 30
prices_dunkel = [0.29] * 96
for i in range(8, 20): # Night valley
    prices_dunkel[i] = 0.21
for i in range(28, 38): # Morning peak
    prices_dunkel[i] = 0.45
for i in range(68, 82): # Evening peak
    prices_dunkel[i] = 0.48

generate_archetype_dataset(
    name="golden_winter_dunkelflaute",
    date_str="2026-12-14",
    temp_profile_24=temp_dunkel,
    solar_rad_profile_24=solar_dunkel,
    price_profile_96=prices_dunkel,
    dhw_tank_temp=43.0,
    room_temp=19.8,
    notes="Vriezende dunkelflaute dag met stabiele deellast modulatie en nachtdalsturing"
)

# 4. Kwakkelwinter / Mistig & Vochtig (Defrost Zone)
# Temps 1.0°C to 2.8°C (high frosting risk).
temp_defrost = [1.2, 1.0, 0.8, 0.8, 1.0, 1.2, 1.4, 1.6, 2.0, 2.4, 2.8, 2.6, 2.5, 2.2, 1.8, 1.5, 1.4, 1.2, 1.2, 1.0, 1.0, 0.8, 0.8, 0.9]
solar_defrost = [0, 0, 0, 0, 0, 0, 0, 10, 45, 90, 120, 140, 130, 110, 70, 20, 0, 0, 0, 0, 0, 0, 0, 0]
prices_defrost = [0.25] * 96
for i in range(8, 20): prices_defrost[i] = 0.17
for i in range(28, 38): prices_defrost[i] = 0.39
for i in range(68, 82): prices_defrost[i] = 0.41

generate_archetype_dataset(
    name="golden_winter_defrost_humid",
    date_str="2026-02-05",
    temp_profile_24=temp_defrost,
    solar_rad_profile_24=solar_defrost,
    price_profile_96=prices_defrost,
    dhw_tank_temp=40.5,
    room_temp=20.0,
    notes="Vriesmist en vochtige kwakkelwinter met ontdooicyclus-afslag in COP"
)

# 5. Schouderseizoen (Voorjaar / Najaar)
# Temps 10°C to 17.5°C, crossing the 16°C threshold.
temp_shoulder = [10.5, 10.0, 9.8, 9.5, 9.5, 10.0, 11.2, 12.8, 14.5, 15.8, 16.8, 17.5, 17.2, 16.5, 15.2, 14.0, 13.0, 12.5, 12.0, 11.5, 11.2, 11.0, 10.8, 10.5]
solar_shoulder = [0, 0, 0, 0, 0, 10, 60, 180, 320, 480, 620, 680, 650, 580, 440, 260, 110, 30, 0, 0, 0, 0, 0, 0]
prices_shoulder = [0.23] * 96
for i in range(48, 62): prices_shoulder[i] = 0.14
for i in range(68, 80): prices_shoulder[i] = 0.34

generate_archetype_dataset(
    name="golden_shoulder_season",
    date_str="2026-04-20",
    temp_profile_24=temp_shoulder,
    solar_rad_profile_24=solar_shoulder,
    price_profile_96=prices_shoulder,
    dhw_tank_temp=44.0,
    room_temp=20.5,
    notes="Schouderseizoen met middagtemperatuur boven 16C en stabiele stookseizoenswissel"
)
