# Dynamic Energy Scheduler & Power Slotter (HEMS)

**Versie:** `0.2.0`  
**Auteur:** Hermes Agent | In opdracht van: Matthijs van den Berg  
**Systeem:** Daikin Altherma 3 H HT 18kW · 350L SWW Boiler (OEG FKS 500-R) · 5.5 kWp Zonnepanelen · Deye 10kW Hybride Thuisaccu  
**Contract:** Powerpeers Dynamisch (per 25 september 2026) · EnergyZero API  

---

## 📁 Duidelijke Folderstructuur & Lagen

Het HEMS-systeem is opgedeeld in vier helder herkenbare lagen:

```
/config/projects/energy-scheduler/
│
├── 🌐 layer1_data_collection/     # LAAG 1: Externe Dataverzameling & Caching
│   ├── __init__.py
│   └── collector.py               # EnergyZero (15m/1h prijzen), Open-Meteo, Freshness & 45s Retries
│
├── 🔬 layer2_calibration/         # LAAG 2: Fysisch Model & Zelflerende Feedback
│   ├── __init__.py
│   └── calibrator.py              # Zonnehoek & Schaduwmatrix K(h), DHW standby verlies, OLS Regressie
│
├── ⚡ layer3_scheduling/          # LAAG 3: Vermogens-Waterval & Smart Grid Dispatch
│   ├── __init__.py
│   └── scheduler.py               # Power Slotter: Sluiplast -> SWW -> CV -> Accu -> Teruglevering
│
├── 🚀 runners/                    # UITVOERENDE RUNNERS (Cron / CLI)
│   ├── run_daily_optimizer.py     # 4x daags (06:30, 11:30, 14:30, 18:30) HEMS dagplanning
│   ├── run_weekly_calibration.py  # Wekelijkse zelfkalibratie van het model (Zondag 08:00)
│   └── sync_energy_taxes.py       # Periodieke synchronisatie van officiële energiebelasting
│
├── 📁 config/                     # CONFIGURATIEBESTANDEN
│   ├── heatpump_config.json       # Master hardware, tarieven en vermogens
│   └── model_parameters.json      # Gekalibreerde parameters (UA_base, tilt matrix, COP)
│
├── 📁 data/                       # LOKALE CACHES (Offline Veilig)
│   └── energy_feed_cache.json     # Tier 1 Hot Cache met atomaire replace-schrijfacties
│
└── 🧪 tests/unit/                 # GEAUTOMATISEERDE TESTS (100% Groen)
    ├── test_collector.py          # Freshness, no-mock-data, retries
    ├── test_calibrator.py         # OLS convergentie, tilt matrix, clamping vangrails
    └── test_power_slotter.py      # Vermogenswaterval, spitsblokkades, Influx export
```

---

## ⚡ De 3 Kernlagen Uitgelegd

### Laag 1: Data Verzameling (`layer1_data_collection`)
* **Wat doet het?** Haalt 96 kwartierprijzen en 24 uursprijzen op via de publieke EnergyZero API, en haalt de 48-uurs weersvoorspelling op via Open-Meteo.
* **Freshness Check:** Berekent de exacte leeftijd in seconden. Data ouder dan 4 uur triggert automatisch een refresh.
* **Resilientie:** 5-voudige exponentiële backoff (tot 45 seconden retry-window).
* **Nul Mock Data:** Gooit bij een complete API-storing en lege cache een formele `DataUnavailableError` op; rekent nooit met verzonnen getallen.

### Laag 2: Zelflerend & Fysisch Model (`layer2_calibration`)
* **Uurlijkse Zonnehoek- & Schaduwmatrix $K(h)$:**  
  Berekent op basis van jouw werkelijke dak- en sensordata de correctiefactor per uur. In de namiddag leveren jouw zuidwest-georiënteerde schuine panelen $1{,}25\times$ tot $1{,}73\times$ méér dan standaard vlakke weerkaarten voorspellen!
* **DHW Vatverlies:** Verrekent het natuurlijke stilstandsverlies van het 350L vat (~1,5 à 2,0 kWh/etmaal).
* **Vangrails & Clamping:** Exponentiële demping (80% oud + 20% nieuw) en harde fysische grenzen ($K(h) \in [0{,}5; 2{,}5]$, $UA \in [6; 11]$) voorkomen dat sensor-uitschieters het model ontregelen.

### Laag 3: HEMS Regie & Sturing (`layer3_scheduling`)
* **De Prioriteiten-Waterval:**  
  1. Baseload (~300 W continue huishoudafname)  
  2. 350L SWW Boiler (Prioriteit 1: DP optimizer, COP ~3.1, 0 ct degradatie)  
  3. CV Verwarming (Prioriteit 2: 2R1C schil- en vloermodel, COP ~4.5, 0 ct degradatie)  
  4. Thuisbatterij (Prioriteit 3: 15 kWh LFP, loosely coupled optimizer, 87% roundtrip efficiency)  
  5. Net-teruglevering (Feed-in tegen markttarief minus opslag)
* **Thuisbatterij Slimme Sturing (`battery_policy.py`):**
  * `CHARGE_SOLAR`: Gratis zonne-overschot opvangen tot 5 kW.
  * `DISCHARGE_PEAK`: Spitsontlasting tijdens dure uren om netafname naar 0 W te drukken.
  * `HOLD_RESERVE`: Accucapaciteit strategisch vasthouden voor een latere duurdere piek.
  * `CHARGE_GRID`: In de winter 's nachts bijladen mits de spread groter is dan het roundtrip-verlies (13%) + slijtage (€0,078/kWh).
* **Warmtepomp Smart Grid Relais (S10S / S11S):**  
  * `SG1 (Geforceerd uit)`: Blokkade tijdens ochtendspits (07:00–08:30) en avondspits (17:30–20:30).
  * `SG2 (Normaal)`: Rustig basisbedrijf op de weersafhankelijke stooklijn.
  * `SG3 (Geadviseerd aan)`: Thermische vloerbuffering (+1°C) tijdens zonne-uren.
  * `SG4 (Geforceerd aan)`: 60°C boilerrun (waarbij de cv-hoofdschakelaar eerst wordt uitgezet om 9 kW elementen te weren).

---

## 🚀 Snelle Commando's

```bash
# 1. Handmatig draaien van de HEMS dagplanning:
/config/projects/energy-scheduler/runners/run_daily_optimizer.py

# 2. Handmatig draaien van de wekelijkse modelkalibratie:
/config/projects/energy-scheduler/runners/run_weekly_calibration.py

# 3. Draaien van de volledige testsuite:
pytest /config/projects/energy-scheduler/tests/unit/ -v
```
