# Open HEMS — Master Implementatieplan: Integrale Device Config, Subsystemen & Reheat Sturing

**Document-ID:** `OPENHEMS-PLAN-2026-09-27-01`  
**Auteurs:** Open HEMS Multi-Agent Team (Lead Orchestrator, @energie-expert, @wiskundige, @data-scientist, @software-architect, @qa-agent)  
**Status:** In Review / Klaar voor gefaseerde uitvoering  
**Doel:** Het herzien van apparaatconfiguratie, data en CRUD in Open HEMS, inclusief een gecombineerde Hoofdkaart met 2 Subkaarten voor de warmtepomp en het tapwatervat, en volledige doorwerking naar de planner en statistieken.

---

## 1. Executive Summary & Architecturale Doelstelling

In Open HEMS staan de warmtepomp (Daikin Altherma 3 H HT) en het tapwatervat (OEG 350L SWW) momenteel als twee losse apparaten in de apparatenlijst (de een onder Direct MQTT via Inepro 102, de ander onder Home Assistant Core). 

Fysisch en hydraulisch is het tapwatervat echter een **subsysteem van de warmtepomp**:
* Er is slechts **één gemeenschappelijke compressor** en **één kWh-meter** (Inepro 102).
* Een **hydraulische driewegklep** schakelt tussen CV (vloerverwarming) en SWW (tapwater). Ze kunnen fysiek nooit gelijktijdig draaien zonder dat het dure 9 kW back-up verwarmingselement (BUH) inschakelt.
* De apparaten moeten daarom **fysisch gekoppeld** zijn (Parent-Child hiërarchie met interlocks), maar **functioneel en parametrisch apart configureerbaar** blijven.
* Het toevoegen, bewerken en verwijderen van apparaten moet direct en consistent doorwerken in de gehele keten: van data-inname (Laag 1) en de wiskundige planner (Laag 3) tot de actuators (Laag 4) en de GUI dashboards (Laag 5).

---

## 2. Inbreng & Consensus van de Team-Persoonlijkheden

### 🎯 Lead Orchestrator
* **Regie & Fasering:** We splitsen het werk in 5 strak afgebakende, testgedreven fasen. Iedere fase moet afzonderlijk groen testen voordat de volgende start.
* **Invariantenbewaking:**
  * *Invariant 1 (Dumb Views):* De GUI berekent zelf géén hydraulische vermogens of toestanden; de backend levert de subsysteem-statussen en aggregaties kant-en-klaar aan via de API.
  * *Invariant 2 (Entity Isolation):* Geen Home Assistant entiteitsstrings in de kernmodellen (`layer3_scheduling/`, `models/canonical.py`).
  * *Invariant 5 (API/MCP Parity):* Ieder nieuw endpoint of parameter-mutatie wordt synchroon bijgewerkt in `docs/openapi.json` en `mcp_server.py`.
  * *Invariant 6 (Headless Browser QA):* Visuele verificatie met screenshots in de browser vóór afronding.

### ⚡ @energie-expert (Thermodynamica & Daikin Hardware)
* **Hydraulische Interlock:** Als het vat opwarmt via de warmtepomp, moet de CV-vloerverwarmingsoftwarematig in de pauzestand worden gehouden (`isolate_space_heating_during_dhw = True`). Dit voorkomt hydraulische kortsluiting en pendelen.
* **Daikin Reheat Gedrag:**
  * *Daikin Systeeminstelling (Autonoom):* De warmtepomp bepaalt zijn eigen heropwarming op basis van veldinstelling [6-00] (temperatuurverschil, standaard 10 °C onder setpoint) en [6-0C] (reheat temperatuur, 45 °C). Open HEMS bemoeit zich hierbij niet met een softwarematige blokkade, maar monitort alleen en plant zon-boosts naar 60 °C.
  * *Open HEMS Drempelsturing:* Open HEMS dwingt een harde software-ondergrens af (bijv. 40,0 °C bij 50 °C setpoint) via `climate.hc_dhw_dhw_setpoint = off` totdat de goedkoopste uren aanbreken.

### 📐 @wiskundige (Optimalisatie & Dispatch)
* **Parent-Child Capaciteitsvergelijking in LP/MIP:**
  Voor ieder kwartier-tijdslot k geldt:
  `P_warmtepomp(k) = P_cv(k) + P_sww(k)`
  Met de binaire exclusiviteitsvoorwaarde:
  `u_cv(k) + u_sww(k) <= 1`
  Waarbij de compressor nooit gelijktijdig CV en SWW kan leveren.
* **Dynamische Planner-koppeling:** `CentralPlanner` en `DhwTankSpec` moeten hun parameters (`volume_liters`, `auto_start_delta_c`, `reheat_mode`) direct ontlenen aan `cfg["devices"]`.

### 📊 @data-scientist (Telemetrie & Aggregatie)
* **Dynamische Telemetrie Rollen:** Zonnestromen en verbruikers moeten worden geaggregeerd op basis van `role = "producer"` en `role = "consumer"`. Als een gebruiker een 2e omvormer of warmtepomp toevoegt, telt deze automatisch mee in de totaalgrafieken.
* **Plan vs. Actual Synchronisatie:** De historische actuatiegrafieken moeten de toestand van het subsysteem (CV vs SWW) direct kunnen annoteren vanuit de daadwerkelijke InfluxDB vermogens- en klepstanden.

### 🛠️ @software-architect & @qa-agent
* **Datamodel:** Introductie van `parent_device_id` en `subsystems: [...]` in de apparaten-structuur.
* **Auto-Replan:** Een `POST`, `PUT` of `DELETE` op `/api/devices` triggert direct `PlanStore.recalculate()`.
* **Frontend:** Implementatie van de Hoofdkaart met 2 Subkaarten en contextuele device-modals met Tailwind CSS.
* **QA:** Volledige testdekking (pytest) en live screenshot-verificatie via headless browser.

---

## 3. Gefaseerd Stappenplan

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ FASE 1: Datamodel & Parent-Child Relatie                                    │
│ → models/canonical.py, heatpump_config.json, dhw_specs.py                  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ FASE 2: Planner Doorwerking & Auto-Replan                                   │
│ → CentralPlanner leest devices, API CRUD triggert herberekening             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ FASE 3: Backend API & MCP Lockstep Parity                                   │
│ → routes_system.py, mcp_server.py, openapi.json                             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ FASE 4: Frontend UI — Hoofdkaart + 2 Subkaarten & Contextuele Modal          │
│ → web/index.html, web/js/app.js                                             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ FASE 5: QA, Unit Tests & Headless Browser Verificatie                        │
│ → pytest tests/, browser_exec visual audit met screenshots                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### Fase 1: Datamodel & Parent-Child Relatie
**Doel:** Formeel vastleggen van de koppeling tussen de warmtepomp en het tapwatervat en ondersteuning van `reheat_mode`.

1. **Configuratie bijwerken (`/config/heatpump_config.json`):**
   * Voeg `parent_device_id: "daikin_heat_pump"` toe aan apparaat `dhw_tank`.
   * Voeg `subsystems: ["dhw_tank"]` toe aan apparaat `daikin_heat_pump`.
   * Zorg dat `dhw_tank.parameters` bevat:
     * `volume_liters`: 350
     * `target_temp_c`: 50.0
     * `boost_temp_c`: 60.0
     * `emergency_reheat_c`: 38.0
     * `deadband_reheat_c`: 40.0
     * `reheat_mode`: `"daikin_system_setting"` *(of `"openhems_threshold"`)*
2. **Klasse `DhwTankSpec` bijwerken (`layer3_scheduling/dhw_specs.py`):**
   * Laat `DhwTankSpec.from_config(cfg)` eerst zoeken in `cfg.get("devices", [])` naar een apparaat van het type `dhw_boiler` of `thermal_storage`.
   * Val terug op de legacy key `cfg.get("dhw_boiler", {})` als fallback.
3. **Canoniek Datamodel (`models/canonical.py`):**
   * Breid `DeviceConfig` uit met optionele velden `parent_device_id: Optional[str] = None` en `subsystems: List[str] = field(default_factory=list)`.

---

### Fase 2: Planner Doorwerking & Auto-Replan
**Doel:** Zorgen dat wijzigingen aan apparaten direct en automatisch doorwerken in de 24h-planning.

1. **Directe koppeling in `CentralPlanner` (`layer3_scheduling/central_planner.py`):**
   * De planner haalt apparaatparameters (zoals vermogens, volumes en drempels) direct op uit de actieve `DeviceConfig` lijst.
   * Als `reheat_mode == "daikin_system_setting"`, dwingt de planner tijdens normale uren géén kunstmatige uitschakeling af zolang de temperatuur boven noodcomfort (38 °C) ligt.
2. **Auto-Replan Hook in System Router (`api/routes_system.py`):**
   * Voeg na een geslaagde `POST /api/devices`, `PUT /api/devices/{id}` of `DELETE /api/devices/{id}` een directe call toe:
     ```python
     # Trigger onmiddellijke herberekening van de 24h planning
     ensure_active_canonical_plan(force_recalculate=True)
     ```
   * Hierdoor ziet de gebruiker in de GUI direct de invloed van een aangepast apparaat op het dispatch-profiel.

---

### Fase 3: Backend API & MCP Lockstep Parity
**Doel:** Garanderen dat alle nieuwe apparaatparameters en relaties via zowel REST als MCP beschikbaar zijn.

1. **REST Endpoints controleren (`api/routes_system.py`):**
   * Zorg dat `parent_device_id`, `subsystems` en alle type-specifieke `parameters` gevalideerd en bewaard worden in `PUT` en `POST` routes.
2. **MCP Server bijwerken (`mcp_server.py`):**
   * Verifieer dat `openhems_get_devices` de relaties en parameters correct retourneert.
3. **OpenAPI Specificatie (`docs/openapi.json`):**
   * Update het schema voor `DeviceConfig` en `DeviceParameters`.
4. **Guardrail Test draaien:**
   * `pytest tests/architecture/test_api_mcp_lockstep.py`

---

### Fase 4: Frontend UI — Hoofdkaart met 2 Subkaarten & Contextuele CRUD
**Doel:** Een strakke, intuïtieve gebruikerservaring in de WebUI conform de wensen van de gebruiker.

1. **Rendering van de Hoofdkaart + Subkaarten (`web/js/app.js`):**
   * Wanneer de apparatenlijst wordt opgebouwd, detecteert de renderer apparaten met `subsystems` of `parent_device_id`.
   * Voor de Daikin Altherma wordt één grote **Systeem Hoofdkaart** gerenderd met:
     * **Bovenbalk:** Gedeelde hardware (Inepro 102 MQTT meter, SG-Ready sturing, live vermogen, actieve modus).
     * **Linker Subkaart (CV Vloerverwarming):** Doeltemperatuur woonkamer, stooklijn LWT (33–35 °C), modulatiebodem (~950 W), vloertraagheidsindicator (3-4u), actuele toestand (Verwarmen / Standby / Gepauzeerd door SWW). Knop: *Bewerken CV*.
     * **Rechter Subkaart (Warm Tapwatervat 350L):** Actuele temperatuur sensor R5T (49,9 °C), doeltemperatuur (50 °C), boost (60 °C), volume (350L).
     * **Reheat Indicator & Keuze:** Duidelijke badge:
       `⚙️ Reheat: Daikin Systeeminstelling (Autonoom via hardware)` of `Open HEMS Drempel (40 °C)`.
       Knop: *Bewerken Tapwatervat*.
2. **Contextuele Apparaat Modal (`web/index.html` & `web/js/app.js`):**
   * Bij het kiezen van `type = "thermal_storage"` of `"dhw_boiler"` toont de modal automatisch:
     * Dropdown **Reheat Modus**:
       * `Daikin Systeeminstelling (Warmtepomp bepaalt zelf via interne thermostaat)`
       * `Open HEMS Drempelsturing (Softwarematige drempel)`
     * Invoerveld **Reheat Drempel (°C)** (standaard `40.0`).
     * Invoerveld **Doeltemperatuur Normaal (°C)** (standaard `50.0`).
     * Invoerveld **Zonne-Boost Temperatuur (°C)** (standaard `60.0`).
     * Invoerveld **Watervolume (L)** (standaard `350`).
     * Dropdown **Gekoppelde Hoofdunit (Parent)**: selectie van `daikin_heat_pump`.
   * Bij andere apparaattypes (zonnepanelen, batterij) toont de modal hun eigen relevante velden (Wp, omvormervermogen, accucapaciteit).

---

### Fase 5: QA, Unit Tests & Headless Browser Verificatie
**Doel:** Sluitende verificatie vóór oplevering conform Invariant 4 en Invariant 6.

1. **Geautomatiseerde Backend Tests:**
   * Draai alle tests: `PYTHONPATH=. pytest tests/`
   * Valideer dat de anti-drift guardrails groen blijven: `pytest tests/architecture/test_anti_drift_guards.py`
2. **Headless Browser Visual Audit (QA Agent via `browser_exec`):**
   * Navigeer naar de Open HEMS WebUI (tabblad *Apparaten*).
   * Maak een screenshot (`capture_screenshot()`) van de nieuwe Hoofdkaart met de twee Subkaarten.
   * Open de Bewerk-modal van het tapwatervat en verifieer de velden (Reheat Mode, Drempel 40 °C).
   * Controleer de browserconsole op JavaScript-fouten.

---

## 4. Akkoord & Startsein

Dit plan waarborgt zowel de fysische realiteit van de warmtepompinstallatie als een schaalbare software-architectuur voor toekomstige apparaten.

Na jouw akkoord starten we direct met **Fase 1** en **Fase 2**.
