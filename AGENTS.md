# Open HEMS — AI Agent & Developer Playbook

Welcome to Open HEMS. This document is the authoritative onboarding guide for AI agents and human contributors. It defines the core invariants, test commands, and canonical recipes for extending the codebase without introducing architectural drift.

---

## 🏛️ The 4 Invariant Rules (Checklist Before Every Commit)

Before presenting any code changes or committing:
- [ ] **1. Single Source of Truth:** Never add math, aggregation, or state-color calculations inside UI views or HTTP GET handlers in `daemon.py`. All presentation consumers read exclusively from `PlanStore.get_plan()`.
- [ ] **2. Core Entity Isolation:** Core packages (`layer1_data_collection/sanitizer.py`, `layer2_calibration/`, `layer3_scheduling/`, `models/canonical.py`) must contain **zero** Home Assistant entity strings (e.g. `sensor.`, `climate.`, `switch.`) and zero brand names. Hardware entities belong strictly in `integrations/` or `config/site_config.json`.
- [ ] **3. Absolute Ban on Mock Data in Production:** Never invent synthetic values or mock responses in production runtime. Mocks are strictly confined to `tests/`. Missing data must be handled via `Quality.STALE` or `Quality.INTERPOLATED` flags.
- [ ] **4. Test Suite Green:** All tests (including architecture guardrails and golden replay tests) must pass before pushing (`pytest tests/`).

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

## 🛠️ Canonical Recipes

### Recipe 1: Een Nieuw Device Toevoegen
Devices worden declaratief gedefinieerd in `config/site_config.json` op basis van hun *capabilities* (`can_delay`, `can_modulate`, `can_store`, `can_export`, `has_deadline`).

1. **Definieer in config:** Voeg het device toe aan `devices` in `config/site_config.json`:
   ```json
   {
     "id": "thuisbatterij_10kwh",
     "name": "Deye 10kWh LFP Thuisaccu",
     "archetype": "battery_storage",
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
2. **Koppel in Integratie:** Maak een adapter in `integrations/<device_name>/` (zie voorbeeld: `site_adapters/daikin_p1p2/classifier.py`).
3. **Voeg dispatch mapping toe:** Zorg dat de planner in `layer3_scheduling/central_planner.py` het device herkent via zijn capability en een `DeviceSlotDispatch` toewijst.

---

### Recipe 2: Een Nieuwe Optimalisatie-Policy Toevoegen
Policies regelen stook-, laad- of uitschakelbeslissingen op basis van tarieven, zonnestraling en comfortgrenzen.

* **Voorbeeld om te kopiëren:** `layer3_scheduling/central_planner.py` (zie de `detect_dynamic_price_peaks` methode voor piekbeleid).
1. Definieer de policy-parameters in `config/site_config.json`.
2. Implementeer de evaluatiemethode in een policy-klasse (of breid `CentralPlanner` uit).
3. Garandeer dat de output uitsluitend gebruikmaakt van het canonieke enum (`StandardizedState`).
4. Voeg een unit-test toe aan `tests/unit/test_power_slotter.py` of `tests/unit/test_canonical_models.py`.

---

### Recipe 3: Een Nieuwe Data Provider Toevoegen
Data providers voeden ruwe data aan Laag 1 (`layer1_data_collection/collector.py`).

* **Voorbeeld om te kopiëren:** `layer1_data_collection/collector.py` (zie `P1Collector` of `OpenMeteoCollector`).
1. Implementeer de provider-methode in `layer1_data_collection/`.
2. Zorg dat de data via `layer1_data_collection/sanitizer.py` (`TelemetrySanitizer`) passeert voor:
   - Freshness check (< 15 min TTL).
   - 15-minuten grid alignment op `Nu`.
   - Fysische grensbewaking.
3. Vang netwerkfouten af en markeer ontbrekende punten expliciet met `Quality.STALE` of `Quality.INTERPOLATED`.
