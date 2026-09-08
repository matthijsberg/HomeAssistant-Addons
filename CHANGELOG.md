# Changelog — Open HEMS

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to our **4-Tier Semantic Versioning Specification**:
- **Tier 1 (MAJOR `X.0.0`):** Breaking architectural changes, core framework rewrites, fundamental data model shifts.
- **Tier 2 (MINOR `x.Y.0`):** Public releases published to GitHub with user-facing features, adapters, and release notes.
- **Tier 3 (PATCH `x.y.Z`):** Stable bug fixes, security patches, and localized component enhancements.
- **Tier 4 (DEV/INTERNAL `x.y.z-dev.N`):** Incremental development and internal test iterations.

---

## [0.20.1] — 2026-09-08 (Fix Variable Naming for Unallocated Load in Battery Optimization)

### Fixed
- Replaced remaining `baseload` references with `unallocated` in battery surplus candidate calculations.

---

## [0.20.0] — 2026-09-08 (7x24 Learned Hourly Unallocated Load Profile & Predictive Modeling)

### Added
- **Learned 7x24 Hourly Unallocated Consumption Profile:**
  - Aggregated 8,709 hourly historical production measurements over the past 365 days from InfluxDB:
    $$\text{Ongedefinieerd Verbruik} = (P_{\text{P1\_import}} - P_{\text{P1\_export}}) + P_{\text{zon}} - P_{\text{warmtepomp}}$$
  - Formulated a 168-hour day-of-week load matrix capturing real household behavioral patterns:
    - **Maandag:** 315 W average, 432 W morning peak, 673 W evening peak.
    - **Dinsdag (Wasdag):** 383 W average, 439 W morning coffee peak, **941 W evening peak** (laundry & household appliances).
    - **Zaterdag / Zondag:** 514 W weekend morning peaks (breakfast/cooking), 320 W night minimums.
  - Dynamically injects the calibrated hour-by-hour unallocated load profile into the 24-hour predictive forecast instead of a static baseline.
- **UI & Terminology Overhaul:**
  - Renamed all baseline metrics to **"Ongedefinieerd Verbruik"** across the UI, tooltips, and legend.
  - Updated card header badge to `Ongedefinieerd Verbruik: 7x24 Model`.
  - Recalculated net power balance ($\text{Cons} - \text{Gen}$) with dynamic unallocated load.

---

## [0.19.1] — 2026-09-08 (Set Expected Net Line Color to Crimson Red)

### Changed
- **Verwacht Netto Verbruik Line Color:**
  - Changed overlay line color and points from orange (`#F97316`) to vivid crimson red (`#EF4444`).
  - Updated corresponding legend chip indicator to red (`bg-red-500` / `text-red-400`).

---

## [0.19.0] — 2026-09-08 (Classic Dual-Polarity Stacked Architecture & Net Overlay Line)

### Added & Re-Architected
- **Aligned with Power Producers & Netstromen Design Language:**
  - **Consumers (> 0 kW):** All loads stack upward above the horizontal zero axis:
    - Basislast (+kW, blue)
    - SWW Tapwater 350L (+kW, pink)
    - CV Verwarming (+kW, indigo)
    - Accu Laden (+kW, emerald green) — explicitly categorized as a consumer.
  - **Generation & Sources (< 0 kW):** All energy sources stack downward below the horizontal zero axis:
    - Zon Productie (-kW, amber/gold)
    - Accu Ontladen (-kW, teal/cyan)
  - **Expected Net Power Line (`net_power_kw`):** Continuous bold orange overlay line drawn directly across the bar chart:
    - Line > 0: Net grid import (netafname)
    - Line < 0: Net grid export (teruglevering)
    - Line = 0: Completely self-sufficient
- **Dynamic Price Overlay:** Aligned on the right Y1-axis with mathematically synchronized zero axis.
- **Unified Clean Tooltips:** Zero-value entries filtered out; tapping outside canvas immediately dismisses popups on mobile.

---

## [0.18.1] — 2026-09-08 (Deploy Appliance Advice Banner & Surplus Badge)

### Fixed & Enhanced
- **Visible Appliance Advice Banner & Badge:**
  - Added `☀️ Vrij Overschot: X.X kWh` summary badge to the 24h prediction header.
  - Added dedicated emerald callout banner recommending optimal hours for running washing machines, dryers, dishwashers, or EV charging on free solar surplus.
  - Added legend chip for `Vrij Zonne-Overschot (Wasmachine/EV)`.

---

## [0.18.0] — 2026-09-08 (Synchronized Zero Axis, Solar Surplus Appliance Advice & Touch Dismiss)

### Added
- **Synchronized Zero Horizontal Axis:**
  - Mathematically locked the zero-line position of both the Left Y-axis (Power in kW) and Right Y1-axis (Price in €/kWh) using an exact linear ratio `zeroRatio = |minY| / (maxY - minY)`. Both metrics now share the exact same horizontal grid line at $Y = 0$.
- **Solar Surplus Appliance Window & Recommendation (Wasmachine / Vaatwasser / EV):**
  - **Visual:** Rendered unconsumed free solar surplus (`Vrij Zonne-Overschot`) as a distinct green floating bar between the top of the scheduled consumer stack and the 0-line.
  - **Callout Banner:** Dedicated appliance advice banner above the chart computing total surplus energy (e.g. ~5.3 kWh free solar, peak 1.4 kW) to advise running non-smart household appliances during sunny windows.
  - **Daily Report:** Integrated predictive solar surplus recommendations into `/api/analytics` `daily_digest`.
  - **Badge:** Added `☀️ Vrij Overschot: X.X kWh` summary badge to the prediction card header.
- **Enhanced Mobile Touch & Tooltip Dismissal:**
  - Tapping anywhere outside the chart canvas immediately dismisses stuck hover tooltips.
  - Tooltip filter automatically hides zero-value lines (`SWW: 0`, `CV: 0`, `Accu: 0`), keeping touch popups clean and concise.

---

## [0.17.0] — 2026-09-08 (Solar-Prioritized Dispatch & Floating Baseline Consumption Architecture)

### Added
- **Floating Baseline Consumption Architecture:**
  - Placed local energy production (Solar PV + Battery discharge) as an underlying negative energy pool below $Y = 0$ ($[-P_{\text{gen}}, 0]$).
  - Consumers (Baseload, SWW boiler, space heating, and battery charging) dynamically stack **upwards starting from $-P_{\text{gen}}$**:
    - If total consumption $\le P_{\text{gen}}$, all consumers fit completely inside the negative zone under $Y=0$; remaining space to 0 represents exported solar/battery surplus.
    - If total consumption $> P_{\text{gen}}$, the stack crosses above $Y=0$ into positive territory, visually highlighting actual required grid import.
- **Solar Priority Dispatch Logic:**
  - SWW boiler run is strictly scheduled on peak solar generation hours ($\ge 1.0$ kW) for 100% self-consumption before falling back to lowest tariff.
  - Battery charging is prioritized on daytime solar surplus ($P_{\text{solar}} - P_{\text{load}}$) instead of buying from grid when sunny.
  - Battery discharge activates during evening peak tariff hours (€0.38 - €0.44/kWh) to displace expensive grid import.
- **Taller Visual Canvas & Unified Legend:**
  - Expanded chart container height to 420px (mobile) and 460px (desktop) so low baseloads (e.g. 300 W) are distinctly visible.
  - Removed duplicate Chart.js legend; retained clean, descriptive HTML legend chips below.

---

## [0.16.0] — 2026-09-08 (Dual-Polarity 24h Prediction & Battery Discharge Optimization)

### Added
- **Dual-Polarity 24-Hour Forecast Engine:**
  - **Negative Stack (< 0 kW):** Expected solar PV production (`solar_kw_neg`) and scheduled battery discharge (`battery_discharge_kw_neg`) plotted downward under the center axis.
  - **Battery Discharge Optimization:** Intelligent schedule activates battery discharge during peak evening tariff hours (€0.40 - €0.44/kWh) or high household demand to displace expensive grid import.
  - **Positive Stack (> 0 kW):** Baseload, SWW boiler (350L), space heating (CV), and battery charging stacked upward.
  - **Expected Net Grid Power Line Overlay (`net_power_kw`):** Continuous bold orange line showing actual power drawn from the grid ($\text{Load} - \text{Gen}$). When line is below zero, energy is exported; when above zero, imported.
  - **Dual Polarity Legend Chips:** Distinct indicators for positive consumers (+kW), negative generators (-kW), net grid power, and price overlay.

---

## [0.15.2] — 2026-09-08 (Declare Chart Variables Globally & Deep-Clone Multi-Canvas Configurations)

### Fixed
- **Global Declaration of `analyticsChartInstance`:**
  - Resolved `ReferenceError: analyticsChartInstance is not defined` by hoisting all Chart.js instances (`analyticsChartInstance`, `chartInstance`, `powerProducersChartInstance`, `electricityPricesChartInstance`) to the top of the client controller script.
- **Deep-Cloned Canvas Configuration:**
  - Prevented Chart.js runtime mutation conflicts between `hemsChartAnalytics` (Laag 4) and `hemsChart` (Laag 3) by passing deep-cloned JSON configuration objects.
  - Verified live rendering of all charts.

---

## [0.15.1] — 2026-09-08 (Fix JS Syntax Error & Synchronize Analytics All-Chart Loader)

### Fixed
- **Resolved Fatal Frontend JavaScript Syntax Error:**
  - Diagnosed and fixed a dangling syntax token in `loadChartData()` which caused the browser's JavaScript engine to halt on load and prevented any API data from fetching.
  - Verified JavaScript syntax clean with Node.js parser (0 errors).
- **Synchronized Multi-Chart Loader on Boot & Tab Change:**
  - Ensured `showTab('analytics')` and the initial boot sequence explicitly invoke all three analytics panels:
    1. `loadAnalytics()` (KPIs & Daily Digest)
    2. `loadElectricityPricesChart()` (EPEX Dual-Axis Rates & Solar Forecast)
    3. `loadPowerProducersChart()` (Live Dual-Polarity Net InfluxDB Telemetry)
    4. `loadChartData()` (24h Ahead Rolling Consumption Forecast)

---

## [0.15.0] — 2026-09-08 (24h Ahead Rolling Power Prediction & Baseload Settings)

### Added
- **24-Uurs Vermogens- & Verbruiksprognose (Rolling 24h Prediction):**
  - Gestapelde prognosegrafiek die 24 uur vooruit kijkt vanaf het huidige wandklokuur (`Nu (18:00)` t/m `Morgen 17:00`) gebaseerd op echte EPEX beurstijden (EnergyZero) en Open-Meteo zonnestraling.
  - **Gestapelde Verbruikscomponenten:**
    - **Continue Basislast:** Standaard 300 W (instelbaar).
    - **Warm Tapwater (SWW Boiler 350L):** Gepland op het voordeligste dag- of zonnepiekmoment (~1,2 kW stroomopname).
    - **Woningverwarming (CV):** Dynamisch berekend op basis van buitentemperatuur ($T_{\text{buiten}} < 15,5^\circ\text{C}$) en warmteverlies.
    - **Thuisaccu Laden:** Gepland bij economische prijsarbitrage ($\Delta P \ge €0,115/\text{kWh}$) of zonne-absorptie.
  - **Overlays:** Zonneproductie verwachting (kW) en EPEX stroomtarief (€/kWh).
- **Instelbare Continue Basislast in de GUI:**
  - Onder *Tarieven & Leveranciers* toegevoegd: **Continue Basislast Woning** (standaard `300 Watt`).
  - Eenvoudig aanpasbaar en direct opgeslagen in `heatpump_config.json` via het nieuwe `POST /api/settings` endpoint.

---

## [0.14.0] — 2026-09-08 (Dual-Axis Solar Forecast & Clean Settings Architecture)

### Added & Enhanced
- **Geïntegreerde Zonnestroom Verwachting (Dual-Axis Chart):**
  - De grafiek toont nu direct de **Verwachte Zonneproductie (kW)** van vandaag als een vloeiende gouden/gele gloedcurve op een tweede rechter Y-as (`y1` in kW).
  - Je ziet nu direct hoe de zonnepiek (vandaag tot 1,41 kW) samenvalt met de goedkopere beursstroomuren rond het middaguur (€0,1935/kWh om 13:00).
- **Opgeruimde Mobiele Header in Analyse:**
  - Het invoerveld voor zonnestroom-kosten is verwijderd uit de grafiek-header, waardoor de balk op mobiel niet meer rommelig over meerdere regels breekt.
  - De header bevat nu alleen de schone resolutie-kiezer (`Kwartiertarieven (15m)` / `Uurtarieven (1h)`) en de verversknop.
- **Centrale Instelling in 'Tarieven & Leveranciers':**
  - Een nieuwe instellingen-kaart toegevoegd onder *Tarieven & Leveranciers*: **Interne Opwek & Afschrijving Kostprijzen**.
  - Hier kan de gebruiker de *Zonnestroom Kostprijs / LCOE* (€/kWh, standaard €0,060) bewerken en opslaan. De gestippelde referentielijn in de grafiek beweegt direct mee.

---

## [0.13.0] — 2026-09-08 (EPEX Electricity Prices & Configurable Solar Cost Chart)

### Added
- **EPEX Stroomtarieven & Zonnestroom Kostprijs Grafiek (Laag 4):**
  - Nieuw interactief paneel in de analyse-weergave met live day-ahead beursstroomtarieven uit EnergyZero / EPEX Spot.
  - **Resolutie Selector:** Schakel naadloos tussen **Kwartiertarieven (15m, 96 datapunten)** en **Uurtarieven (1h, 24 datapunten)**.
  - **Instelbare Zonnestroom Kostprijs (LCOE):** Direct bewerkbaar in de GUI (standaard €0,060/kWh / 6 cent) en opgeslagen in `heatpump_config.json`.
  - **Gecombineerde Grafiek:**
    - EPEX All-in stroomtarief curve (stepped line met blauwe fill).
    - Gestippelde referentielijn voor zonnestroom kostprijs (€0,060/kWh).
    - Interactieve tooltip toont direct de netto besparing van zonnestroom t.o.v. het actuele beurstarief.
  - **Statistieken Chips:**
    - Laagste tarief van de dag (bijv. €0,1935/kWh om 13:00)
    - Hoogste tarief van de dag (bijv. €0,4390/kWh om 19:45)
    - Gemiddeld dagtarief (bijv. €0,3180/kWh)
    - Zonnestroom besparingsmarge (+€0,2580/kWh voordeel t.o.v. netstroom)

---

## [0.12.0] — 2026-09-08 (Timeframe Selector & Energy Integrals kWh Totals)

### Added
- **Interactive Timeframe Selector in GUI:**
  - Added clean dropdown selector directly in the Power Producers header:
    - `1h`: Laatste 1 uur (1-minuut resolutie)
    - `6h`: Laatste 6 uur (2-minuten resolutie)
    - `24h`: Laatste 24 uur (5-minuten resolutie, standaard)
    - `48h`: Laatste 2 dagen (15-minuten kwartierresolutie)
    - `7d`: Laatste 7 dagen (1-uurs resolutie)
  - Seamlessly re-queries `openhems` InfluxDB and dynamically updates the Chart.js canvas on change.
- **Timeframe Energy Totals (kWh) in Legend Cards:**
  - Integrated Riemann/trapezoidal energy integration across all sampled intervals:
    `Energy (kWh) = sum(Power (W) * dt (hours) / 1000)`
  - Each of the 6 metric cards below the graph displays the exact period energy total in bold (e.g. `0.38 kWh`, `2.47 kWh`) alongside `Last *` and `Min`/`Max` power values.

---

## [0.11.4] — 2026-09-08 (Fix Global CONFIG_FILE Reassignment & Add-on Flushes)

### Fixed
- **Global `CONFIG_FILE` Scope:** Reassigned `global CONFIG_FILE` in `main()` when `--config` argument is passed by Home Assistant Supervisor options, guaranteeing that the background collector reads the correct site devices configuration.
- **Unbuffered Logging (`flush=True`):** Added explicit `flush=True` to all background telemetry collector log statements for immediate diagnostic visibility in Home Assistant add-on logs.

---

## [0.11.3] — 2026-09-08 (Fix kW Unit Conversion & Device Type Matching)

### Fixed
- **Automatic kW to Watt Unit Conversion:**
  - Resolved unit mismatch where Home Assistant sensors reporting in `kW` (such as `sensor.power_consumption` and `sensor.power_production`) were previously recorded directly as fractional Watts (e.g. 0.055 W instead of 55 W), resulting in values rounding down to zero.
  - Implemented unit inspection in `sample_devices()` multiplying `kW` readings by 1000 to store canonical Watts.
- **Robust Device Type Matching:**
  - Expanded device archetype filters to properly catch all hardware definitions:
    - Solar: `solar_pv`, `solar_inverter`, `solar`
    - Thermal buffers: `thermal_buffer`, `dhw_tank`, `dhw_boiler`
    - Batteries: `battery`, `home_battery`, `battery_storage`
- **Live Non-Zero Telemetry Verified:**
  - Verified live points streaming to `openhems`: Solar ~784 W, House Consumption ~821 W, Export ~11 W, Import ~47 W.

---

## [0.11.2] — 2026-09-08 (Fix CEST Dutch Timezone & Replace fill(previous) with fill(none))

### Fixed
- **CEST Timezone Alignment:**
  - Standardized all time-axis labeling using Python's `zoneinfo.ZoneInfo("Europe/Amsterdam")` and explicit `TZ: Europe/Amsterdam` environment.
  - X-axis timestamps now align 1-to-1 with Dutch local time (CEST/CET) matching the user's phone clock.
- **Removed Artificial Rectangular Fill:**
  - Replaced `fill(previous)` with `fill(none)` and added sparse-time point merging.
  - The chart now displays actual, real data intervals without drawing artificial horizontal flat blocks across unmeasured time periods.
- **Supervisor Token & Unbuffered Python:**
  - Added `homeassistant_api: true` and `python3 -u` to ensure the background collector reliably communicates with Home Assistant and logs immediately.

---

## [0.11.1] — 2026-09-08 (Fix Collector Thread & Dynamic Window Zoom for Pure openhems)

### Fixed & Enhanced
- **Started Background Collector Thread:** Corrected server initialization in `daemon.py` to instantiate and start `HemsBackgroundCollector` on daemon boot.
- **Dynamic Time Window Zooming:** When initializing on a brand-new database with less than 24h of history, the chart dynamically zooms to the actual recording window (from first recorded point to present) rather than squashing data against 1400 empty leading intervals.
- **InfluxDB Authentication:** Ensured default connection config automatically maps to `openhems` user and database.

---

## [0.11.0] — 2026-09-08 (100% Pure openhems Datastore & Rolling Window Accumulator)

### Changed & Hardened
- **Completely Purged `hassio` Data Sources & Fallbacks:**
  - Removed all queries, configurations, and fallbacks pointing to Home Assistant's internal `hassio` database.
  - Open HEMS is now 100% self-contained and exclusively reads and writes to the canonical `openhems` InfluxDB database.
- **Layer 1 Rolling Window Accumulator (Anti-Spike & Noise Filtering):**
  - Implemented in-memory tumbling window accumulator:
    - High-frequency devices (P1 1s streams, Modbus 10s meters, and irregular HA events) are sampled every 10 seconds into a thread-safe sliding accumulator.
    - Computes mathematical arithmetic mean ($\bar{x} = \sum x_i / N$) over the industry-standard **60-second window**.
    - Flushes anti-spike, noise-filtered 1-minute averages to `openhems` exactly once per minute at aligned timestamps.
  - Power Producers Analytics queries `openhems` directly with 1-minute/15-minute group-by buckets.

---

## [0.10.0] — 2026-09-08 (Dedicated Open HEMS InfluxDB & Background Collector Engine)

### Added
- **Dedicated `openhems` InfluxDB Database & Credentials:**
  - Configured dedicated database `openhems` and user `openhems` in InfluxDB 1.8 with isolated credentials safely vaulted in `/config/open_hems_secrets.json` (chmod 0600).
- **Canonical Time-Series Datamodel:**
  - `energy_telemetry`: Standardized device-level streams (`main_grid_meter`, `rooftop_solar`, `daikin_heat_pump`, `dhw_tank`, `deye_battery`) with vector, flow polarity (`IMPORT`, `EXPORT`, `GENERATION`, `CONSUMPTION`, `STORAGE`), and units.
  - `market_tariffs`: Day-ahead/intraday electricity prices per provider.
  - `weather_forecast`: Solar irradiance and ambient weather metrics.
- **Layer 1 Background Telemetry Collector Engine (`HemsBackgroundCollector`):**
  - Runs in the daemon background (every 30s) to continuously poll configured devices, format canonical Line Protocol points, and stream real-time batches to `openhems`.
- **Hybrid Analytics Engine:**
  - The Power Producers dashboard queries `openhems` as primary datastore, with automatic backfill logic from `hassio` during the initial 24h bootstrap window.

---

## [0.9.0] — 2026-09-08 (Grafana-Style Power Producers Dual-Polarity Analytics)

### Added
- **Power Producers & Realtime Netstromen Visualisatie (Laag 4):**
  - Geïntegreerde Grafana-stijl tijdreeks-grafiek met **dubbele polariteit** direct gevoed vanuit InfluxDB (`hassio` database):
    - **Positieve as ($>0\text{ W}$):** Afname van het net (Crimson area fill `#EF4444`) en Totaal Verbruik (Oranje lijn `#F97316`) plus Opgewekt Gebruikt (Teal fill `#14B8A6`).
    - **Negatieve as ($<0\text{ W}$):** Zonnepanelen productie (Gele/olijf area fill `#EAB308`) en Teruglevering aan het net (Groene area fill `#10B981`).
  - **Identieke Grafana Legenda & Live Statistieken:**
    - Live compacte kaarten onder de grafiek voor alle 6 meetstromen met **Last \***, **Min**, en **Max** waarden (automatisch geformatteerd in W of kW).
  - **Backend Endpoint `/api/analytics/power_producers`:**
    - Haalt 144 intervallen (10-minuten resolutie over 24 uur) op via InfluxDB Line Query met authenticatie via de veilige kluis (`/config/open_hems_secrets.json`).
    - Berekent direct `Totaal Verbruik` ($P_{\text{import}} + \max(0, P_{\text{solar}} - P_{\text{export}})$) en `Opgewekt Gebruikt` ($\min(P_{\text{solar}}, P_{\text{verbruik}})$).

---

## [0.8.0] — 2026-09-08 (Architecture Streamlining: Absorb Layer 4 into Devices & Policies)

### Changed & Streamlined
- **Abolished Standalone Layer 4 (Actuation/Control):**
  - Re-allocated device-level parameters directly to **Device Hardware Adapters (Layer 1)**:
    - Minimum runtime / compressor dwell-time (anti-pendel protection).
    - Device max power ratings.
    - Local emergency comfort thresholds (e.g. DHW tank $< 38.0^\circ\text{C}$ emergency heat).
    - Physical hardware relay actuation entities (S10S / S11S).
  - Re-allocated multi-device total constraints directly to **Policy Orchestration (Layer 3)**:
    - Systeembrede 3-fasen netlimiet / Peak Shaving (max 17.250 W / 3x25A) to prevent main fuse blowouts.
    - Multi-device hydraulic interlock: CV heating lockout during SG4 hot water boost to prevent 9 kW backup heater.
    - Solar surplus cascading priority waterfall: 1) SWW Boiler $\rightarrow$ 2) Home Battery $\rightarrow$ 3) EV $\rightarrow$ 4) Grid export.
- **Streamlined 4-Layer Hierarchy in UI & Documentation:**
  - **Laag 4:** Analyse & Rapportage (KPIs, Besparingen, COP).
  - **Laag 3:** Optimalisatie & Beleid (inclusief Peak Shaving banner en multi-device orchestration).
  - **Laag 2:** Zelflerend & Fysica (Kalibratie, Offsets, Sensor downtime maskers).
  - **Laag 1:** Data, Verbindingen & Apparaten (InfluxDB, MQTT, Device Hardware Links met beveiliging).
- **Updated `ARCHITECTURE.md` and `AGENTS.md`** across the repository and add-on.

---

## [0.7.1] — 2026-09-08 (Live EPEX, P1 DSMR & Weather Ingestion)

### Added & Connected
- **EPEX Spot / EnergyZero Provider:**
  - Fully mapped to live Home Assistant sensor `sensor.energyzero_today_energy_current_hour_price` (actueel €0.2520/kWh) and direct EnergyZero Day-Ahead REST API.
  - Interactive status card with live rate readout and API endpoint inspection.
- **Open-Meteo & Weidhuis Weather Provider:**
  - Fully mapped to `weather.weidhuis` and local Wittboy GW2000A weather station `sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature` (actueel 19.3°C).
  - 48-hour global solar irradiance (GHI W/m²) forecast pipeline connected.
- **P1 DSMR Hoofdmeter:**
  - Connected as active physical Grid Meter device with `sensor.power_consumption` (actueel verbruik) and `sensor.power_production` (teruglevering).
  - Configured with 3x25A grid parameters for peak-shaving guardrails.

---

## [0.7.0] — 2026-09-08 (MQTT Device Source Adapters & Direct Bus Ingestion)

### Added
- **Multi-Source Device Adapters (Home Assistant vs. Direct MQTT):**
  - Clarified separation between physical devices (resources) and external context feeds.
  - Devices can now be configured with either:
    1. `source_type: "homeassistant"` (pulling live metrics from Home Assistant entity sensors/switches).
    2. `source_type: "mqtt"` (subscribing directly to realtime MQTT streaming topics like DSMR P1 meters, ESPAltherma, Shelly devices, and Deye inverters).
  - **MQTT Device Configuration Fields:**
    - `mqtt_broker_id`: Selectable link to any configured broker from the Layer 1 connections catalog.
    - `mqtt_power_topic`: Telemetry topic streaming live power/Wattage.
    - `mqtt_power_json_key`: Optional JSON extraction path for structured payloads.
    - `mqtt_control_topic`: Optional command topic for sending setpoint/switch payloads.
  - **Ingress UI Device Editor:** Dynamic source-type switcher in `device-modal` displaying either HA entity pickers or MQTT topic/broker inputs.
  - **Device Cards Enhancement:** Device cards explicitly show the active data source (`🏠 Home Assistant` or `⚡ MQTT Topic: <topic>`).

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.6.1 archived prior to upgrade.

---

## [0.6.1] — 2026-09-08 (Secure Secrets Vault & ID-Based Testing)

### Fixed & Enhanced
- **Isolated Secrets Vault (`/config/open_hems_secrets.json`):**
  - Moved all passwords and tokens out of public configs into an isolated vault with strict `0600` permissions.
  - Added `open_hems_secrets.json` to `.gitignore` and `secret_scanner.py` exclusion patterns, preventing accidental leakage.
  - Passwords are completely masked as `••••••••` in API payloads and DOM rendering.
- **Fixed InfluxDB Unauthorized Error on Card Testing:**
  - Resolved issue where `onclick='testSpecificInflux(${JSON.stringify(c)})'` broke on HTML attribute quoting and special characters (like section symbols `§`).
  - Switched to clean ID-based testing: `testSpecificInflux(connId)` and `testSpecificMqtt(connId)`.
  - The backend securely resolves the stored credentials from `/config/open_hems_secrets.json` using the connection ID, so tests always authenticate reliably without exposing secrets to client-side JavaScript.

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.6.0 archived prior to upgrade.

---

## [0.6.0] — 2026-09-08 (Layer 5 Analytics & Reporting & Inverted Navigation Hierarchy)

### Added
- **Laag 5: Analyse & Rapportage (`layer5_analytics/` & `view-analytics`):**
  - **Financial Savings Tracking:** Real-time calculation of dynamic load shifting savings vs. flat tariff baseloads (`calculate_daily_savings`).
  - **Solar Self-Consumption Ratio:** Dedicated calculator tracking percentage of generated PV electricity utilized directly on-site (`calculate_self_consumption_ratio`).
  - **Heat Pump Efficiency Auditing:** Seasonal COP tracking for DHW (~2.04) and space heating (~4.80).
  - **Forecast-vs-Actual Variance Engine:** Computes Mean Absolute Error (MAE) and RMSE comparing Day-Ahead EPEX/solar forecasts with verified InfluxDB hardware telemetry.
  - **Automated Digest Generator (`generate_daily_digest`):** Markdown/text digest ready for Telegram and dashboard reporting.
  - **RESTful Analytics API:** `GET /api/analytics` returning live KPI cards and daily summaries.
  - **Expanded Unit Test Suite:** `tests/unit/test_analytics.py` bringing total passing unit tests to 27/27.
- **Inverted Navigation Hierarchy (Layer 5 Down to Layer 1):**
  - Re-ordered left sidebar menu to prioritize high-value analytical outputs over low-level infrastructure:
    1. **Laag 5: Analyse & Rapportage** (Top of sidebar, default landing view)
    2. **Laag 4: Veiligheid & Relais Aansturing** (Hardware status, RAM SG-Ready S10S/S11S contacts)
    3. **Laag 3: Optimalisatie & Beleid** (24h Planning, Policy Engine, Tariffs)
    4. **Laag 2: Zelflerend & Fysica** (Kalibratie & Offsets)
    5. **Laag 1: Data & Verbindingen** (Apparaten, InfluxDB / MQTT CRUD, Open APIs — at the bottom)

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.5.1 archived prior to upgrade.

---

## [0.5.1] — 2026-09-08 (Responsive Mobile UI & Off-Canvas Navigation Drawer)

### Added
- **Full Mobile-Friendly Responsiveness (Ingress & Handheld Viewports):**
  - Converted sidebar navigation into an off-canvas responsive drawer on screens `< 768px`:
    - Automatically hides off-screen (`-translate-x-full`) with a smooth 300ms CSS slide animation.
    - Added backdrop overlay (`#sidebar-backdrop`) for backdrop clicks.
    - Added mobile-only hamburger button (`☰`) in the header and close button (`✕`) inside the drawer.
    - Auto-closes the drawer upon selecting any menu navigation tab.
  - **Fluid Content Layout:**
    - Main viewport takes full width (`w-full`), eliminating the squeezed ~40% width column issue on phones.
    - Optimized padding (`p-3 sm:p-5 md:p-8`) giving cards full room on narrow screens.
    - Grid cards collapse cleanly to 1 column on mobile, expanding to 2-3 columns on desktop.
    - Action buttons and metric badges wrap cleanly (`flex-wrap`).
  - **Responsive Modals:**
    - All CRUD modals constrained to `w-full max-w-lg max-h-[90vh] overflow-y-auto` with backdrop blur, preventing overflow beyond phone screens.

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.5.0 archived prior to upgrade.

---

## [0.5.0] — 2026-09-08 (4-Layer Modular Monorepo Architecture & Agent Scoping)

### Added
- **4-Layer Contract-First Modular Monorepo Architecture:**
  - **Overarching Architecture (`ARCHITECTURE.md`):** Comprehensive blueprint defining system boundaries, data flows, and layer responsibilities.
  - **Per-Layer Agent Specifications (`AGENT_SPEC.md`):** Explicit rules and scopes so AI agents can develop on a single layer without side effects.
    1. **`layer1_data_collection/` (Ingestion & Connectivity):** Network I/O, InfluxDB Line Protocol, MQTT brokers, feed fetchers. Strictly isolated from optimization and actuation.
    2. **`layer2_calibration/` (Physics & Empirical Modeling):** Pure Python/NumPy statistics, solar matrix $K(h)$, building $UA_{\text{base}}$, defrost penalty, 80/20 EMA damping, physical clamping. Zero network I/O.
    3. **`layer3_scheduling/` (Optimization & Policies):** Multi-vector solver evaluating `ShiftableConsumerPolicy`, `ThermalBufferPolicy`, and `BatteryArbitragePolicy` (with economic deadband $\Delta P \ge €0.115/\text{kWh}$). Generates `ScheduleSlot` dispatches.
    4. **`layer4_control/` (Actuation & Hardware Safety Guard):** Translates dispatches into volatile RAM Smart Grid contact states (S10S/S11S), enforces 20-minute compressor dwell-time locks, guarantees hydraulic exclusivity (disabling space heating switch during DHW boost to eliminate 9 kW BUH), and Priority 1 emergency comfort ($< 38^\circ\text{C}$).
- **Formal Interfaces (`interfaces.py`):** Abstract contracts per layer decoupling implementation from interfaces.
- **Automated Unit Test Suite Expanded:** Added `tests/unit/test_controller.py` bringing test coverage to 23/23 passing unit tests.

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.4.1 archived prior to upgrade.

---

## [0.4.1] — 2026-09-08 (Multi-Instance InfluxDB & MQTT CRUD Console)

### Added
- **Multi-Instance CRUD for Time-Series Storage & Streaming Brokers:**
  - Open HEMS is now 100% decoupled from single-instance or local-only assumptions.
  - **InfluxDB Instances (`/api/infrastructure/influxdb`):**
    - Users can add, edit, test, and delete multiple InfluxDB connections (e.g. local HA InfluxDB 1.8, remote dedicated servers, or InfluxDB 2.x Cloud with token/org).
    - Dedicated connection modal with live connection tester (`testModalInflux`) reporting database existence, series count, and latency in ms.
    - Selectable default/active storage instance.
  - **MQTT Message Brokers (`/api/infrastructure/mqtt`):**
    - Users can add, edit, test, and delete multiple MQTT broker endpoints (e.g. local Home Assistant Mosquitto, external cloud brokers, or dedicated IoT gateways).
    - Dedicated broker modal with TLS/SSL toggle, base topic prefix, client ID, credentials, and live protocol handshake tester (`testModalMqtt`).
    - Selectable default/active broker.
  - **Live Multi-Instance UI Console:**
    - Visual grid of configured InfluxDB and MQTT connection cards in the `Verbindingen & Opslag (Laag 1)` tab.
    - Per-card `Testen`, `Bewerken`, and `Verwijderen` action buttons with real-time status badges.

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.4.0 archived prior to upgrade.

---

## [0.4.0] — 2026-09-08 (Solidified Data Collection Layer: InfluxDB & MQTT)

### Added
- **Laag 1 Dataverzameling & Connectiviteit Console (`view-infrastructure`):**
  - **InfluxDB Tijdreeksdatabase:**
    - Full configuration support for InfluxDB 1.8 / 2.x (Host/URL, Data Opslag DB `hermes`, Home Assistant Lees DB `hassio`, credentials, retention policy).
    - Dedicated live connection test API (`POST /api/infrastructure/influxdb/test`): executes live `/ping` and `SHOW MEASUREMENTS`, returning latency (ms), database status, and measurement count.
    - Native nanosecond-precision InfluxDB Line Protocol writer (`POST /api/infrastructure/write-test-point`): direct verified telemetry writes (`204 No Content`).
  - **MQTT Message Broker:**
    - Full configuration support for MQTT brokers (Host `core-mosquitto`, Port `1883`, Base Topic Prefix `openhems`, Client ID, credentials).
    - Native protocol connection tester (`POST /api/infrastructure/mqtt/test`): executes TCP socket connect and MQTT 3.1.1 `CONNECT` packet handshake, interpreting broker return codes (RC 0 OK, RC 4 Bad User/Pass, RC 5 Not Authorized, Connection Refused, Timeout).
  - **Live Telemetrie & Data-Inname Monitor:**
    - Real-time display of registered series in `hassio` (2.330 series) and `hermes` telemetry storage.
    - Interactive "Schrijf Test Telemetrie" button for verified real-data writes.
- **RESTful Infrastructure API:**
  - `GET /api/infrastructure`: retrieves active connectivity settings.
  - `POST /api/infrastructure/influxdb`: saves InfluxDB configuration.
  - `POST /api/infrastructure/mqtt`: saves MQTT broker configuration.
  - `GET /api/infrastructure/telemetry-stats`: queries real database series stats.

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.3.2 archived prior to upgrade.

---

## [0.3.2] — 2026-09-07 (Policy-to-Device Multi-Selector & Bidirectional Mapping)

### Added
- **Bidirectional Policy-to-Device Selector:**
  - Added multi-checkbox device selector to the Policy editor modal (`modal-pol-devices-list`), dynamically listing all configured HEMS devices with their resource type.
  - Target device assignments (`target_devices`) are persisted via RESTful API (`POST` / `PUT /api/policies/<id>`).
  - Policies UI now displays friendly device badges (e.g. `Daikin Altherma 3 H HT`, `Warm Tapwatervat 350L SWW`) rather than raw IDs.
  - Devices UI now explicitly indicates which policy or policies currently control each physical device (e.g. `Beleid: 350L SWW Boiler Buffer Beleid` or `Geen beleid gekoppeld (stand-by)`).

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.3.1 archived prior to upgrade.

---

## [0.3.1] — 2026-09-07 (Decoupled Policy Engine & 3 Policy Archetypes)

### Added
- **Decoupled Policy Engine from Device Hardware:**
  - Devices are now strictly physical resources (HA entity links, power ratings, capacity).
  - Policies are independent orchestrators controlling multiple resources based on economics, comfort, and safety.
- **Three Fundamental Policy Archetypes:**
  1. `ShiftableConsumerPolicy` (Verbruik zonder opslag): For dishwashers, washing machines, dryers, EV chargers. Configurable window, duration, power rating, interruptibility, and solar threshold.
  2. `ThermalBufferPolicy` (Buffer zonder teruggave): For 350L DHW boilers and space heating. Enforces Priority 1 emergency comfort threshold (< 38°C overrules everything), economic reheat threshold (< 46°C prevents unneeded cycling), standard 50°C target, 60°C solar/dal boost, morning/evening peak lockouts (SG1), and CV isolation during DHW runs to eliminate 9kW BUH resistance heaters.
  3. `BatteryArbitragePolicy` (Accu met teruggave): For hybrid inverters and home batteries. Introduces the **Economic Deadband (Dode Zone)**: evaluates round-trip conversion loss (13%) and LCOS cell degradation (€0.0741/kWh). If $\Delta P < €0.115/\text{kWh}$, the battery enters `HOLD / STANDBY` to avoid loss-making cycles. Free solar surplus charges the battery before grid arbitrage.
- **RESTful Policies CRUD API:** `/api/policies` with `GET`, `POST`, `PUT`, and `DELETE`.
- **Ingress UI Navigation & Modals:** Dedicated "Beleid & Policies" tab with archetype-specific modal editors.

### Security & Deployment
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.3.0 archived prior to upgrade.

---

## [0.3.0] — 2026-09-07 (Generic Framework & Chart.js Stacked Visualizer)

### Added
- **Clean Slate Generic Framework Architecture:**
  - Removed all hardcoded site/device logic from core; all components are now dynamically instantiated entities.
  - Standard pre-configured Open APIs: EPEX Spot (EnergyZero) for day-ahead/quarter-hourly pricing and Open-Meteo for solar/weather forecasts.
- **Full CRUD for Energy Suppliers / Tariffs:**
  - Pluggable tariff entities (Powerpeers, Tibber, NextEnergy, fixed/dynamic) with import/export markup, tax, and interval settings (`15m` / `1h`).
- **Full CRUD for Devices & Consumers with Policies:**
  - Dynamic device configuration with selectable operating policies: `solar_first`, `cheapest_hours`, `peak_avoidance`, `comfort_priority`, `arbitrage_and_solar`.
  - **Home Assistant Entity Auto-Discovery & Dropdown Selector:** Direct integration with Home Assistant Core API (`/api/ha/entities`) to select live sensors (`sensor.*power*`, `sensor.*watt*`, `sensor.*temp*`) and switches (`switch.*`).
- **Interactive 24-Hour Stacked Bar Chart (Chart.js):**
  - Hour-by-hour stacked consumption bars: Baseload, Heat Pump / SWW Boiler, Battery Charging, EV.
  - Overlay curves for Solar Production (kW) and Dynamic Electricity Price (€/kWh).
  - Dynamic Recommendation Balloons / Callout Banners highlighting the cheapest hours of the day and peak solar surplus moments.
- **Adopted Stitch Obsidian Dark-Mode Design System:**
  - Deep `#080B11` / `#0E1422` theme, custom SVG icons, responsive layout.

### Security
- Automated pre-commit secret scan verified: 0 credentials.
- Automatic snapshot v0.2.2 archived prior to upgrade.

---

## [0.2.2] — 2026-09-07 (Stitch Design System Integration)

### Added
- **Canonical Data Model (`models/canonical.py`):**
  - First-class primitives for `Vector` (Electricity, Heat, Gas, Water) and `Flow` (Import, Export, Production, Consumption, Storage).
  - Strongly typed `Measurement` with provenance tracking (`Quality`: `good`, `stale`, `excluded`).
  - Resource capabilities abstraction (`DeviceCapability`, `DeviceState`, `DeviceCommand`) with deterministic priority arbitration (1 = Emergency, 2 = Comfort, 3 = Economic).
- **5-Layer Architecture Modularization:**
  - `layer1_data_collection`: Multi-tier API fetcher, atomic file cache, 4h freshness validation, exponential backoff (up to 45s).
  - `layer2_calibration`: Self-tuning empirical solar tilt/shading profile $K(h)$, building loss OLS regression ($UA_{\text{base}}$, $c_{\text{wind}}$, $c_{\text{solar}}$), 350L SWW standby cooling loss calibration.
  - `layer3_scheduling`: 24h power waterfall slotter, morning/evening peak lockouts (SG1), and automated publishers (InfluxDB `hermes` db, Google Sheets, Home Assistant).
- **Hardware Longevity & Safety Guardrails:**
  - Daikin EEPROM protection: Smart Grid control routed strictly through S10S/S11S binary contacts operating in volatile RAM (0 flash wear).
  - Hydrobox BUH isolation: `switch.hc_mode_altherma_on` is turned OFF prior to DHW forced runs, preventing 9 kW resistive heaters from activating for space heating.
  - Dual DHW triggers: Scheduled optimal peak run + immediate $38^\circ\text{C}$ emergency comfort threshold.
- **Data Quality & Hardware Masking:**
  - Integrated `sensor.warmtepomp_power` (Modbus 3-phase compressor power meter).
  - Added declarative `data_exclusion_windows` in configuration; automatically excludes the disconnected meter period (2026-07-15 to 2026-09-04) from all calibration regressions.
- **Home Assistant Add-on Packaging:**
  - Packaged as a standalone Home Assistant Add-on (`open_hems`) with `config.yaml`, `Dockerfile`, `build.yaml`, and dark-mode Ingress Web UI on port 8099.
- **Declarative Site Configuration:**
  - Added `config/site_example.yaml` demonstrating how external users can define custom meters, inverters, heat pumps, and tariffs.
- **Comprehensive Documentation:**
  - Created official Google Doc: *HEMS Architectuur & Technisch Ontwerp*.

### Changed
- Decoupled physical thermodynamic modeling (`HeatPumpCore`) from raw network I/O (`EnergyDataCollector`).
- Upgraded EnergyZero API fetcher to support both hourly and quarter-hourly dynamic tariffs.
- Synchronized dual time input entities (`input_datetime.geplande_starttijd_boiler` and `input_datetime.heatpump_optimal_runtime`) to eliminate midday trigger collisions.

### Security
- Locked sensitive configuration files to `chmod 600`.
- Ensured zero secrets, tokens, or PII are committed to Git.
- Strict No-Mock-Data Policy: raises `DataUnavailableError` instead of fabricating synthetic solar curves.

---

## [0.1.0] — 2026-09-03 (Initial Working Prototype)
- Initial Daikin Altherma 3 H HT DHW runtime optimizer script.
- Static daily cronjob and Google Sheets export.
- Smart Grid relay switching via Home Assistant REST API.
