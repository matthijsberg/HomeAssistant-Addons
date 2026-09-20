# Open HEMS — AI Agent & Developer Playbook

Welcome to Open HEMS. This document is the authoritative onboarding and development guide for AI agents and human contributors. It defines the core invariants, test verification commands, and canonical recipes for extending the codebase without introducing architectural drift.

---

## 🏛️ The 5 Invariant Rules (Checklist Before Every Commit)

Before presenting any code changes or committing:
- [ ] **1. Single Source of Truth & Dumb Views:** Never add math, aggregation, or state-color calculations inside UI views or HTTP GET handlers in `daemon.py`. All presentation consumers read exclusively from `PlanStore.get_plan()`. Styling, translations, and HEX colors belong in `config/mode_catalog.json`.
- [ ] **2. Core Entity Isolation:** Core packages (`layer1_data_collection/sanitizer.py`, `layer2_calibration/`, `layer3_scheduling/`, `models/canonical.py`) must contain **zero** Home Assistant entity strings (e.g. `sensor.`, `climate.`, `switch.`) and zero brand names. Hardware entities and device-specific interlocks belong strictly in `integrations/<device_or_protocol>/` or `config/site_config.json`.
- [ ] **3. Absolute Ban on Mock Data in Production:** Never invent synthetic values or mock responses in production runtime. Mocks are strictly confined to `tests/`. Production telemetry missing data must be handled via `Quality.STALE` or `Quality.INTERPOLATED` flags. Offline development and CI verification rely strictly on the **Replay Harness with Golden Plan Snapshots** (`tests/fixtures/golden/`).
- [ ] **4. Test Suite Green:** All tests (including architecture guardrails and golden replay tests) must pass before pushing (`pytest tests/`).
- [ ] **5. API & MCP Lockstep Parity:** Any change, addition, or retirement of a REST API endpoint MUST be declared in `docs/openapi.json` and simultaneously exposed in `mcp_server.py`. Architectural guardrail `tests/architecture/test_api_mcp_lockstep.py` enforces 100% parity.
- [ ] **6. GUI & Frontend Verification via Headless Browser:** When modifying the WebUI, HTML, CSS, JavaScript, or dashboard charts, the QA agent must use the headless browser (via `browser_exec` with `capture_screenshot()` and DOM/console error inspection) to perform visual and live data verification before marking work complete.
- [ ] **7. Historical Telemetry Fidelity (Feiten vs. Redeneren):** Alle data voor historische grafieken (actuaties, staten, vermogens, spitsblokken) moet direct worden opgehaald uit de persistente opslag (InfluxDB telemetrie en annotaties), NOOIT ter plekke synthetisch worden geredeneerd of achteraf berekend met voorspellende modellen (zoals `detect_dynamic_price_peaks` over een willekeurig zoomvenster). Redeneren en modelleren over historische data is uitsluitend toegestaan voor afgeleide data (zoals thermische vermogensafleidingen, trendanalyses, kalibraties en samenvattingen), mits voorzien van fysische ruisbewaking.

---

## 🧪 Verification & Test Commands

Run the full automated test suite from the repository root:

```bash
# Run all tests (unit, golden replay, and architectural guardrails)
PYTHONPATH=. pytest tests/

# Run golden replay regression test
PYTHONPATH=. pytest tests/unit/test_replay_harness.py

# Run anti-drift and entity isolation guardrails
PYTHONPATH=. pytest tests/architecture/test_anti_drift_guards.py

# Bytecode syntax check
python3 -m py_compile daemon.py
```

---

## 📐 Architectural Standards & Naming Conventions

* **Exact Codename Parity:** Always maintain 1:1 parity between PRD terms and code classes:
  * `TelemetrySanitizer` (in `layer1_data_collection/sanitizer.py`)
  * `CleanTelemetryFrame` (in `layer1_data_collection/sanitizer.py`)
  * `CentralPlanner` (in `layer3_scheduling/central_planner.py`)
  * `PlanStore` (in `layer3_scheduling/plan_store.py`)
  * `CanonicalDispatchPlan` (in `models/canonical.py`)
  * `TariffProvider` (in `layer3_scheduling/tariff_provider.py`)
* **Injected PlanStore:** Pass `PlanStore` instances into classes and handlers as a parameter/dependency rather than forcing a global singleton. Use `PlanStore.get_instance()` only as a convenience default.
* **Effective Mode Reporting:** Actuators in Layer 4 must always report `effective_mode`, `realized_power_kw`, and optional `downgrade_reason` back into telemetry. The calibration layer trains strictly on effective modes.
* **Unified Integrations:** A device's telemetry reader and actuator live together in `integrations/<protocol_or_device>/`. Hardware interlocks (e.g. CV master OFF during DHW run) are enforced inside the integration package, not in the general planner.

---

## 🛠️ Canonical Recipes

### Recipe 1: Een Nieuw Device Toevoegen
Devices worden co-located ondergebracht in `integrations/<device_name>/` en geconfigureerd in `config/site_config.json` op basis van hun *capabilities* (`can_delay`, `can_modulate`, `can_store`, `can_export`, `has_deadline`, `is_thermal`).

* **Voorbeeld om te kopiëren:** `integrations/daikin_altherma/` (bevat `reader.py`, `actuator.py`, `interlocks.py`).

1. **Maak het integratiepakket aan:**
   ```
   integrations/<device_slug>/
   ├── __init__.py
   ├── reader.py        # Telemetry ingestion (temperatures, power, states)
   ├── actuator.py      # Control commands & effective_mode reporting
   └── interlocks.py    # Device-level invariants (e.g. dwell times, safety interlocks)
   ```
2. **Definieer het device in `config/site_config.json`:**
   ```json
   {
     "id": "thuisbatterij_10kwh",
     "name": "Deye 10kWh LFP Thuisaccu",
     "adapter": "deye_modbus_tcp",
     "capabilities": ["can_store", "can_export", "can_modulate"],
     "parameters": {
       "capacity_kwh": 10.0,
       "max_charge_kw": 5.0,
       "max_discharge_kw": 5.0,
       "roundtrip_efficiency": 0.88
     }
   }
   ```
3. **Schrijf een device-invariants test:** Voeg een unit-test toe die garandeert dat hardware-beveiligingen (bijv. maximale laadstroom of hydraulische interlocks) lokaal worden geborgd.

---

### Recipe 2: Een Nieuwe Optimalisatie-Policy Toevoegen
Policies bepalen stook-, laad- of uitschakelbeslissingen op basis van tarieven, zonnestraling en comfortgrenzen.

* **Voorbeeld om te kopiëren:** `layer3_scheduling/central_planner.py` (zie de `detect_dynamic_price_peaks` methode voor dynamische spitsblokkades).

1. Definieer de policy-parameters declaratief in `config/site_config.json`.
2. Implementeer de evaluatiemethode conform de signature:
   `plan(frame: CleanTelemetryFrame, tariffs: TariffProvider, devices: List[DeviceConfig], constraints: Dict) -> CanonicalDispatchPlan`.
3. Garandeer dat de output uitsluitend canonieke statuscodes gebruikt (`StandardizedState`).
4. Valideer de policy tegen het replay-harnas (`pytest tests/unit/test_replay_harness.py`).

---

### Recipe 3: Een Nieuwe Data Provider of Tariefstructuur Toevoegen
Data providers voeden ruwe data aan Laag 1 (`layer1_data_collection/collector.py`).

* **Voorbeeld om te kopiëren:** `layer1_data_collection/collector.py` (zie `P1Collector` of `OpenMeteoCollector`).

1. Implementeer de provider-methode in `layer1_data_collection/`.
2. Zorg dat de data via `TelemetrySanitizer` passeert voor:
   - Freshness check (< 15 min TTL).
   - 15-minuten grid alignment op `Nu`.
   - Fysische grensbewaking.
3. Vang netwerkfouten af en markeer ontbrekende datapunten expliciet met `Quality.STALE` of `Quality.INTERPOLATED`.
