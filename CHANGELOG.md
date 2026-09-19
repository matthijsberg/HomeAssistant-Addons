# Changelog — Open HEMS

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to our **4-Tier Semantic Versioning Specification**:
- **Tier 1 (MAJOR `X.0.0`):** Breaking architectural changes, core framework rewrites, fundamental data model shifts.
- **Tier 2 (MINOR `x.Y.0`):** Public releases published to GitHub with user-facing features, adapters, and release notes.
- **Tier 3 (PATCH `x.y.Z`):** Stable bug fixes, security patches, and localized component enhancements.
- **Tier 4 (DEV/INTERNAL `x.y.z-dev.N`):** Incremental development and internal test iterations.

---

## [0.103.57] — 2026-09-19 (DHW Parameter Flow End-to-End & Empirische k_out Validatie)

### Deel A: Parameterbestand Aansluiten & Calibrator Behoud
- **Parameter- & Buitentemperatuurdoorvoer:**
  - Alle 8 aanroepen van `DhwTankSpec.get_electric_power_kw` in `dhw_optimizer.py` en `dhw_plan_adapter.py` geven nu expliciet `params=model_parameters` en de actuele/voorspelde `outdoor_temp_c` per slot door.
  - Hiermee is gewaarborgd dat parameterwijzigingen in `heatpump_model_parameters.json` direct doorwerken in de solver en gepubliceerde plannen.
- **Calibrator Behoudt Onbekende Blokken:**
  - In `layer2_calibration/calibrator.py` (`ModelCalibrationEngine`) overschrijft een kalibratieronde niet langer onbekende sleutels; bestaande blokken zoals `dhw_cop`, `dhw_power`, `building`, etc. worden behouden via een veilige samenvoeging.
- **Live Parameterbestand Geharmoniseerd:**
  - `/config/heatpump_model_parameters.json` bevat nu zowel `dhw_cop` als `dhw_power` met gevalideerde waarden. Automatische backup `.json.bak` aangemaakt.
- **Zichtbare Fallback-Waarschuwingen:**
  - `get_dhw_power_params` en `get_dhw_cop_params` in `models/physics.py` loggen nu een duidelijke waarschuwing (`[WARN]`) zodra er op ingebouwde standaardwaarden wordt teruggevallen.
- **Acceptatietests:**
  - `tests/unit/test_dhw_power_params_integration.py` toegevoegd met 4 gerichte acceptatietests:
    - `test_power_params_are_read_from_file`
    - `test_calibration_preserves_unknown_blocks`
    - `test_live_params_file_contains_dhw_blocks`
    - `test_outdoor_temperature_reaches_power_function`

### Deel B: Empirische Validatie van k_out
- **Volledige Jaaranalyse (365 dagen telemetrie in InfluxDB openhems):**
  - $N = 2.365$ stationaire 5-minuten intervallen met actieve buren ($P > 800\text{ W}$, $30 \le T_{\text{tank}} \le 65^\circ\text{C}$, $-20 \le T_{\text{out}} \le 45^\circ\text{C}$).
  - Buitentemperatuurspreiding $17{,}5\text{ K}$ ($P_{05} = 0{,}9^\circ\text{C}$, $P_{95} = 18{,}4^\circ\text{C}$ $\ge 15\text{ K}$).
  - Koudste kwintiel $474$ intervallen, warmste kwintiel $473$ intervallen ($\ge 30$).
  - Simultane meervoudige OLS-regressie toont $k_{\text{out}} = +0{,}0570\text{ kW/K}$ ($t = -22{,}61$, 95% CI $[0{,}0520 ; 0{,}0619]$, $p < 10^{-15}$).
  - Alle vier de statistische acceptatiecriteria zijn behaald. De fysica verklaart de negatieve correlatie (minder compressielift nodig bij hogere verdampingstemperaturen).
- **Herhaalbaar Kalibratiescript:**
  - `scripts/calibrate_dhw.py` uitgebreid met `--mode power`, `--dry-run`, `--write` en geautomatiseerde criteria-verificatie.

---

## [0.103.56] — 2026-09-19 (Dynamisch DHW Compressorvermogen & WP7 Autonome Baseline Simulator)

### Fysische Modellering & Dynamisch Compressorvermogen
- **Dynamische Compressormodellering $P_{\text{el}}(T_{\text{tank}}, T_{\text{out}})$:**
  - In `models/physics.py` is de canonieke pure functie `dhw_electric_power_kw` geïmplementeerd en gekoppeld aan `DhwTankSpec.get_electric_power_kw()`.
  - Vervangt het starre 3,0 kW blok door een empirisch gekalibreerde functie over 60 dagen telemetrie (97 DHW runs in InfluxDB):  
    $P_{\text{el}}(T_{\text{tank}}, T_{\text{out}}) = \text{clamp}(2{,}72 + 0{,}074 \cdot (T_{\text{tank}} - 50) - 0{,}005 \cdot (T_{\text{out}} - 10), 1{,}6, 3{,}5)$.
  - Startfase bij 40°C trekt $\sim 1{,}98\text{ kW}_{\text{el}}$, basisfase bij 50°C $\sim 2{,}72\text{ kW}_{\text{el}}$, en vollastboost bij 60°C $\sim 3{,}46\text{ kW}_{\text{el}}$.
  - Configuratieblok `dhw_power` toegevoegd aan `heatpump_model_parameters.json`.

### Autonome Baseline & Actuatiemodel (WP7)
- **Autonome Thermostaat Simulator (`dhw_baseline.py`):**
  - Pure simulator `simulate_autonomous()` ingebouwd die het zelfstandige gedrag van de warmtepomp nabootst (aanslaan bij $T \le \text{setpoint} - 10\text{ K}$, afslaan bij $\text{setpoint}$, spitsblokkade-respect).
  - De optimizer bewaakt dat een gepubliceerd plan altijd $J \le J_{\text{baseline}}$ behaalt; indien ingrijpen niet loont, wordt het zelfstandige plan overgenomen.
  - Tegenfeitenlaag vergelijkt nu zuiver tegen de echte autonome baseline in plaats van een vat dat doorkoelt naar kamertemperatuur.

---

## [0.103.55] — 2026-09-19 (DHW Traject Horizon 24h/48h Slicing & Sticky Controls Fix)

### Frontend & Dashboard Fixes
- **DHW Temperatuurtraject Horizon Filter (24u vs 48u):**
  - `/api/model/dhw-status` snijdt het traject (`temperatures_c`, `p05`, `p95`, `demand_kwh_th`) nu exact af op basis van de opgevraagde horizon (`96` slots voor `24h`, `192` slots voor `48h`).
  - De boilervat-grafiek op het tabblad Voorspelling toont nu exact 24 uur wanneer 24u geselecteerd is, en 48 uur bij 48u.
- **Sticky Filterbalken Fix (Voorspelling & Historie):**
  - De sticky navigatie- en filterbalken op de tabbladen Voorspelling en Historie hebben nu `top-16 md:top-20 z-20` (in plaats van `top-0`), waardoor ze tijdens het scrollen perfect onder de vaste paginaheader blijven plakken zonder erachter te verdwijnen.

---

## [0.103.54] — 2026-09-19 (Home Assistant Input Select Smart Grid Actuator & Idempotente Guard)

### Actuatie & Relaisstabiliteit (Stap 1 & 2)
- **Actuatie via `input_select.warmtepomp_smart_grid_modus`:**
  - `make_daikin_ha_actuator()` stuurt nu primair `input_select.select_option` aan met de canonieke HA-opties (`"Automatisch"`, `"Geadviseerd aan"`, `"Geforceerd aan"`, `"Geforceerd uit"`).
  - Home Assistant's automatie `warmtepomp_smart_grid_control` schakelt vervolgens beide UniPi-relais (S10S en S11S) atomair om.
- **Idempotente Toestandcheck (0 Relaisklapperen):**
  - Vóór elke serviceaanroep (select, switch, climate) controleert de actuator of de huidige toestand in Home Assistant al overeenkomt met de gewenste toestand.
  - Voorkomt dat er elke 60 seconden onnodige schakelcommando's naar Home Assistant of UniPi worden verstuurd.
- **Enum-naar-String Normalisatie:**
  - `cur_slot.mode_code` wordt expliciet genormaliseerd naar lowercase string (strip van `StandardizedState.`), waardoor `forced_on` en `max_on` niet meer onbedoeld terugvallen naar `normal`.
- **Sanering van Conflicterende HA Automaties:**
  - `automation.warmtepomp_smart_grid_control` is ingeschakeld om het input_select netjes te vertalen naar S10S/S11S.
  - Oude legacy automatie `warmtepomp_smart_grid_hems_klimaat_regeling` en excess energy automatiseringen blijven gedeactiveerd.

---

## [0.103.53] — 2026-09-19 (DHW Comfortmarge Configureerbaar + Solver-, Uitleg- & Grafiekfixes)

### DHW Solver Interpolatie & Boundary Fixes (WP1)
- **Oplossing van de 0,25°C Grenskruip:**
  - `dhw_optimizer.py` interpoleert stap 8b en de forward pass nu via `_interp_finite()` uitsluitend over eindige $V$-waarden.
  - Voorkomt dat de comfortgrens bij harde lockouts, `OFF_DWELL` of counterfactual lockouts met $0{,}25^\circ\text{C}$ per geblokkeerd kwartier omhoog kruipt.
  - Onjuiste meldingen als "noodzakelijk om comfort te behouden" verdwijnen wanneer uitstel fysisch perfect haalbaar is.

### Boilerspec uit Config & Modelparameters (WP6)
- **Dynamische Inlezing:**
  - `CentralPlanner.plan()` en `ensure_active_canonical_plan()` lezen `DhwTankSpec.from_config(cfg)` en `model_parameters` direct dynamisch in.
  - Hardcoded klasseconstanten (1,8 / 2,4 kW) opgeruimd uit alle API-routers en evaluators; `models/physics.py` leest `dhw_cop`-blok via `load_dhw_cop_params()`.

### Tapvraag & Doelfunctie J Uitleglaag (WP2 & WP3)
- **Zichtbare Tapbalken:** `demand_kwh_th` en `demand_p95_kwh_th` worden nu netjes meegenomen in de trajectory van de optimizer en getoond in de UI.
- **Eerlijke Doelfunctie-Vergelijking:**
  - Alle tegenfeiten (`cap50`, `delay`) vergelijken plannen nu op basis van de volledige doelfunctie $J$ (stroom + startkosten minus restwarmte) in plaats van alleen stroomkosten.
  - Financiële kaart toont de exacte formule: `Plan 48u: stroom €X, starts €Y, restwarmte −€Z → netto €J.`

### Configureerbare Comfortmarge (WP4 & WP5)
- **Modus P50 / P95 / Vast:**
  - Configureerbaar via `dhw_optimizer.comfort_margin` in `heatpump_config.json`, de nieuwe REST API (`GET`/`POST /api/settings`) en de UI-instellingentab.
  - Matthijs' site-config staat op `mode: "p50"`, `min_margin_c: 0.5`.
  - Tekstverwoording opgeschoond: noemt nu het eerste kruispunt onder de effectieve grens in plaats van het onreële 48-uurs minimum.

---

## [0.103.52] — 2026-09-19 (Fysische DHW Kalibratie & Vermogens-/COP-Herijking)

### Fysische Kalibratie & DHW Vermogens-/COP-Correctie (WP6)
- **Triple-Check & Empirische Kalibratie uit InfluxDB:**
  - Uit analyse van 382 historische DHW-runs in InfluxDB blijkt dat een tapwaterrun gemiddeld slechts 36–40 minuten duurt (nooit 2 uur) en dat het 350L vat tijdens runs met 12 tot 15 °C per uur opwarmt ($\approx 5{,}5 - 6{,}2\text{ kW}_{\text{th}}$).
  - De Daikin Altherma trekt tijdens DHW-vollast consistent $\sim 2{,}89\text{ kW}_{\text{el}}$ ($\approx 3{,}0\text{ kW}_{\text{el}}$).
  - Bij $3{,}0\text{ kW}_{\text{el}}$ en $\sim 6{,}0\text{ kW}_{\text{th}}$ hoort een werkelijke $\text{COP} \approx 2{,}0$ bij 50°C (OLS fit op 345 runs geeft $\text{COP}_{50} = 1{,}902$).
- **Gezamenlijke Correctie Vermogen & COP:**
  - Elektrisch vermogen in `DhwTankSpec` verhoogd van $1{,}8\text{ kW}$ naar $3{,}0\text{ kW}$.
  - Basis-COP in `data/heatpump_model_parameters.json` en `models/physics.py` verlaagd van $2{,}85$ naar $2{,}0$ met nieuw configureerbaar `dhw_cop`-blok (`cop_50: 2.0`, `k_t: 0.07`, `k_out: 0.05`, bounds `[1.4, 3.2]`).
- **Impact op Getoonde Cijfers (Factor 1,67 Correctie):**
  - Alle kWh en euro's per tapwaterrun in de UI waren tot nu toe een factor $1{,}67$ ($3{,}0 / 1{,}8$) te laag. Run 1 van vannacht toonde bijvoorbeeld $1{,}35\text{ kWh}$ en €$0{,}24$, maar kostte in werkelijkheid ongeveer $2{,}25\text{ kWh}$ en €$0{,}38$.
  - De temperatuurvoorspellingen en trajectories klopten fysisch al wel, doordat de oude aanname $1{,}8\text{ kW} \times 2{,}85\text{ COP} = 5{,}13\text{ kW}_{\text{th}}$ toevallig nagenoeg gelijk was aan de werkelijke output van $3{,}0\text{ kW} \times 2{,}0\text{ COP} = 6{,}0\text{ kW}_{\text{th}}$ minus stilstandsverliezen.
- **Guardrail & CI:**
  - Nieuwe consistentietest `test_dhw_warming_rate_consistency` in `tests/unit/test_physics.py` garandeert dat de gesimuleerde opwarmsnelheid binnen 25% van de empirische $13{,}5^\circ\text{C}/\text{uur}$ blijft.
  - Kalibratiescript `scripts/calibrate_dhw.py` uitgebreid met automatische rapportage van gemiddelde run-duur, elektrisch vermogen en opwarmsnelheid.

---

## [0.95.1] — 2026-09-15 (Security Audit & Vulnerability Remediation)

### Security Hardening
- **Subprocess Command Elimination:** Removed external shell subprocess execution in `/api/schedule/recalculate`; replaced with safe in-process `ensure_active_canonical_plan(force_refresh=True)`.
- **MQTT Credential Vaulting:** Implemented secure vaulting for MQTT passwords into `open_hems_secrets.json` (0600 permissions); stripped plaintext credentials from public `heatpump_config.json` and masked in API JSON responses with `••••••••`.
- **Strict Path Traversal Protection:** Hardened static file resolution in `daemon.py` using Python 3.9+ `file_path.is_relative_to(WEB_DIR.resolve())` to prevent path traversal prefix bypasses.
- **InfluxQL DoS Clamping:** Clamped user `limit` parameters to `min(500, max(1, limit))` in decision audit queries to prevent memory exhaustion.

### Architectural Refactor & Single Source of Truth (Invariant 1)
- **Centralized Decision Contracts:** Moved all financial and trajectory decision evaluation out of HTTP router handlers into `CentralPlanner.plan()` and `DHWPlanSummary.decision_details`. Zero ad-hoc financial calculations or tariff multiplications in routers.
- **DHW Physical Draw-Off Engine:** Extracted First Law thermodynamic water draw-off calculations ($Q_{tap} = Q_{in} - Q_{standby} - \Delta E_{tank}$) to `layer2_calibration/dhw_thermal_model.py`.
- **Dedicated Model Validator:** Created `layer2_calibration/model_validator.py` encapsulating normalized MAE and volumetric energy accuracy scoring.
- **Daemon.py Decoupling:** Stripped 1,600+ lines of duplicate helper methods from `daemon.py`, reducing it to a clean ~790-line HTTP server and background thread orchestrator.
- **Frontend Modularization:** Extracted 6,000+ lines of JavaScript from `web/index.html` into `web/js/app.js`, reducing `web/index.html` to a clean 3,100-line layout template.

### Bug Fixes & Tariff Alignment
- Fixed DHW decision box calculation to use real-time solar surplus blend (€0.065-€0.094 export value) and actual night dal tariffs (€0.300) rather than comparison against forbidden hard-lockout peak prices.
- Fixed historical DHW draw-off telemetry display in `dhwHistoryChart` so tapping events are rendered as light blue bars.

### Architecture & Modularization
- **Modular Domain Routers:** Decoupled 3,000+ lines of monolithic `if path == ...` routing in `daemon.py` into 4 dedicated domain routers under `api/`:
  - `api/routes_analytics.py`: Real-time energy telemetry, day-ahead electricity prices, power producers, DHW history, decisions audit, and validation overlay.
  - `api/routes_model.py`: 2R1C space heating thermal trajectories, 350L DHW status, parameter recommendations (GET/POST), retraining, model decomposition, and unallocated baseline.
  - `api/routes_schedule.py`: 24h rolling dispatch chart data, control status, policies CRUD, exclusion windows, and optimizer recalculation triggers.
  - `api/routes_system.py`: Single Source of Truth health checks, OpenAPI schema, external providers, device/tariff CRUD, and multi-broker infrastructure connectivity.
- **Shared Service Context:** Extracted pure helper functions and plan generation to `api/context.py`, eliminating circular dependencies.
- **Drastic Monolith Shrinkage:** `daemon.py` shrunk from **14,561 lines down to 2,410 lines (-83.4% overall)**, focusing solely on server bootstrap, MQTT subscriptions, and background data collection.
- **Purity Guardrail Extension:** Updated `tests/architecture/test_ast_handler_purity.py` to continuously verify Dumb View invariants across the new router modules.

---

### Architecture
- **Frontend Decoupling:** Extracted 9,195 lines of raw HTML, Tailwind CSS, and JavaScript from `daemon.py` into a dedicated, clean `web/index.html` static asset directory.
- **Drastic File Size Reduction:** `daemon.py` shrunk from **14,561 lines down to 5,396 lines (-63%)**, separating web presentation from core HTTP routing, InfluxDB telemetry, and background dispatch threads.
- **Static File Serving:** Implemented static asset delivery (`_serve_spa`, `_serve_static_file`) with proper MIME types and cache headers.
- **Integrity Gate Alignment:** Updated `scripts/verify_data_integrity.py` to validate design tokens and frontend engine directly against `web/index.html`.

---

### Added
- **Visual Forced-Off Spitsblok Bands in DHW Chart:** Added custom Chart.js vertical overlay plugin to `chart-dhw-temperature` that projects active `forced_off` hard peak lockout intervals as translucent red vertical background bands (`rgba(239, 68, 68, 0.16)`) with dashed borders and top lock labels (`🔒 SPITSBLOK`).
- **Strictly Forced-Off Only:** Advisory/recommended off (`advised_off`) intervals remain transparent so hard lockouts stand out unambiguously.
- **DHW Legend Indicator:** Added `[Spitsblok 🔒]` badge to the DHW temperature chart legend.

---

### Fixed
- **Direct Thermal OLS Regression:** Replaced the post-hoc static COP multiplier (`slope * 3.8`) in `retrain_from_openhems` with direct day-by-day thermal heat regression ($Q_{th,day} = E_{el,day} \times \text{COP}(T_{out,day})$) against temperature lift $\Delta T = (19.5 - T_{out})$.
- Mathematically eliminates distortion between cold days (low COP, high kWh) and mild days (high COP, low kWh), recovering building insulation ($UA_{base}$ in W/K) directly from physical heat transfer principles.

---

### Fixed
- **Recommendation Status & Action Button State:** Swapped out inactive "Afwijzen" and "Accepteren & Toepassen" buttons when parameters are already accepted, replacing them with a confirmed badge (`Geaccepteerd & Actief (hh:mm)`) and a `🔄 Nieuwe Kalibratie` button.
- **Live Active Value Synchronization:** Synchronized `current_value` in `/api/model/recommendations` with the actual operating parameters from `heatpump_model_parameters.json` and reset drift to 0% once accepted.
- **Immediate Button Feedback:** Added active spinner states (`⏳ Bezig...`) on click and auto-refreshed the card immediately.

---

### Fixed
- **Macro-Clustering of Split Peak Rungs:** Overcame fragmented peak detection where temporary 30-45 min price ripples split an evening peak into multiple small lockouts. Adjacent candidate slots within the same spits window (gap $\le 60$ min) are now merged into one unified macroscopic peak.
- **Continuous 150m Hard Crest Focus:** The unified peak designates its single highest-priced continuous $\le 150$ min window as `FORCED_OFF` (Red), while flanking shoulder slots are safely designated as `ADVISED_OFF` (Orange).
- **Anti-Cycling Dwell Time Guard:** Strictly enforces $\ge 120$ minutes of continuous recovery spacing between any two hard lockouts, eliminating erratic "uit $\to$ aan $\to$ uit" switching cycles on the Daikin heat pump compressor.
- **Unit Test:** Added `test_dynamic_peaks_macro_clustering_and_anti_cycling`.

---

### Fixed
- **Live Room & Target Sensor Wiring:** Mapped live room temperature and target setpoint in `daemon.py` directly from `climate.woonkamer_climate_daikin` and `sensor.hc_sensors_temperature_room`, replacing nonexistent `sensor.woonkamer_temperatuur`.
- **Dynamic 2R1C Free-Drift in Summer Mode:** Replaced static dummy 20°C flatline in `SpaceHeatingPolicy` with genuine 2R1C thermal forward simulation. The building's room and floor temperatures now drift dynamically based on passive window solar gains and outdoor thermal envelope loss even when CV compressor is 0 kW.
- **Dynamic Carnot COP Curve:** Expanded Carnot upper clamp to 6.8 and enabled dynamic outdoor-temperature-dependent COP calculation across both history and forecast horizons.

---

### Added
- Implemented saturation lockout (`DHW_BUFFER_60_MAX_TANK_TEMP_C = 53.0°C`, `DHW_BUFFER_60_MIN_HEADROOM_C = 7.0°C`) in `layer3_scheduling/dhw_daytime_arbiter.py`: strictly suppresses 60°C buffering when tank is already >= 53.0°C, preventing unprofitable micro-runs for small temperature lifts.
- Full decision audit logging: logs counterfactual reason and clear explanation to `decision_log` and UI cards when buffer runs are suppressed due to saturation.
- Unit test `test_daytime_arbitrage_saturation_lockout_at_59c` to continuously guard against regression.

---

### Fixed
- Replaced wide 5-hour continuous DHW dispatch window in validation overlay with realistic finite-pulse scheduling (45m night top-up @ 1.8kW, 60-75m solar boost @ 2.4kW), reducing predicted DHW energy from an inflated 15.6 kWh down to ~4.5 kWh (aligning with actual ~4.68 kWh).

---

### Changed
- Relocated 'Model Validatie: Voorspelling vs. Werkelijkheid' card from Historie to the Zelflerend Model (`#calibration`) tab directly beneath the 4 model KPI cards.
- Wired `loadValidationOverlayChart()` to trigger automatically upon navigating to the Zelflerend Model tab.

---

### Added
- Dual-line DHW validation overlay in `daemon.py`: displays planned heat pump dispatch runs (Option A, primary dashed line) alongside physical household tapping demand (Option B, translucent soft amber curve).

### Fixed
- Replaced naive L1 point-by-point quality metric with IEA PVPS composite accuracy (50% Volumetric Energy Accuracy + 50% NMAE normalized to rated peak capacity), eliminating the transient cloud double-penalty on solar forecasting.

---

### Added
- 5 seasonal archetype fixtures in `tests/fixtures/golden/`:
  1. `golden_summer_solar_heavy.json`: 35kWh PV, negative midday prices, summer lockout CV.
  2. `golden_winter_sunny_peak.json`: Cold sunny winter day, floor buffer charging, hard evening peak coasting.
  3. `golden_winter_dunkelflaute.json`: Freezing overcast day, low continuous modulation, night valley top-up.
  4. `golden_winter_defrost_humid.json`: Near-freezing high humidity, automated COP frosting penalty.
  5. `golden_shoulder_season.json`: Spring/autumn transition crossing 16°C threshold with stable modulation.
- Expanded `tests/unit/test_replay_harness.py` to benchmark all seasonal archetypes continuously in CI.

---

### Added
- 2R1C underfloor buffer optimization in `layer3_scheduling/space_heating_policy.py`: evaluates thermal energy cost (€/kWh_th = Price / COP), schedules strategic pre-heat runways before dynamic peak lockouts, and allows daytime solar overshoot (up to setpoint + 1.2°C) so the concrete screed remains saturated to coast through evening peaks.
- Canonically typed `SpaceHeatingSlotResult` and `SpaceHeatingPlanSummary` in `models/canonical.py`, attached directly to `CanonicalDispatchPlan.heating_summary`.
- `CleanTelemetryFrame` support for `target_room_temp`, `current_room_temp`, and `current_floor_temp` in Layer 1.

### Fixed
- Sanitized `/api/model/heating-forecast` in `daemon.py` into a 100% Dumb View reading purely from `PlanStore.get_plan().heating_summary`, eliminating direct HA queries and ad-hoc simulation logic in the view handler.

---

### Fixed
- Enforced `abs(sol_raw)` for solar inverter readings in `HemsBackgroundCollector` live power balance, correctly calculating direct solar self-consumption and unallocated load during daytime export.

---

## [0.32.2] — 2026-09-08 (Fix Physical Self-Consumption Overcounting & Synchronize Exact Spot Pricing in Tooltips)

### Fixed
- **Physical Solar Self-Consumption Overcounting:**
  - Corrected interval energy balance formula from `self_cons = min(solar, verbruik)` to `self_cons = max(0.0, solar - terug)`.
  - Guarantees `Afname + Opgewekt Gebruikt == Totaal Verbruik` strictly across all hours, resolving discrepancy where both Afname (262 W) and Opgewekt Gebruikt (896 W) were double-counted against total load (896 W).
- **Exact Interval Spot Pricing in Hover Tooltips:**
  - Fixed fallback bug where tooltip fell back to `€0.280/kWh` instead of reading the exact live price curve (`€0.2155/kWh`).
  - Extracted exact interval price directly from the chart's `Stroomprijs All-in` dataset.
  - Now accurately calculates costs: e.g. `896 W (0.90 kWh) * €0.2155/kWh = €0.19` (not €0.25).
- **Power (W/kW) + Energy (kWh) Dual Display:**
  - Added explicit energy in kWh alongside power in Watts in every tooltip row: e.g. `896 W (0.90 kWh) · €0.19`.
  - Added interval duration badge in tooltip header (`1 uur` vs `15 min`).

---

## [0.32.0] — 2026-09-08 (Perfect Center-Aligned Horizontal 0-Axis for Watt & Tariffs, and Active Custom Tooltips Across Charts)

### Added
- **Exact Center-Aligned Horizontal 0-Axis (`min = -max`):**
  - Configured symmetric dynamic range on both the primary left axis (`y` in Watts) and secondary right axis (`y1` in €/kWh).
  - Guarantees that the horizontal 0 Watt line and the horizontal 0 €/kWh line are positioned on the exact same vertical center pixel height (50.0% of canvas height) across all screen widths.
- **Active Custom HTML Tooltip Delivery:**
  - Fully wired `customHemsTooltipHandler` into both `powerProducersChart` (Verbruikshistorie) and `hemsChartAnalytics` (Verbruiksvoorspelling) via `options.plugins.tooltip.external`.
  - Added ultra-high z-index (`z-[9999]`) and smooth mobile touch dismissal.
  - Verified actual line indicators for curves (`Totaal Verbruik`, `Netto Grid Stroom`, `Stroomprijs All-in`) and rounded bar pills for stacked loads.
  - Formatted interval euro amounts (`Afname: +€...`, `Teruglevering: -€...`, `Opgewekt Gebruikt: €... besp.`, `Netto: €...`).

---

## [0.31.0] — 2026-09-08 (EPEX Price Overlays in Historical Chart & Modern Styled Tooltips with Actual Lines and Differentiated Dynamic Costs)

### Added
- **EPEX All-in Stroomprijs Curve in Verbruikshistorie:**
  - Added cyan dashed curve (`#06B6D4`, `borderDash: [4, 4]`) to the historical `Verbruikshistorie` chart plotted on a dedicated right Y-axis (`Tarief €/kWh`), matching the predictive forecast visualization.
- **Contractually Differentiated Dynamic Tariffs:**
  - **Opgewekt Gebruikt (Self-consumption):** Valued against full All-in EPEX import price (avoiding costly grid consumption ~€0.28/kWh).
  - **Teruglevering (Grid export):** Valued against dynamic export price (EPEX spot base minus €0.00605/kWh Powerpeers verkoopvergoeding, without energy taxes/VAT).
- **Custom Modern HTML Hover Tooltip:**
  - Replaced standard generic canvas tooltip with a custom styled HTML tooltip component:
    - **Visual Line Indicators:** Datasets that are lines (`Totaal Verbruik`, `Netto Grid Stroom`, `Stroomprijs`) display true colored lines (and dashed for tariff) instead of generic square dots.
    - **Visual Bar Indicators:** Stacked bar components display rounded pills.
    - **Monetary Costs per Type:** Shows interval euro costs/revenues next to power:
      - `Afname: +€...` (in red)
      - `Teruglevering: -€...` (in emerald)
      - `Opgewekt Gebruikt: €... bespaard` (in cyan)
      - `Totaal Verbruik: €... bruto` (in orange)
    - **Netto Interval Balance:** Clear footer calculating exact net costs or revenues (`Netto Kosten: €...` or `Netto Opbrengst: +€...`).

---

## [0.30.2] — 2026-09-08 (Deterministic Canonical Unit Normalization & Hardware Contract Enforcement)

### Added
- **Canonical Unit Normalization (`models/canonical.py`):**
  - Implemented `normalize_power_reading()` to deterministically convert raw power readings to Watts without arbitrary numerical thresholds.
  - Contract precedence:
    1. Device configuration contract: `device_cfg['native_unit'] == 'kW'` strictly converts by multiplying by 1000.0, preserving low-power balancing (e.g. 0.010 kW -> 10 W).
    2. Entity metadata inspection: converts `unit_of_measurement == 'kW'` to Watts.
    3. Native Watt sources (Modbus Inepro meters) pass through unaltered.
- **Hardware Device Contracts in `heatpump_config.json`:**
  - Added explicit `native_unit` and `storage_unit` fields across all device profiles (`main_grid_meter`: kW, `rooftop_solar`: W, `daikin_heat_pump`: W).
- **Comprehensive Unit Testing:**
  - Added test suite in `tests/unit/test_canonical_models.py` verifying small-power balancing (10W), large loads (1.5kW), Modbus passthrough, device contracts, and non-numeric inputs (35/35 tests passing).

---

## [0.30.1] — 2026-09-08 (Fix Collector InfluxDB Flush Loop Crash & Backfill Missing Historical Window)

### Fixed
- **Collector Line Protocol Flush Crash:**
  - Resolved `NameError: name 'mode_tag' is not defined` in `HemsBackgroundCollector.flush_window_to_influx()` when formatting line protocol strings for `energy_telemetry`.
  - Added safety check `mode_tag = f",mode={parts[6]}" if len(parts) > 6 else ""` before string formatting.
- **Historical Data Backfill:**
  - Ingested and synchronized 1,072 missing 1-minute data points between 16:30 CEST and 21:00 CEST from canonical Home Assistant recorder history into InfluxDB `openhems`.
  - Restored continuous live updates on `Verbruikshistorie` chart up to current wall-clock time (21:00 CEST).

---

## [0.30.0] — 2026-09-08 (Lean & Mean Cleaned Dashboard Architecture with 2 Dedicated Categories)

### Added
- **Clean Two-Category Dashboard Architecture (Voorspelling vs Historie):**
  - Restructured the primary dashboard into two clearly delineated, dedicated operational sections:
    1. **🔮 VOORSPELLING (FORECAST):**
       - **Verbruiksvoorspelling:** 24-hour rolling predictive scheduling stacked bar chart (`hemsChartAnalytics`), clean optimizer banner, domestic solar recommendation banner, aligned 6-box metrics with projected costs (€), and dual-polarity legend chips.
       - **Prijzen & Zonnevoorspelling:** Dual-axis EPEX electricity spot prices and solar irradiance curves (`electricityPricesChart`) with 15m/1h resolution selector and 4-metric rate cards.
       - **Category Filters:** Dedicated 1h/15m resolution toggle and refresh button in the category bar.
    2. **📊 HISTORIE (HISTORICAL DATA):**
       - **Verbruikshistorie:** Canonical historical energy telemetry (`powerProducersChart`) with dual-polarity stacked bars or smooth lines, and aligned 6-box metrics with actual accumulated costs (€).
       - **Category Filters:** Inline diagram type toggle (📊 Staven vs 📈 Lijn), interval resolution toggle (1 Uur vs 15 Min), and timeframe period dropdown (1h, 6h, 24h, 48h, 7d).
       - **Prestatie & Rapportage:** Integrated automated digest and seasonal COP breakdown.
- **Top 4 KPI Metrics Preserved:**
  - `Besparing Vandaag`, `Zelfconsumptie`, `Warmtepomp COP`, and `Prognose Validatie` cleanly pinned at the very top of the page.
- **Lean & Mean Decluttering:**
  - Removed redundant filler text, verbose subtitles, and useless labels (such as "openhems" badge on chart).
  - Shortened all graph titles to direct, punchy Dutch descriptors (`Verbruiksvoorspelling`, `Prijzen & Zonnevoorspelling`, `Verbruikshistorie`).

---

## [0.29.0] — 2026-09-08 (Interactive Bar/Line Toggle & Grafana-Style Smart Interval Resolution for Power Producers)

### Added
- **Interactive Diagram Type Toggle (📊 Staven vs 📈 Lijn):**
  - Added toggle on the historical "Power Producers & Netstromen" chart allowing users to instantly switch between:
    - **📊 Staven (Bar chart):** Aligned with the 24-Hour Ahead Prediction chart, featuring dual-polarity stacked bars on `stack: 'energy'` with positive consumers (Afname + Opgewekt Gebruikt) above the axis, negative producers (Teruglevering + Direct Benut) below the axis, and overlaid lines for Total Consumption & Net Grid flow.
    - **📈 Lijn (Line chart):** Continuous multi-layer filled area graph.
- **Grafana-Style Smart Interval Resolution Toggle (1 Uur vs 15 Min vs Auto):**
  - Implemented dynamic interval resolution buttons (`1 Uur` & `15 Min`) on the chart card.
  - Smart default auto-tuning:
    - For `24h` range: Defaults strictly to **1 Uur** (`1h`) hourly aggregation.
    - For short ranges (`1h`, `6h`): Defaults automatically to **15 Min** (`15m`) quarter-hourly aggregation.
    - For multi-day ranges (`48h`, `7d`): Defaults to **1 Uur** or **2 Uur** for balanced density without visual clutter.
  - Full backend query support in `/api/analytics/power_producers?range=...&resolution=...`.

---

## [0.28.0] — 2026-09-08 (Site Adapters Architecture & Daikin P1P2 State Disaggregation)

### Added
- **Site-Specific Adapters Architecture (`site_adapters/`):**
  - Created cleanly isolated sub-package `site_adapters/daikin_p1p2/` to strictly decouple hardware- and site-specific classifiers from Open HEMS Core.
  - Implemented `DaikinP1P2StateClassifier` & `HeatPumpDisaggregation` dataclass.
  - Multi-source state decision tree evaluating:
    - Primary: Raw Daikin P1P2 MQTT topics (`Action_Heating_Cooling_Auto_Off`, `DHW_Demand`, `Valve_DHW_Tank`, `Climate_Heating`, `Climate_Cooling`).
    - Secondary: Home Assistant fallback entities (`select.daily_energy_usage_sums_wp`, `binary_sensor.hc_*`).
    - Power safety: Standby threshold (< 48 W) ensures idle compressor draw (~33 W) is strictly booked as STANDBY.
  - Disaggregates total heat pump electrical power into pure physical buckets: `dhw_w`, `heating_w`, `cooling_w`, and `standby_w`.
  - Enriched InfluxDB `energy_telemetry` with tag `mode={mode_tag}`.
  - Visualized live heat pump mode badge on Systeeminfrastructuur telemetry bar (e.g. `Warmtepomp: 33 W [STANDBY]`).
  - Added unit test suite in `tests/unit/test_site_adapters.py` (7 tests, 100% passing; total 34 tests passing).

---

## [0.27.0] — 2026-09-08 (Unified 6-Box Metrics with Integrated Monetary Costs & Central Theme)

### Added
- **Unified 6-Box Metrics Grid for Both Historical & 24h Ahead Prediction:**
  - Standardized metrics layout (2 rows x 3 columns) across both the historical Power Producers chart and the 24-Hour Ahead Prediction chart.
  - Box 1: Zonnepanelen (kWh, Last/Avg kW, Min/Piek kW, and Monetary Value €).
  - Box 2: Teruglevering (kWh, Last/Avg kW, Min/Piek kW, and Monetary Revenue €).
  - Box 3: Afname (kWh, Last/Avg kW, Max/Piek kW, and Monetary Grid Cost €).
  - Box 4: Totaal opgewekt (kWh, Last/Avg kW, Min/Piek kW, and Total Generation Value €).
  - Box 5: Opgewekt Gebruikt / Direct Self-Consumption (kWh, Last/Avg kW, Min/Piek kW, and Net Avoidance Savings €).
  - Box 6: Totaal Verbruik (kWh, Last/Avg kW, Max/Piek kW, and Gross Energy Cost €).
- **Integrated EPEX Spot / EnergyZero Monetary Pricing:**
  - Realtime and predictive price multiplication (`kWh * EPEX spot price €/kWh`) for every interval (hourly and quarter-hourly PT15M).
  - Clean display of cost badges (e.g. `17.9 kWh · €3.42` / `€0.18 opbr.` / `€0.89 besp.`).
- **Canonical Central Color Theme (`data/theme_colors.json`):**
  - Created central single source of truth for all device types, energy flow polarities, metric boxes, and chart overlays (`theme_colors.json`).
  - Colors matching the canonical dashboard design:
    - Zonnepanelen: `#EAB308` (Yellow)
    - Teruglevering: `#10B981` (Emerald)
    - Afname: `#EF4444` (Red)
    - Totaal opgewekt: `#84CC16` (Lime)
    - Opgewekt Gebruikt: `#06B6D4` (Cyan)
    - Totaal Verbruik: `#F97316` (Orange)
    - Netto lijn: `#EF4444` (Felrood)
    - Prijslijn: `#06B6D4` (Cyan gestreept)

---

## [0.26.0] — 2026-09-08 (Direct Inepro Modbus MQTT Streaming Adapters for Heat Pump & Solar)

### Added
- **Direct MQTT Streaming Adapters for Inepro Modbus Meters:**
  - Added background MQTT subscriber thread (`HemsMqttSubscriberThread`) in Open HEMS Layer 1 collector connecting directly to Mosquitto with user `openhems`.
  - Subscribes to live Modbus (MBMD) meter topics:
    - **Solar Panels:** `mbmd/inepro1-103/Power` (subtopics `L1`, `L2`, `L3`) — reads direct generation in Watt.
    - **Daikin Heat Pump:** `mbmd/inepro1-102/Power` (subtopics `L1`, `L2`, `L3`) — reads direct consumption in Watt.
  - Zero-latency in-memory cache feeding the 60-second tumbling window accumulator and realtime power balance pipeline, with automatic fallback to Home Assistant entities.
  - Updated device registry in `heatpump_config.json` with `source_type: "mqtt"`, `mqtt_power_topic`, and 3-phase parameter metadata.

---

## [0.25.0] — 2026-09-08 (Self-Learning Unallocated Model in Calibration & Live Data Pipeline Monitor)

### Added
- **Self-Learning Unallocated Model Dashboard in Calibration Tab (`view-calibration`):**
  - Added dedicated card displaying the 7×24 hourly unallocated profile matrix.
  - Interactive Day-of-Week selector (`Ma`, `Di (Wasdag)`, `Wo`, `Do`, `Vr`, `Za`, `Zo`).
  - Realtime visual 24-hour load heatmap bar chart with hover tooltips showing exact wattage per hour.
  - Key metrics display: Day Average, Night Standby (295–320 W), Morning Peak, Evening Peak.
  - Added "Herbereken Model" button to re-evaluate the 180-day canonical dataset on demand.
- **60s Tumbling Window Data Pipeline & Power Balance Monitor in Infrastructure Tab (`view-infrastructure`):**
  - Visual 3-stage animated data flow diagram:
    1. Ingestion (10s): P1 Meter (ESPHome 6053), Solar Inverter, Daikin WP, Home Battery & MQTT.
    2. Accumulator (60s Window): In-memory tumbling window averaging with live progress bar (`0% - 100%`) and sample counter (`X/6 samples`).
    3. Datastore (`openhems`): Synchronized time-series write to dedicated InfluxDB with total points counter.
  - Live Mathematical Power Balance telemetry strip:
    - P1 Netto ($P_{\text{imp}} - P_{\text{exp}}$)
    - Zon Productie ($P_{\text{sol}}$)
    - Direct Zonne-Eigenverbruik ($\max(0, P_{\text{sol}} - P_{\text{exp}})$)
    - Warmtepomp ($P_{\text{wp}}$)
    - Totaal Werkelijk Huisverbruik ($P_{\text{net}} + P_{\text{sol}}$)
    - Ongedefinieerd Verbruik ($P_{\text{totaal}} - P_{\text{wp}}$)
- **REST Endpoints:**
  - Added `/api/pipeline/status` for continuous 10s polling of the in-memory window accumulator.
  - Added `/api/calibration/unallocated-model` for inspection and synchronization of the 7x24 profile.

---

## [0.24.0] — 2026-09-08 (Same-Column Dual-Polarity Alignment & 15-Minute Prediction Toggle)

### Added & Enhanced
- **Same-Column Dual-Polarity Alignment:**
  - Unified all stacked bar datasets under a single `stack: 'energy'` identifier.
  - Positive consumer bars (Ongedefinieerd, SWW, CV, Accu Laden) stack directly upward above $Y=0$, and negative production bars (Zon, Accu Ontladen) stack directly downward below $Y=0$ in the **exact same vertical column**, eliminating side-by-side splitting.
- **15-Minute Resolution Forecast Toggle:**
  - Added interactive `[ 1 Uur ] [ 15 Min ]` segmented control buttons to both the Dashboard and Analytics card headers.
  - Integrated 15-minute EPEX market tariffs from EnergyZero (`INTERVAL_QUARTER`) across 96 quarter-hour slots (24 hours ahead) with $0.25\text{h}$ trapezoidal energy integral.

---

## [0.23.0] — 2026-09-08 (Fix Mobile Legend Overflow on Dashboard Tab)

### Fixed & Enhanced
- **Mobile Legend Overflow on Dashboard Tab (`view-dashboard`):**
  - Resolved root cause shown in user screenshot where the legend on Tab 1 was placed on the same line as the title, squeezing and overflowing off the right screen edge on mobile.
  - Moved the legend **below the canvas** into a dedicated wrapping container (`flex-wrap gap-2.5`) with updated labels (`Ongedefinieerd (+kW)`, `SWW (+kW)`, `CV (+kW)`, `Accu Laden (+kW)`, `Zon (-kW)`, `Accu Ontladen (-kW)`, `Netto Lijn`, `Prijs (€/kWh)`).
  - Synchronized metrics badges (`⚡ Verbruik`, `💶 Netto`, `☀️ Overschot`) across both Dashboard and Analytics tabs.

---

## [0.22.1] — 2026-09-08 (Calibrate 7x24 Load Profile directly from Canonical HA Energy Database)

### Added & Calibrated
- **Canonical HA Energy Statistics Calibration:**
  - Diagnosed flat daytime profile. Re-calculated 180 days of hourly consumption from the canonical Home Assistant SQLite Energy database (`sensor.daily_energy_consumption`, `sensor.daily_energy_returned`, `sensor.zonnepanelen_export_power`, `sensor.daily_energy_usage_sum_wp`).
  - Successfully captures real household daytime patterns:
    - **Nacht (00:00–06:00):** Strak ~295–320 W (25th percentile standby).
    - **Ochtend (07:00–09:00):** 500–750 W (thee, koffie, ontbijt).
    - **Overdag (10:00–17:00):** 450–850 W (gezin, apparaten, actieve uren).
    - **Avond & Wasdag:** Tot 950–992 W piek op dinsdagavond.

---

## [0.22.0] — 2026-09-08 (Enforce Summer Lockout for CV Space Heating & Compact Mobile Badges)

### Fixed
- **Summer Lockout for CV Space Heating:**
  - Resolved root cause of nighttime load showing ~550 W instead of ~300 W. The predictive engine had a legacy threshold planning 240 W of space heating (`CV (+kW)`) whenever outdoor temperature dropped under 15.5°C at night, despite being in early September.
  - Enforced strict summer lockout rules from `heatpump_config.json`: when 24h mean outdoor temperature $\ge 15^\circ\text{C}$, maximum temperature $\ge 18^\circ\text{C}$, or month is May–September, space heating is strictly locked at 0.0 kW.
  - Night load (01:00–06:00) now cleanly sits at pure unallocated baseline of **320 W** with 0 CV load and the red net line tracking exactly along the 320 W bar.
- **Compact Mobile Prediction Badges:**
  - Streamlined badge padding and labels (`Ongedefinieerd: 7x24`, `⚡ Verbruik: XX.X kWh`, `💶 Netto: €X.XX`, `☀️ Overschot: X.X kWh`) to wrap cleanly on mobile screens without truncation.

---

## [0.21.0] — 2026-09-08 (Calibrated ~300W Night Standby & 24h Total Predicted Energy / Cost Engine)

### Added
- **24-Hour Total Predicted Energy & Net Cost Engine:**
  - Computes total predicted 24-hour gross electrical consumption ($E_{\text{verbruik}} = \sum P_{\text{load}} \cdot \Delta t$) in kWh.
  - Computes total expected net energy cost ($\sum P_{\text{net}} \cdot \text{tarief}$) in EUR based on live hourly EPEX day-ahead prices and scheduled self-consumption/arbitrage.
  - Calculates gross load value and solar self-consumption savings.
- **Top Metrics Badges:**
  - Added `⚡ Verbruik: XX.X kWh` (indigo) and `💶 Netto Kosten: €X.XX` (emerald) to the prediction header alongside the unallocated profile and solar surplus badges.
- **Calibrated Night Baseline Standby:**
  - Anchored nighttime unallocated consumption (00:00–06:00) around ~300 W (295–315 W) by filtering out intermittent winter EV charging / defrost outliers using robust percentile statistics.

---

## [0.20.2] — 2026-09-08 (Resolve Path to Learned Profile via `__file__`)

### Fixed
- Added `Path(__file__).parent / "data" / "unallocated_load_profile.json"` and `/config/unallocated_load_profile.json` so the learned 7x24 model loads directly in container environments.

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
