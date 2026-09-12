# Product Requirements Document (PRD) & Systeemarchitectuur
# Open HEMS (Home Energy Management System)

**Versie:** 1.0.0-RC1  
**Auteur:** Hermes Agent  
**Eigenaar / Opdrachtgever:** Matthijs van den Berg  
**Datum:** September 2026  
**Status:** Ter Review & Goedkeuring  
**Licentie:** Open Source (MIT)  

---

## 1. Executive Summary & Kernfilosofie

Open HEMS is een modulair, open-source energiemanagementsysteem voor residentiële woningen met complexe thermische en elektrische energiestromen (warmtepomp, groot tapwatervat, zonnepanelen, dynamische energiecontracten en optionele thuisbatterij).

### 1.1 De Drie IJzeren Ontwerpprincipes
1. **Volledige Standalone Autonomie (HA als optionele Provider/Actuator):**  
   Open HEMS is **geen** Home Assistant afhankelijkheid. Het kernsysteem (Core Engine) draait als zelfstandig Linux-proces of Docker container. Home Assistant fungeert uitsluitend als één van de vele mogelijke *data providers* (uitlezen van sensoren via REST/WebSocket) en *actuators* (schakelen van relays/entiteiten via services). Als Home Assistant herstart, crasht of offline is, blijft Open HEMS zelfstandig doordraaien op basis van directe Modbus-, P1- of MQTT-verbindingen.
2. **Single Source of Truth (Eén Datamodel, Geen UI-Math):**  
   Alle visualisaties, dashboards, tijdbalken, advieskaarten en actuatoren consumeren hetzelfde, centrale, onveranderlijke publicatie-object (`CanonicalDispatchPlan`). Presentatielagen zijn "dom" (*dumb views*): ze berekenen zélf nooit stookmomenten, tellen geen energie op en hebben geen eigen kleurendefinities.
3. **Strikte No-Mock-Data Directive & Data-Integriteit:**  
   In productieberekeningen, adviezen, rapportages en actuatie worden **nooit** gesimuleerde, verzonnen of placeholder-data gebruikt. Alle berekeningen worden gevoed door live telemetrie (InfluxDB, Modbus, DSMR/P1), geverifieerde EPEX-beurstarieven (EnergyZero API) en lokale weerassimilatie (Wittboy GW2000A en Open-Meteo). Ontbrekende of verouderde data wordt geëxpliciteerd met kwaliteitsvlaggen (`Quality.STALE`, `Quality.INTERPOLATED`) en opgevangen via veilige noodprofielen, nooit door fictieve cijfers.

---

## 2. Systeembaseline & Fysische Installatie (Referentie Matthijs)

Het wiskundige model is primair gekalibreerd op de fysieke installatie van Matthijs van den Berg te Culemborg:

* **Warmtepomp Buitenunit:** Daikin Altherma 3 H HT (EPRA18DW17) — 18 kW thermisch, 3-fase 400V, R32 koudemiddel.
* **Warmtepomp Binnenunit:** Daikin Hydrobox (ETBX16E9W7) met geïntegreerde 9 kW elektrische back-up heater (BUH).
* **Tapwatersysteem (DHW):** OEG 350 L geëmailleerd boilervat met grote warmtewisselaar. Thermische capaciteit: $0,407\text{ kWh/K}$.
  * Normaal setpoint: 50,0°C.
  * Zonnebuffer boost: 60,0°C.
  * Elektrisch vermogen: ~1,8 kW (standaard) tot ~2,4 kW (boost).
* **Aansturing & Relais (Daikin Smart Grid Contacts):**
  * Hardware relays S10S en S11S aangesloten op Daikin Smart Grid interface.
  * Vluchtige RAM-sturing (geen EEPROM-degradatie).
  * Fysische ontkoppeling: Tijdens actieve SWW/DHW-runs (`SG4`) wordt de CV-hoofdschakelaar (`switch.hc_mode_altherma_on`) uitgeschakeld om te voorkomen dat de 9 kW BUH gelijktijdig inschakelt op de CV-groep.
* **Vloerverwarming (CV):**
  * 2-massa thermisch model (dekvloer + binnenlucht).
  * Modulatievloer Daikin compressor: ~950 W elektrisch minimum.
  * Hysterese: 0,5°C onder `target_temp_low`.
* **Zonnepanelen (PV):** Inepro 103 meter, 5,76 kWp veld (34° helling, 225° zuidwest), 5,5 kW omvormer.
* **Energietarief:** Powerpeers dynamisch contract (EPEX spotprijs + €0,0121 inkoopopslag + €0,11085 energiebelasting & ODE + 21% btw; vaste kosten €6,25/mnd).

---

## 3. De 5-Lagen Doelarchitectuur (Clean Architecture)

De software is gestructureerd volgens een strikt eenrichtings-gegevensstroommodel:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      EXTERNE BRONNEN & PROTOCOLLEN                      │
│   Modbus TCP  │  P1 DSMR  │  MQTT  │  InfluxDB  │  EPEX API  │ Weer API  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAAG 1: COLLECTIE & INGESTIE (layer1_data_collection)                   │
│ - Hardware adapters (Modbus, Influx, P1, Home Assistant Provider)       │
│ - Asynchrone data-collectie in 60s ringbuffers                          │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAAG 1B: TELEMETRY SANITIZER & DATA QUALITY (sanitizer.py)              │
│ - Freshness Watchdog: controleert of telemetrie < 15 min oud is         │
│ - Kwartier-rooster synchronisatie (15m slot alignment vanaf 'Nu')       │
│ - Fysische grensbewaking (PV <= 6kW, temps tussen -30°C en 95°C)        │
│ - Output: 1 contractueel 'CleanTelemetryFrame' met Quality-vlaggen      │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAAG 2: KALIBRATIE & ZELFLEREND MODEL (layer2_calibration)              │
│ - 7×96 Kwartieren Matrix: Historisch referentieverbruik per weekdag     │
│ - EWMA Residual Learning: Realtime bijsturing op recente afwijkingen    │
│ - Lokale assimilatie: Wittboy GW2000A zonnestraling vs weersvoorspelling│
│ - Model Governance: Automatische parameter-validatie                    │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAAG 3: CENTRALE OPTIMALISATIE & DISPATCH (layer3_scheduling)           │
│ - Centrale Planner: Eén wiskundig oplosser voor DHW, CV, PV en Batterij │
│ - Dynamische Spitsdetector: Kwartieranalyse met wintercomfort-cap (2,5u)│
│ - Fysieke Dagprioriteit: 10:00–16:00u altijd zonnebuffer vóór nachtrun   │
│ - Nachtdal Tie-breaker: 03:30u i.p.v. vlakke middernacht                │
│ - Toewijzing van de gestandaardiseerde 6-status taxonomie                │
│ - Output: 1 onveranderlijk 'CanonicalDispatchPlan'                      │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAAG 3B: PLAN STORE & RECOMMENDATION REGISTRY (plan_store.py)           │
│ - Thread-safe centrale in-memory Singleton Store                        │
│ - Versiebeheer & Publicatie-auditlog in InfluxDB (hems_recommendations) │
│ - Single Source of Truth voor álle consumenten                          │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
         ┌───────────────────────────┴───────────────────────────┐
         ▼                                                       ▼
┌─────────────────────────────────┐   ┌───────────────────────────────────┐
│ LAAG 4: ACTUATIE & CONTROLE     │   │ LAAG 5: PRESENTATIE & UI (DOM)    │
│ (layer4_control)                │   │ (daemon.py & Lovelace)            │
│ - Standalone relaisactuators    │   │ - Tab Voorspelling / Analyse      │
│ - Home Assistant Actuator Plugin│   │ - Tab Zelflerend Model (Decomp.)  │
│ - Modbus registerschrijvers     │   │ - Tab Historie & Validatie        │
│ - Dry-run / Safe-mode isolatie  │   │ - PURE RENDERING: Geen berekening,│
│ - Falen-naar-veilig (SG2 norm.) │   │   geen eigen kleuren of math!     │
└─────────────────────────────────┘   └───────────────────────────────────┘
```

---

## 4. Functionele Specificaties per Component

### 4.1 Laag 1B: Telemetry Sanitizer (`sanitizer.py`)
* **Doel:** Voorkomen dat ruwe, corrupte of ontbrekende data doordringt tot de rekenkern.
* **Eisen:**
  1. *Freshness Check:* Als de laatste sensor-tijdstempel ouder is dan 15 minuten, wordt `is_fresh = False` gemarkeerd en een waarschuwingsvlag geregistreerd.
  2. *Grid Alignment:* De tijdlijn wordt altijd vastgezet op het actuele afgeronde kwartier (bijv. 09:37 wordt afgerond naar 09:30).
  3. *Outlier Rejection:* Waarden buiten fysische limieten (bijv. negatieve zonnestroom, kamertemperatuur < 5°C of > 40°C, boilertemperatuur > 95°C) worden afgewezen en gemarkeerd.
  4. *Interpolatie:* Ontbrekende kwartier-spotprijzen worden veilig aangevuld via het laatste bekende uur met vlag `Quality.INTERPOLATED`.

### 4.2 Laag 2: Zelflerend Model (7×96 Matrix & Fysica)
* **7×96 Kwartieren Matrix:**
  * Bevat de zuivere statistische basisbehoefte van de woning per weekdag (Ma t/m Zo) in 96 kwartieren (00:00–24:00), gefit op 374+ dagen InfluxDB telemetrie.
  * Dit is de *statistische referentiebehoefte* vóór dynamische sturing of weersinvloeden.
* **EWMA Residual Learning:**
  * Exponentieel gewogen voortschrijdend gemiddelde dat kortstondige levenspatronen (bijv. feestdagen, vakantie, ziekbed) adaptief meeneemt zonder historische data te wissen.
* **Wittboy Weerassimilatie:**
  * Realtime vergelijking van lokale stralingsmeting met de Open-Meteo satellietvoorspelling; past de zonvoorspelling aan met een nudging-factor.

### 4.3 Laag 3: Centrale Optimalisatie & Beslisregels
* **DHW 350L Thermisch Model & Prioriteiten:**
  1. *Fysische Dag-Prioriteit:* Zolang er daglichturen (10:00–16:00u) in het vooruitzicht liggen, plant het systeem **altijd eerst** de dagrun (standaard 50°C of zonnebuffer boost naar 60°C). Een nachtrun wordt pas als secundair vangnet geëvalueerd voor de *daaropvolgende* nacht.
  2. *Zonnebuffer Boost (60°C):* Als het verwachte zonne-overschot overdag $\ge 2,5\text{ kWh}$ bedraagt, wordt automatisch doorgestookt naar 60°C (opslaan van goedkope thermische energie).
  3. *Nachtdal Tie-Breaker (03:30u):* Wanneer EPEX-tarieven na middernacht vlak zijn (of nog niet gepubliceerd vóór 13:00u), kiest het algoritme statistisch voor 03:30u in plaats van blind 01:00u.
  4. *Counterfactual Ongeheate Simulatie:* Berekent exact het afkoelverloop zonder bijverwarming inclusief douche-onttrekkingen, en toont de berekende dip in het dashboard ter verantwoording van stookbesluiten.
* **Dynamische Spitsdetector op Kwartierbasis:**
  1. *Percentielclustering:* Detecteert prijspieken via $P_{75}$ en $P_{85}$ drempels en een absolute delta van $\Delta P \ge \text{€0,030–€0,050}$ t.o.v. de dagmediaan.
  2. *Micro-piek Filtering:* Prijspieken korter dan 30 minuten worden genegeerd om pendelgedrag van de warmtepomp te voorkomen.
  3. *Harde Winter Comfort Cap (max 150 min):* Een harde uitschakeling (`forced_off`) wordt strikt begrensd op maximaal 2,5 uur om afkoeling van de vloer in de winter te verhinderen. Flank-uren worden omgezet naar `advised_off` (CV op minimale modulatievloer ~950W).

### 4.4 Gestandaardiseerde 6-Status Taxonomie
Elke statuscode heeft in de hele applicatie (database, API, tijdbalk, grafieklegenda, kaarttekst) dezelfde definitie, HEX-kleur en gedrag:

| Code | Label | HEX-kleur | Visualisatie | Betekenis & Aansturing |
| :--- | :--- | :--- | :--- | :--- |
| **`forced_off`** | Geforceerd uit (blok) | `#EF4444` | Rood gestreept | Harde spitsblokkade: compressor vergrendeld (SG1/relais open). |
| **`advised_off`** | Geadviseerd uit | `#F59E0B` | Massief oranje | Schouderpiek: uitstel zware apparaten; CV gemoduleerd op laagste vloer. |
| **`normal`** | Normaal | `#1E293B` | Donkergrijs | Standby / vrije operatie binnen comfortgrenzen. |
| **`advised_on`** | Geadviseerd aan | `#4ADE80` | Lichtgroen gestreept | Voordelig venster: warmtepomp mag hoger doorverwarmen voor CV vloerbuffer. |
| **`forced_on`** | Geforceerd aan | `#10B981` | Massief groen | Actieve stookrun naar setpoint (50°C) voor boiler of CV (dag- én nachtrun). |
| **`max_on`** | Maximaal aan (60°C) | `#A855F7` | Massief paars | Zonnebuffer boost: doorverwarmen naar 60°C op zonne-overschot. |

---

## 5. Home Assistant Integratie & Decoupling Strategy

Open HEMS is ontworpen als autonoom opensource-pakket. Home Assistant is **optioneel**.

### 5.1 Standalone Modus (Zonder Home Assistant)
* **Data-invoer:** Directe Modbus TCP client (bijv. naar Deye omvormer of Daikin RTU gateway), P1 serial/MQTT reader, InfluxDB time-series database.
* **Actuatie:** Directe GPIO relaissturing (Raspberry Pi/Linux hardware pins) of Modbus TCP registerschrijvers (Deye / SunSpec).
* **Interface:** Standalone ingebouwde webserver op poort 8099 met de complete Open HEMS cockpit.

### 5.2 Home Assistant Gekoppelde Modus (Add-on of Plugin)
In deze modus draait Open HEMS als container of HAOS Add-on:
* **HA Data Provider Adapter (`ha_collector`):**
  * Leest sensoren (`sensor.p1_power`, `climate.woonkamer_climate_daikin`, `sensor.hc_dhw_temperature_r5t_dhw_tank`) via de HA WebSocket API of REST `/api/states`.
  * Geeft de data door aan Laag 1B (Telemetry Sanitizer).
* **HA Actuator Adapter (`ha_actuator`):**
  * Ontvangt dispatch-opdrachten van Laag 4.
  * Roept services aan (`climate.set_temperature`, `switch.turn_on` / `switch.turn_off`).
  * Garandeert dat als een service-aanroep faalt, een veilige fallback plaatsvindt zonder de planner te blokkeren.
* **HA Ingress UI:**
  * De standalone webcockpit wordt 1-op-1 ingesloten in het Home Assistant linker zijmenu via Ingress, waarbij relatieve API-paden (`./api/...`) worden gebruikt.

---

## 6. Datamodellen & API Contracten

### 6.1 `CleanTelemetryFrame` (Laag 1B Output)
```python
@dataclass
class CleanTelemetryFrame:
    timestamp: datetime
    is_fresh: bool
    freshness_age_seconds: float
    resolution_minutes: int  # 15 min
    slots: List[TelemetrySlot]  # 96 kwartieren
    validation_errors: List[str]
```

### 6.2 `CanonicalDispatchPlan` (Laag 3 Publicatie)
```python
@dataclass
class CanonicalDispatchPlan:
    generated_at: str  # ISO timestamp
    horizon_hours: float  # 24.0 tot 48.0
    resolution_mins: int  # 15
    is_fresh: bool
    slots: List[DispatchPlanSlot]  # 96 kwartieren vanaf Nu
    dhw_summary: DHWPlanSummary
    dynamic_peaks: List[Dict[str, Any]]
    validation_issues: List[str]
```

### 6.3 REST Endpoints (Dumb Presentation Consumers)
* `GET /api/schedule/chart-data`: Levert het actieve plan voor de 24-uurs dispatch grafiek en tijdbalk.
* `GET /api/model/decomposition`: Levert exact dezelfde 96 slots uit `PlanStore` voor de gestapelde decompositiegrafiek op het Zelflerend tabblad (Single Source of Truth).
* `GET /api/model/dhw-status`: Levert de `dhw_summary` met counterfactual ongeheate curve en beslisdata.
* `GET /api/health/consistency`: Realtime kwaliteitscontroleur die de versheid, slot-consistentie en taxonomie-uniformiteit toetst.

---

## 7. Kwaliteitsborging, Validatie & Anti-Drift Regels

Om toekomstige regressie of inconsistenties onmogelijk te maken, gelden de volgende strikte software-eisen:

1. **Vector-Consistentie Invariant:**
   * Een geautomatiseerde test toetst dat `/api/schedule/chart-data` en `/api/model/decomposition` op ieder kwartier exact identieke getallen (Watt, EUR, kWh) teruggeven.
2. **Kleur- en Taxonomie-Invariant:**
   * Elke status (`StandardizedState`) heeft exact één vastgelegde HEX-code. Geen enkel script of frontend mag hiervan afwijken.
3. **AST / Lint Guardrail:**
   * Geen enkele HTTP GET handler in `daemon.py` mag zelf wiskundige berekeningen uitvoeren; handlers mogen uitsluitend data ophalen via `PlanStore.get_plan()`.
4. **Pre-Commit Beveiliging:**
   * Elke release wordt voorafgegaan door een scan op wachtwoorden/tokens (`secret_scanner.py`) en volledige bytecode-compilatie.
5. **Snapshot Backups:**
   * Voorafgaand aan elke live containerupgrade wordt automatisch een snapshot opgeslagen in `/config/addons_archive/open-hems/` (minimaal 5 versies retentie).

---

## 8. Release Roadmap

* **v0.88.x:** Uniformering van visualisaties en tijdbalk-taxonomie.
* **v0.89.x:** Implementatie van `TelemetrySanitizer`, `PlanStore` en `CentralPlanner`.
* **v0.90.x:** Integratie van `/api/health/consistency` en volledige ontkoppeling van Home Assistant.
* **v1.0.0:** Eerste formele Open Source publicatie met standalone installer en Home Assistant Community Add-on repository.
