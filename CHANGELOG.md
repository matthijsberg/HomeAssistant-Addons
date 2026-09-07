# Changelog — Open HEMS

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to our **4-Tier Semantic Versioning Specification**:
- **Tier 1 (MAJOR `X.0.0`):** Breaking architectural changes, core framework rewrites, fundamental data model shifts.
- **Tier 2 (MINOR `x.Y.0`):** Public releases published to GitHub with user-facing features, adapters, and release notes.
- **Tier 3 (PATCH `x.y.Z`):** Stable bug fixes, security patches, and localized component enhancements.
- **Tier 4 (DEV/INTERNAL `x.y.z-dev.N`):** Incremental development and internal test iterations.

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
