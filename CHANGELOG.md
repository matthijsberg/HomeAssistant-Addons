# Changelog — Open HEMS

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to our **4-Tier Semantic Versioning Specification**:
- **Tier 1 (MAJOR `X.0.0`):** Breaking architectural changes, core framework rewrites, fundamental data model shifts.
- **Tier 2 (MINOR `x.Y.0`):** Public releases published to GitHub with user-facing features, adapters, and release notes.
- **Tier 3 (PATCH `x.y.Z`):** Stable bug fixes, security patches, and localized component enhancements.
- **Tier 4 (DEV/INTERNAL `x.y.z-dev.N`):** Incremental development and internal test iterations.

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
