# Changelog — Open HEMS

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to our **4-Tier Semantic Versioning Specification**:
- **Tier 1 (MAJOR `X.0.0`):** Breaking architectural changes, core framework rewrites, fundamental data model shifts.
- **Tier 2 (MINOR `x.Y.0`):** Public releases published to GitHub with user-facing features, adapters, and release notes.
- **Tier 3 (PATCH `x.y.Z`):** Stable bug fixes, security patches, and localized component enhancements.
- **Tier 4 (DEV/INTERNAL `x.y.z-dev.N`):** Incremental development and internal test iterations.

---

## [0.2.0] — 2026-09-06 (Public Release — Generic HEMS Foundation)

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
