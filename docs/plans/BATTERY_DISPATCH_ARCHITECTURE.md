# Open HEMS — Thuisbatterij Integratie & Dispatch Specificatie
**Documentversie:** 2.0.0 (Engineering Specificatie)  
**Status:** In Implementatie (Fase 1 gereed)  
**Referenties:** ADR-002, ADR-005, `layer3_scheduling/battery_policy.py`

---

## 1. Doel & Systeemspecificaties

Open HEMS stuurt de thuisbatterij aan als **3e Prioriteit** achter het boilervat (Prioriteit 1) en de cv-vloerbuffer (Prioriteit 2). 

### Fysische & Economische Parameters
* **Nominale Capaciteit:** 15,0 kWh LFP (13,5 kWh bruikbaar venster).
* **SoC Vangrails:** Minimaal 10% (beveiliging diepontlading) tot maximaal 95% (levensduurbehoud).
* **Vermogensgrenzen:** Maximaal 5,0 kW laden en 5,0 kW ontladen (omvormer AC-grens).
* **Roundtrip Efficiency:** 87% ($\eta_{\text{one-way}} \approx 93,27\%$).
* **Levelized Cost of Cycling (LCOC):** € 0,078 per doorgeladen kWh (€ 6.000 aanschafwaarde / 76.500 kWh levensduur).
* **Operationele Modus:** **Achter-de-meter Nul-op-de-Meter** (Zelfconsumptie maximaliseren en afnamepieken afvlakken). Geen actieve net-naar-net handel.
* **Instelbare Drempelwaarde:** `min_cycle_margin_eur_kwh` (standaard: € 0,085/kWh netto marge bovenop inkoop en degradatie).

---

## 2. De Loosely Coupled Prioriteiten-Waterval

In elk kwartier ($k$) van de 24–48 uurs horizon wordt de batterij loosely coupled geoptimaliseerd op het **resterende netto vermogen** ná de warmtepomp:

$$P_{\text{rest}, k} = P_{\text{baseload}, k} + P_{\text{dhw}, k} + P_{\text{cv}, k} - P_{\text{zon}, k}$$

```
[ Bruto Zonneproductie P_pv,k ]
              │
              ▼
   [ 1. Huishoudelijke Baseload ] ──> ~300 W continue afname (Prioriteit 0)
              │ (Resterend PV Overschot)
              ▼
   [ 2. DHW Boilervat (Prioriteit 1) ] ──> 2,7 kW el (COP 3.1, 0 ct degradatie)
              │ (Resterend PV Overschot)
              ▼
   [ 3. CV Vloerbuffer (Prioriteit 2) ] ──> 1,5 - 4,5 kW el (COP 4.5, 0 ct degradatie)
              │ (Resterend PV Overschot P_rest,k < 0)
              ▼
   [ 4. THUISBATTERIJ LADEN (Prioriteit 3) ] ──> Min(|P_rest,k|, 5 kW) tot 95% SoC
              │ (Rest-overschot)
              ▼
   [ 5. Teruglevering aan het Net ] ──> Exporteer tegen p_export
```

### Waarom Prioriteit 3:
1. **DHW (COP ~3,1):** 1 kWh stroom = 3,1 kWh nuttige warmte. Degradatie = € 0,00.
2. **CV Vloerbuffer (COP ~4,5):** 1 kWh stroom = 4,5 kWh warmte in beton. Degradatie = € 0,00.
3. **Batterij (COP < 1,0):** 1 kWh stroom in = 0,87 kWh stroom uit, met € 0,078 degradatiekost.
*Regel: Thermische opslag met warmtepomp gaat altijd voor op elektrochemische opslag.*

---

## 3. Dedicated Batterij Toestanden (`mode_code`)

De batterij heeft een eigen toestandscatalogus in `DeviceSlotDispatch`:

| Toestandcode | Label | Fysische / Economische Actie |
| :--- | :--- | :--- |
| **`CHARGE_SOLAR`** | Zon-absorptie | Laadt overtollige zonnestroom op (tot max 5 kW) zodra $P_{\text{rest}} < 0$. |
| **`DISCHARGE_PEAK`** | Spitsontlasting | Ontlaadt maximaal om netafname naar 0 W te drukken tijdens harde spitsblokken of de hoogste prijspieken. |
| **`HOLD_RESERVE`** | Reserveren | Houdt capaciteit vast tijdens gematigd dure uren wanneer er binnen de horizon een duurdere piek volgt die deze energie nodig heeft (*"Kruit niet te vroeg verschieten"*). |
| **`CHARGE_GRID`** | Nachtladen (Dal) | Laadt in de winter 's nachts bij vanaf het net als de spread met de komende dagpiek rendabel is: $P_{\text{piek}} - \frac{P_{\text{dal}}}{0,87} - 0,078 \ge \text{marge}$. |
| **`DISCHARGE_BUFFER`** | Restontlading | Voedt late avond-baseload met overtollige accucapaciteit die niet nodig is voor de ochtendpiek. |
| **`STANDBY`** | Standby | Accu is leeg (10%), vol (95%), of actuele prijzen rechtvaardigen geen actie. |

---

## 4. Wiskundig Optimalisatie-Algoritme (`BatteryPolicy`)

Het algoritme in `layer3_scheduling/battery_policy.py` draait elk kwartier over 96 tot 192 slots via een multi-pass methode:

1. **Pass 1 (Solar Soak):** Detecteer alle slots met $P_{\text{rest}} < -0,05\text{ kW}$ en ken `CHARGE_SOLAR` toe tot $P_{\text{ch,max}}$ en $SoC \le 95\%$.
2. **Pass 2 (Valley & Opportunity Check):** Identificeer daluren geschikt voor winternacht-laden waar de voorwaartse spread met komende pieken $\ge \text{min\_cycle\_margin}$ is.
3. **Pass 3 (Voorwaartse Simulatie met Lookahead):**
   * Bij positief $P_{\text{rest}}$: Bereken de benodigde energie voor toekomstige duurdere pieken binnen een 8-uurs venster.
   * Is het huidige slot de piek of een hard spitsblok? $\to$ `DISCHARGE_PEAK`.
   * Is er een duurdere piek in aantocht en geen overschot? $\to$ `HOLD_RESERVE`.
   * Is er wel overschot boven de toekomstige piekbehoefte? $\to$ `DISCHARGE_BUFFER`.
4. **Pass 4 (Boekhouding):**
   * Berekent cumulatieve $kWh_{\text{in}}$ (zon vs net), $kWh_{\text{uit}}$, SoC-verloop en netto financiële besparing:
     $$\text{Besparing} = \sum \left( E_{\text{dis}} \cdot P_{\text{in,vermeden}} - E_{\text{dis}} \cdot C_{\text{deg}} \right) - \sum \left( E_{\text{grid\_ch}} \cdot P_{\text{in,dal}} \right)$$

---

## 5. Datamodellen & Contracten

### 5.1 Dataclasses (`layer3_scheduling/battery_policy.py`)
```python
@dataclass(frozen=True)
class BatterySpec:
    capacity_kwh: float = 15.0
    usable_capacity_kwh: float = 13.5
    min_soc_pct: float = 10.0
    max_soc_pct: float = 95.0
    max_charge_kw: float = 5.0
    max_discharge_kw: float = 5.0
    roundtrip_efficiency: float = 0.87
    degradation_cost_eur_kwh: float = 0.078
    min_cycle_margin_eur_kwh: float = 0.085

@dataclass
class BatterySlotResult:
    slot_idx: int
    power_kw: float          # + = laden, - = ontladen
    mode_code: str           # CHARGE_SOLAR, CHARGE_GRID, HOLD_RESERVE, etc.
    mode_label: str
    soc_pct: float
    soc_kwh: float
    cost_impact_eur: float
```

### 5.2 ADR-002 Inpassing in Canonical Plan (`models/canonical.py`)
In elk `DispatchPlanSlot`:
```python
slot.device_dispatches["home_battery"] = DeviceSlotDispatch(
    device_id="home_battery",
    device_type="home_battery",
    mode_code=res.mode_code,
    mode_label=res.mode_label,
    electric_kw=res.power_kw,
    payload={
        "soc_pct": res.soc_pct,
        "soc_kwh": res.soc_kwh,
        "cost_impact_eur": res.cost_impact_eur
    }
)
```

---

## 6. UI & Presentatie Contract

1. **Centrale Energie-grafiek:**
   * Laadvermogen ($+ kW$) verschijnt als stroomopname (boven de nullijn).
   * Ontlaadvermogen ($- kW$) verschijnt onder de nullijn en vlakt de netafname direct af.
2. **Centrale Kosten-grafiek:**
   * De vermeden dure stroominkoop verlaagt de kostenbalken in spitsuren automatisch naar 0 euro.
3. **Modusplanning Tijdlijn:**
   * Warmtepomp behoudt haar eigen stooktijdlijn.
   * Batterij krijgt een eigen subtijdlijn met de statussen: *Zon-absorptie*, *Nachtladen*, *Spitsontlasting*, *Reserveren*, *Standby*.
4. **KPI & Instellingen:**
   * Dedicated kaart voor Thuisbatterij (actuele SoC, cycli, dagbesparing).
   * Instellingen-slider voor minimale spread/winstmarge (`min_cycle_margin_eur_kwh`).

---

## 7. Implementatie Stappenplan

* [x] **Fase 0 (Isolatie):** `WP-BAT0` (accu-stromen isoleren van baseload) in `daemon.py`.
* [x] **Fase 1 (Registry & Contracten):** ADR-005, interfaces, registry en `DispatchPlanSlot.device_dispatches` (Schema 1.1.0).
* [x] **Fase 2 (Rekenkern):** `layer3_scheduling/battery_policy.py` met 100% testdekking (12/12 unit tests, 177/177 totaal groen). Inclusief correcties op dubbele round-trip-correctie en economische piek-gate.
* [ ] **Fase 3 (Planner Integratie):** Aanroep van `BatteryPolicy.optimize` in `layer3_scheduling/central_planner.py` op het residuele profiel na DHW en CV.
* [ ] **Fase 4 (API & Presenter):** `api/routes_schedule.py` en `kpi_presenter.py` voeden met de werkelijke `device_dispatches["home_battery"]`.
* [ ] **Fase 5 (Frontend):** UI batterijlijn, laad/ontlaadbalken en instellingen-slider in Web cockpit.
* [ ] **Fase 6 (Hardware Adapter):** Modbus TCP driver (`integrations/home_battery/`) voor live telemetrie en sturing.
