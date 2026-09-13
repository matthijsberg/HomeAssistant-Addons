# Open HEMS — Semantisch Data Model & Annotatie Standaard
Versie: `1.0.0` (Standaardisatie voor Grafana, InfluxDB & Externe Integraties)

Dit document definieert het **formele semantische datamodel** van Open HEMS. Het is ontworpen om maximale transparantie, eenduidige veldnaamgeving en directe compatibiliteit met analysetools zoals **Grafana**, **Home Assistant**, **InfluxDB (1.8 & 2.x)**, **PostgreSQL/TimescaleDB** en **REST APIs** te garanderen.

---

## 🏛️ 1. Kernprincipes & Ontologie

Het datamodel volgt de industriestandaarden voor energiebeheersystemen (IEC 61850 / SAREF4ENER / Brick Schema), versimpeld naar een pragmatisch en snel tijdreeksmodel:

1. **Strikte SI-Eenheden in Veldnamen:**
   * Vermogens bevatten altijd de eenheid in de naam: `_w` (Watt) of `_kw` (kiloWatt).
   * Energieën bevatten altijd: `_kwh` (kiloWattuur).
   * Temperaturen bevatten altijd: `_c` (graden Celsius).
   * Financiële data bevat altijd: `_eur` of `_eur_kwh`.
   * *Principe: Nooit gissen of een veld in Watt of kW staat.*

2. **Vector & Flow zijn Eersteklas Dimensies (Tags):**
   * **`vector`**: De energiedrager (`ELECTRICITY`, `HEAT`, `GAS`, `WATER`).
   * **`flow`**: De fysieke richting van de energiestroom (`IMPORT`, `EXPORT`, `GENERATION`, `CONSUMPTION`, `STORAGE`).

3. **Scheiding tussen Fysieke Apparaten en Logische Balansen:**
   * Fysieke apparaten (`device_type` = `grid_meter`, `solar_pv`, `heat_pump`, `thermal_buffer`) dragen hardware-metingen.
   * Gesynchroniseerde balansen (`source` = `canonical_accumulator`, `device_type` = `balance`) bevatten berekende netto-stromen en het *ongedefinieerd verbruik*.

4. **Gestandaardiseerde 6-Status Taxonomie (Dispatch):**
   * Alle besluiten worden gelogd met de canonieke statuscodes:
     - `forced_off` (SG1 / Spitsblokkade)
     - `advised_off` (SG2 / Economisch blokadvies)
     - `normal` (SG2 / Automatische weersafhankelijke regeling)
     - `advised_on` (SG3 / Pre-heat vloerbuffer boost)
     - `forced_on` (SG4 / Basislading 50°C)
     - `max_on` (SG4 / Zonnebuffer doorverwarming 60°C)

---

## 📊 2. InfluxDB Data Dictionary (Database: `openhems`)

### 2.1 Measurement: `energy_telemetry` (Fysieke & Balans Telemetrie)
*Meetinterval: 10s gesampled, elke 60s als gewogen gemiddelde geflusht.*

#### Tags (Indexen voor snelle filtering):
| Tag | Datatype | Toegestane Waarden | Omschrijving |
| :--- | :--- | :--- | :--- |
| `device_id` | `string` | `main_grid_meter`, `rooftop_solar`, `daikin_heat_pump`, `dhw_tank`, `outdoor_weather` | Unieke identifier van de bron |
| `device_type` | `string` | `grid_meter`, `solar_pv`, `heat_pump`, `thermal_buffer`, `weather_station`, `balance` | Apparaatklasse |
| `flow` | `string` | `IMPORT`, `EXPORT`, `GENERATION`, `CONSUMPTION`, `STORAGE`, `STATE` | Richting van de energiestroom |
| `vector` | `string` | `ELECTRICITY`, `HEAT` | Energiedrager |
| `mode` | `string` | `heating`, `dhw`, `standby`, `cooling` *(optioneel)* | Actieve werkingsmodus warmtepomp |
| `source_type` | `string` | `live_meter`, `ha_state`, `canonical_accumulator` | Herkomst van het signaal |

#### Fields (Meetwaarden):
| Veldnaam | Type | Eenheid | Omschrijving |
| :--- | :--- | :--- | :--- |
| `power_w` | `float` | $W$ | Actueel momentaan elektrisch of thermisch vermogen |
| `temperature_c` | `float` | $^\circ\text{C}$ | Gemeten temperatuur (vat, buiten, binnen) |
| `p1_import_w` | `float` | $W$ | Momentaan afnamevermogen van het openbare elektriciteitsnet |
| `p1_export_w` | `float` | $W$ | Momentaan terugleververmogen aan het net |
| `solar_w` | `float` | $W$ | Momentaan zonne-opwekvermogen (Inepro 103 / omvormer) |
| `heatpump_w` | `float` | $W$ | Momentaan elektrisch vermogen van de warmtepomp (Inepro 102) |
| `direct_solar_w`| `float` | $W$ | Zonnestroom die direct in de woning wordt benut ($\min(\text{solar}, \text{verbruik})$) |
| `total_house_w` | `float` | $W$ | Totale bruto elektriciteitsvraag van het huis ($P_{\text{import}} - P_{\text{export}} + P_{\text{solar}}$) |
| `unallocated_w` | `float` | $W$ | Ongedefinieerd huishoudelijk verbruik ($P_{\text{totaal}} - P_{\text{warmtepomp}}$) |

---

### 2.2 Measurement: `dispatch_schedule` (24-Uurs Vooruitblik & Planning)
*Interval: Elk kwartier (15m) opnieuw geëvalueerd en weggeschreven voor de komende 96 kwartieren.*

#### Tags:
| Tag | Datatype | Omschrijving |
| :--- | :--- | :--- |
| `source` | `string` | `central_planner` |
| `state_code`| `string` | 6-status code (`forced_off`, `advised_off`, `normal`, `advised_on`, `forced_on`, `max_on`) |
| `resolution`| `string` | `15m` of `1h` |

#### Fields:
| Veldnaam | Type | Eenheid | Omschrijving |
| :--- | :--- | :--- | :--- |
| `price_eur` | `float` | €/kWh | Dynamisch all-in elektriciteitstarief (beurs + belasting + opslag) |
| `solar_kw` | `float` | $kW$ | Fysisch gekalibreerde zonnevoorspelling (Forecast.Solar EWMA) |
| `unallocated_kw`| `float`| $kW$ | Voorspeld leefpatroon-verbruik (7x96 historische matrix) |
| `boiler_kw` | `float` | $kW$ | Gepland elektrisch vermogen voor tapwaterverwarming |
| `heating_kw` | `float` | $kW$ | Gepland elektrisch vermogen voor CV vloerverwarming (2R1C model) |
| `net_power_kw` | `float` | $kW$ | Verwachte netto afname ($>0$) of teruglevering ($<0$) op het net |
| `target_dhw_temp_c`| `float`| $^\circ\text{C}$ | Doeltemperatuur boiler ($50{,}0^\circ\text{C}$ of $60{,}0^\circ\text{C}$) |
| `room_temp_c` | `float` | $^\circ\text{C}$ | Voorspelde binnentemperatuur woning o.b.v. vloerbuffer |
| `cop` | `float` | - | Verwachte Daikin Altherma Carnot COP |

---

### 2.3 Measurement: `hems_annotations` (Grafana Event Annotaties)
*Geschreven bij elke statuswisseling, optimalisatie-actie of interventie.*

#### Tags:
| Tag | Omschrijving |
| :--- | :--- |
| `event_type` | Categorie: `dhw_run`, `cv_preheat`, `peak_lockout`, `run_merger`, `system_release` |
| `severity` | Niveau: `info`, `warning`, `critical` |
| `state_code` | De resulterende 6-status code |

#### Fields:
| Veldnaam | Type | Omschrijving |
| :--- | :--- | :--- |
| `title` | `string` | Korte koptekst voor in Grafana vlag (bijv. "⚡ DHW Zonnebuffer 60°C Gestart") |
| `description` | `string` | Volledige economische toelichting en motivatie |
| `target_temp_c`| `float` | Ingesteld setpoint ($^\circ\text{C}$) |
| `power_kw` | `float` | Betrokken vermogen ($kW$) |
| `savings_eur` | `float` | Geschatte besparing t.o.v. later stoken (€) |

---

## 📈 3. Grafana Query Cookbook (Copy-Paste Query Voorbeelden)

Met dit semantische datamodel kun je in Grafana direct de volgende InfluxQL queries gebruiken:

### 3.1 Real-Time Vermogensbalans (Stacked Graph)
```sql
-- Zonne-opwek (positief)
SELECT mean("solar_w") AS "Zonnepanelen" 
FROM "energy_telemetry" 
WHERE "device_id" = 'rooftop_solar' AND $timeFilter 
GROUP BY time($__interval) fill(linear)

-- Ongedefinieerd verbruik (gestapeld boven warmtepomp)
SELECT mean("unallocated_w") AS "Huishoudelijk Verbruik" 
FROM "energy_telemetry" 
WHERE "source" = 'canonical_accumulator' AND $timeFilter 
GROUP BY time($__interval) fill(linear)

-- Warmtepomp totaal vermogen
SELECT mean("heatpump_w") AS "Warmtepomp" 
FROM "energy_telemetry" 
WHERE "source" = 'canonical_accumulator' AND $timeFilter 
GROUP BY time($__interval) fill(linear)

-- Netto netafname / injectie
SELECT mean("p1_import_w") - mean("p1_export_w") AS "Netto Netafname" 
FROM "energy_telemetry" 
WHERE "source" = 'canonical_accumulator' AND $timeFilter 
GROUP BY time($__interval) fill(linear)
```

### 3.2 Boilervat Temperatuur & Thermisch Verloop
```sql
-- Werkelijke watertemperatuur
SELECT mean("temperature_c") AS "Boilervat Temperatuur" 
FROM "energy_telemetry" 
WHERE "device_id" = 'dhw_tank' AND $timeFilter 
GROUP BY time($__interval) fill(linear)

-- Buitentemperatuur referentie
SELECT mean("temperature_c") AS "Buitentemperatuur" 
FROM "energy_telemetry" 
WHERE "device_id" = 'outdoor_weather' AND $timeFilter 
GROUP BY time($__interval) fill(linear)
```

### 3.3 Grafana Native Annotaties Toevoegen
Configureer in Grafana Dashboard Settings ➔ **Annotations**:
* **Name:** `Open HEMS Events`
* **Data source:** `openhems` (InfluxDB)
* **Query:**
  ```sql
  SELECT title, description, state_code 
  FROM "hems_annotations" 
  WHERE $timeFilter
  ```
* **Resultaat:** Grafana toont automatisch gekleurde verticale lijnen en interactieve hover-cards bij elke warmtepomp-actie, zonnebuffer-fusie of spitsblokkade!

---

## 🔒 4. Validatie & Drift Guard

De integriteit van dit semantische datamodel is verankerd in de geautomatiseerde CI/CD gate:
- `tests/architecture/test_anti_drift_guards.py` bewaakt de entiteitsisolatie.
- `scripts/verify_data_integrity.py` controleert vóór elke build of alle SI-eenheden en matrix-vormen 100% conform deze specificatie zijn.
