# Hermes-plan — Ruimteverwarming: van heuristiek naar canonieke optimizer

**Doelversies:** 0.104.x (fase 1 en 2), 0.105.0 (fase 3, solver)
**Aangemaakt:** 2026-09-19 door Claude (Code-sessie), in opdracht van Matthijs
**Verwant plan:** `docs/plans/PLAN-dhw-comfortmarge-en-uitlegfixes.md` — WP1 (solver-interpolatie) en WP3 (doelfunctie) moeten daar eerst landen, anders kopieer je die bugs hierheen
**Speelregels:** `AGENTS.md` invarianten 1 t/m 6, `docs/adr/ADR-004-dhw-dispatch-optimization-problem.md`

---

## 0. Samenvatting voor de uitvoerder

Ruimteverwarming draait nog op een greedy toestandsmachine met vaste drempels. Dat is precies wat ADR-004 voor tapwater heeft afgeschaft. De inzet is groter: ruimteverwarming verbruikt per dag ruwweg zes keer zoveel stroom als tapwater, en de betonvloer is een grotere buffer dan het vat.

| Grootheid | Ruimteverwarming | Tapwater |
|---|---|---|
| Verbruik per dag, jaargemiddeld uit het geleerde profiel | 19,2 kWh_el | 3,1 kWh_el |
| Verschuifbare energie binnen de comfortband | 6,7 kWh_el | 4,1 kWh_el |
| Arbitragewinst per cyclus, prijzen 2026-09-19 | €0,93 | ca. €0,10 |

Drie fasen, in deze volgorde:

| Fase | WP | Wat | Grootte |
|---|---|---|---|
| 1 | CV0 | Nachtdal-bug: slot-indexering gaat uit van middernacht terwijl slot 0 "nu" is | S |
| 1 | CV1 | Kostenmodel: `calculate_slot_financials` in plaats van hardcoded zonnedrempels | M |
| 1 | CV2 | Restwaarde van de vloerbuffer aan het eind van de horizon | M |
| 1 | CV3 | COP-parameters uit het modelbestand in plaats van hardcoded Carnot-rendement | S |
| 1 | CV4 | Horizon naar 48 uur | S |
| 2 | CV5 | Modelvalidatie: het gebouwmodel heeft R² = 0,009 en is ongeschikt als optimizer-basis | L |
| 2 | CV6 | `evaluate_heating_plan_metrics`: één doelfunctie J voor heuristiek én optimizer | M |
| 3 | CV7 | `space_heating_optimizer.py`: DP-solver met schaduwdraaien | XL |
| 3 | CV8 | Omschakeling, uitleglaag, UI en API | L |

Fase 1 is een gedetailleerd executieplan, zie §2. Fase 2 en 3 staan op hoofdlijnen in §3 en §4, en worden pas uitgewerkt als fase 1 gemeten resultaat heeft.

**Harde volgorde-eis:** WP7 (de solver) start niet voordat WP5 groen is. Een optimizer op een model met R² = 0,009 zoekt het optimum van ruis.

---

## 1. Bevindingen die dit plan sturen

### 1.1 De huidige logica is een regelset, geen optimalisatie

`layer3_scheduling/space_heating_policy.py` (502 regels) beslist met vaste drempels:

| Regel | Drempel | Plek |
|---|---|---|
| Start bij comfortafwijking | `t_room <= target - 0,35` | regel ~312 |
| Zonoverschot | `solar_kw >= 1,5` | regel ~321 |
| Aanloop naar piek | 3,0 uur vast | regel ~226 |
| Terugblik voor zonvensters | 6,0 uur vast | regel ~233 |
| Zon- of prijsdrempel in dat venster | `solar >= 1,0` of `kosten <= 0,05` | regel ~237 |
| Nachtdal-marge | `<= min_night_cost + 0,015` | regel ~245 |
| Minimale runlengte | 8 slots = 2 uur | regel ~57 |

Er is geen doelfunctie die deze tegen elkaar afweegt, geen kostenvergelijking tussen alternatieve plannen en geen waardering van de eindtoestand.

### 1.2 Bevestigde bug: nachtdal-detectie kijkt naar het verkeerde tijdvak

Regels 240–246:

```python
min_night_cost = min([thermal_costs[idx] for idx in range(min(n_slots, 24))] or [0.06])
for idx in range(min(n_slots, 24)):
    slot_in_day = idx % 96
    if 8 <= slot_in_day <= 22:  # 02:00 to 05:30
```

`idx` is de index vanaf **nu**, niet vanaf middernacht. De sanitizer labelt slot 0 als `Nu (HH:MM)` (`layer1_data_collection/sanitizer.py:271`). Live gecontroleerd op 2026-09-19 om 15:00: `labels[0..5] = ['14:00', '14:15', '14:30', '14:45', 'Nu (15:00)', '15:15']`. Slot 8 tot 22 was op dat moment dus ongeveer 17:00 tot 20:30, de avondspits, niet het nachtdal. Omdat de planner elke minuut herplant, verandert het bedoelde tijdvak met de klok mee. Zie WP-CV0.

### 1.3 Het gebouwmodel is niet gekalibreerd

`/config/heatpump_model_parameters.json`, blok `metrics`, getraind op 2026-09-17 over 11.514 monsters:

| Metriek | Waarde | Betekenis |
|---|---|---|
| R² | 0,009 | vrijwel geen verklaarde variantie |
| RMSE | 259,7 W | |
| MAE | 228,1 W | |
| MAPE | 26,2 % | |

De 2R1C-constanten `C_AIR = 3,2`, `C_FLOOR = 14,5` en `R_FLOOR_AIR = 0,08` staan hardcoded in de klasse en worden door niets gekalibreerd; alleen `ua_base_w_per_k` komt uit het bestand. `api/routes_model.py:54` gebruikt bovendien een derde waarde, 321,1 W/K, terwijl het bestand 318,5 zegt en de klasseconstante 321. Zie WP-CV5.

### 1.4 De toestand is reduceerbaar, maar dat moet gemeten worden

Met de huidige constanten:

| Tijdsconstante | Waarde | In slots van 15 min |
|---|---|---|
| Kamerlucht, `C_air / (1/R + UA)` | 15 min | 1,0 |
| Vloer, `C_floor × R` | 70 min | 4,6 |

De scheiding is een factor 4,6, dus de kamer is op kwartierbasis bijna quasi-statisch:

```
T_kamer = (T_vloer/R + q_zon + UA·T_buiten) / (1/R + UA)
```

Na één slot is de kamer echter pas voor 63 % geconvergeerd, dus de benadering introduceert tot ongeveer 0,3 K fout bij snelle vloerveranderingen. Dat is veel op een comfortband van 1,8 K. De reductie is daarom een **hypothese die WP5 moet bevestigen**, geen aanname waarop WP7 mag bouwen.

### 1.5 Rekenlast van een DP-solver

Geschat tegen de gemeten 59 ms van de tapwater-solver (0,25 M evaluaties):

| Variant | Toestanden per slot | Evaluaties | Geschatte tijd |
|---|---|---|---|
| 2D: kamer 0,05 K × vloer 0,25 K × runstate | 20.000 | 19,2 M | 4,6 s |
| 2D grof: kamer 0,05 K × vloer 0,5 K × runstate | 8.640 | 8,3 M | 2,0 s |
| 1D gereduceerd: vloer 0,05 K × runstate | 2.000 | 1,9 M | 0,46 s |

De planner herplant nu elke 60 seconden (`api/context.py:93` e.v.). Zelfs de 2D-variant past daarbinnen, mits de solve buiten de HTTP-request draait. Prestatie is dus geen reden om meteen naar 1D te grijpen.

---

## 2. Fase 1 — Executieplan voor de vier bouwstenen plus de bug

Deze fase raakt de beslisstructuur niet. Elke stap is los te reviewen, te testen en terug te draaien. Samen leveren ze betere beslissingen én de meetlat die fase 2 en 3 nodig hebben.

### WP-CV0 — Nachtdal-detectie op echte kloktijd

**Bestand:** `layer3_scheduling/space_heating_policy.py`, regels 240–246.

1. Voeg aan `plan_space_heating` een parameter `slot_datetimes: Optional[List[datetime]] = None` toe. `central_planner.py` (~regel 282) geeft `[s.dt for s in slots]` mee; de slots dragen dat veld al.
2. Vervang de index-heuristiek door een kloktijd-test op elk slot binnen de eerste 24 uur:

```python
for idx, s_dt in enumerate(slot_datetimes[:min(n_slots, 96)]):
    if 2 <= s_dt.hour < 6 and thermal_costs[idx] <= min_night_cost + p.night_valley_margin:
        preheat_candidate_slots.add(idx)
```

3. `min_night_cost` berekenen over precies die nachtslots, niet over de eerste 24 indices.
4. Valt `slot_datetimes` weg (legacy-aanroep), log dan `[WARN]` en sla het nachtdal-blok over. Niet terugvallen op de oude index-aanname.
5. De vensters 2–6 uur en de marge 0,015 verhuizen naar de configsectie uit WP-CV1 §5, zodat er geen nieuwe magische getallen bij komen.

**Tests** in `tests/unit/test_space_heating_policy.py`:

- `test_night_valley_uses_wall_clock_not_slot_index`: dezelfde prijsreeks, één keer gepland vanaf 00:00 en één keer vanaf 15:00. De set nachtdal-kandidaten moet in beide gevallen dezelfde kloktijden bevatten.
- `test_night_valley_ignored_without_datetimes`: zonder `slot_datetimes` geen nachtdal-kandidaten en een waarschuwing.

**Verificatie:** `curl .../api/model/heating-forecast?resolution=15m` op twee tijdstippen van de dag; de gemarkeerde voorverwarmingsvensters moeten op dezelfde kloktijden vallen.

---

### WP-CV1 — Eén kostenmodel voor het hele systeem

**Probleem.** `calculate_thermal_cost` (regels 117–135) waardeert zonnestroom met twee vaste drempels: bij 1,2 kW zon rekent hij €0,075/kWh, bij 0,5 kW €0,12. Die getallen staan los van het werkelijke terugleverbedrag, negeren de sluiplast en verschillen van wat de tapwaterketen gebruikt. Invariant 1 vraagt één bron van waarheid.

**Aanpak.**

1. Vervang de interne prijslogica door `layer3_scheduling.dhw_financials.calculate_slot_financials`, dezelfde functie die de tapwater-solver gebruikt. Die verdeelt het verbruik over eigen zon en netafname en waardeert eigen zon tegen de afgeleide terugleverwaarde via `TariffProvider`.
2. Hernoem de module-rol, niet de functie: geef `dhw_financials.py` een neutrale alias `layer3_scheduling/slot_financials.py` die dezelfde functie exporteert, en laat beide domeinen daaruit importeren. Geen tweede implementatie.
3. Nieuwe signatuur:

```python
def calculate_thermal_cost(cls, price_all_in, cop, solar_kw, unalloc_kw,
                           el_demand_kw, step_hours, tariff_provider) -> tuple[float, float]:
    """Retourneert (kosten_eur_slot, effectieve_prijs_per_kwh_th)."""
```

   De kosten per kWh thermisch worden `p_effectief / COP`, met `p_effectief` uit `calculate_slot_financials`.
4. `plan_space_heating` moet `unallocated_kw` per slot krijgen. `central_planner.py` geeft dat mee als `[s.unallocated_kw for s in slots]`, net als bij tapwater.
5. `TariffProvider` doorgeven vanaf de planner; niet binnen de policy instantiëren.
6. De aanroep op regels 216–219 werkt nu per slot met het werkelijke modulatievermogen. Omdat dat vermogen pas in de simulatielus bekend is, berekent stap 2 een **referentiekostenreeks** op nominaal vermogen voor de vensterselectie, en berekent de lus daarna de **werkelijke** kosten per slot. Leg dat onderscheid vast in de docstring.

**Tests:**

- `test_thermal_cost_uses_export_value_not_fixed_threshold`: bij 2 kW zonoverschot en een all-in prijs van €0,30 moeten de kosten overeenkomen met de terugleverwaarde uit `TariffProvider`, niet met €0,075.
- `test_thermal_cost_matches_dhw_financials`: dezelfde invoer door beide domeinen geeft dezelfde effectieve prijs.
- `test_thermal_cost_accounts_for_baseload`: met een sluiplast boven de zonproductie is er geen eigen zon en geldt de netprijs.

**Verificatie:** in `/api/model/heating-forecast` moet `costs_eur` op een zonnige middag dalen zonder dat het vermogen verandert, en moet de som overeenkomen met de kosten die het dispatch-overzicht voor dezelfde slots rapporteert.

---

### WP-CV2 — Restwaarde van de vloerbuffer

**Probleem.** De planning stopt aan het eind van de horizon zonder de opgeslagen warmte te waarderen. Warmte die na het laatste slot nog in de vloer zit, telt als verspild. Daardoor is voorverwarmen aan het eind van de horizon altijd verliesgevend en vertoont de planning een systematische rem naar het einde toe.

**Aanpak.** Neem het patroon van de tapwater-solver over (`dhw_optimizer.py` regels 305–318):

1. Bepaal `p_hat` als het gemiddelde van de goedkoopste 20 % effectieve prijzen over de horizon.
2. Bepaal `COP_hat` met `calculate_carnot_cop` op de gemiddelde buitentemperatuur en de CV-aanvoertemperatuur.
3. Restwaarde per kelvin vloertemperatuur: `C_FLOOR × p_hat / COP_hat`. Met `C_FLOOR = 14,5` en de prijzen van 2026-09-19 is dat ongeveer €0,40 per kelvin, tegenover €0,016 per kelvin voor het tapwatervat. De vloer is dus veruit de waardevolste buffer in huis.
4. Referentiepunt is de ondergrens van de comfortband, niet nul: `restwaarde = C_FLOOR × (T_vloer_eind − T_vloer_bij_min_comfort) × p_hat / COP_hat`, afgekapt op nul.
5. In fase 1 verandert dit de beslissingen nog niet. Het getal wordt berekend, in `SpaceHeatingPlanSummary` opgenomen als `terminal_value_eur` en in de doelfunctie van WP-CV6 gebruikt. Zo is de meetlat compleet voordat de solver er gebruik van maakt.

**Tests:**

- `test_terminal_value_positive_when_floor_charged`: vloer 2 K boven de ondergrens geeft een restwaarde binnen 5 % van de handberekening.
- `test_terminal_value_zero_at_comfort_floor`: vloer op de ondergrens geeft nul.
- `test_terminal_value_scales_with_cheap_price_percentile`: halvering van de goedkoopste prijzen halveert de restwaarde.

---

### WP-CV3 — COP uit het modelbestand

**Probleem.** `CARNOT_EFFICIENCY = 0,42` staat hardcoded op regel 53, terwijl `/config/heatpump_model_parameters.json` onder `heat_pump` al `carnot_efficiency: 0,48` bevat. De tapwaterketen heeft in v0.103.52 net de omgekeerde weg afgelegd en haalt zijn COP-parameters wél uit dat bestand. Ook `defrost_threshold_c`, `defrost_cop_penalty` en `flow_temp_cv_c` staan in het bestand en worden genegeerd.

**Aanpak.**

1. Breid `get_active_parameters` (regels 61–83) uit met `carnot_efficiency`, `flow_temp_cv_c`, `defrost_threshold_c`, `defrost_cop_penalty`, `min_cop` en `max_cop`, elk met de huidige klasseconstante als terugval en met bereikvalidatie.
2. `calculate_carnot_cop` geeft die waarden door aan `models.physics.calculate_carnot_cop`. Let op de afwijkende standaardwaarden daar: `carnot_efficiency = 0,48`, `min_cop = 2,2`, `max_cop = 6,8`. Na deze wijziging komt alles uit één bron.
3. Ruim `api/routes_model.py:54` op: die gebruikt 321,1 W/K terwijl het bestand 318,5 zegt. Laat de route de waarde uit `get_active_parameters` gebruiken.
4. Vervang de tekstuele modulatiecurve `"2840 - 92·T"` in het parameterbestand door een genest blok met numerieke velden. De huidige stringparser (regels 74–80) splitst op een koppelteken en breekt bij een negatieve intercept. Behoud het lezen van de oude notatie één release lang, met een deprecatiewaarschuwing.

**Tests:**

- `test_carnot_efficiency_loaded_from_model_parameters`
- `test_modulation_curve_parsed_from_numeric_block`
- `test_modulation_curve_legacy_string_still_parsed`
- Architectuurguardrail `test_no_hardcoded_ua_outside_policy`: grep over `api/` op de constanten 321,1 en 0,321.

---

### WP-CV4 — Horizon naar 48 uur

**Probleem.** De docstring belooft 24 uur en de vensterselectie kijkt maximaal 6 uur vooruit, terwijl de planner al 192 slots aanlevert en de tapwater-solver 48 uur gebruikt. Bij een koude periode met een dure dag na een goedkope nacht is 24 uur te kort om de vloer op het juiste moment te laden.

**Aanpak.**

1. `n_slots` volgt de aangeleverde lijst; dat werkt al. Haal de resterende afkappingen op 24 en 96 weg (regels 241–242 vallen samen met WP-CV0).
2. Prijzen voor dag twee zijn pas na ongeveer 13:00 bekend. Ontbrekende slots aan het eind moeten expliciet gemarkeerd worden. Neem het patroon van de dag-vooruitcache over: markeer die slots met de kwaliteitsvlag `INTERPOLATED` en laat de vensterselectie ze overslaan, in plaats van ze als goedkoop te behandelen. Invariant 3 verbiedt verzonnen waarden.
3. De restwaarde uit WP-CV2 schuift mee naar het nieuwe eindpunt. Bij een langere horizon wordt de invloed van de eindwaarde kleiner, wat het plan stabieler maakt.
4. Documenteer in de docstring welke horizon welk deel van de logica gebruikt.

**Tests:**

- `test_heating_plan_handles_192_slots`
- `test_unknown_future_prices_are_not_selected_as_cheap`
- `test_horizon_48h_matches_24h_for_first_96_slots_when_prices_flat`: bij een vlakke prijsreeks mag de langere horizon de eerste dag niet veranderen.

**Verificatie:** `curl .../api/model/heating-forecast?horizon=48h` geeft 192 toekomstige slots, en de kandidaatvensters op dag twee verschijnen pas als de prijzen voor die dag binnen zijn.

---

### Afronding fase 1

- `VERSION`, `config.yaml` en `CHANGELOG.md` bijwerken.
- Goldens draaien. WP-CV0 tot en met WP-CV4 mogen de plannen veranderen; verklaar elk verschil in de commit-body voordat je de snapshots vernieuwt.
- Bouwen met `rsync` naar `/addons/open-hems` gevolgd door `ha apps rebuild local_open_hems`. Een herstart pakt geen nieuwe code op.
- Meet een week lang met `evaluate_heating_plan_metrics` uit WP-CV6 en leg de uitkomst vast. Dat is de nulmeting waartegen de solver zich later moet bewijzen.

---

## 3. Fase 2 — Meetbaar maken

### WP-CV5 — Modelvalidatie en herkalibratie

Voorwaarde voor de solver. Met R² = 0,009 verklaart het huidige gebouwmodel vrijwel niets van de gemeten variantie.

1. Diagnose eerst: zijn de metrieken op het juiste doel berekend, is de steekproef vervuild met zomerslots of met tapwaterruns, en wordt het verwarmingsvermogen wel correct toegerekend?
2. Kalibreer `UA`, `C_AIR`, `C_FLOOR` en `R_FLOOR_AIR` gezamenlijk op gemeten binnentemperatuur, buitentemperatuur, zoninstraling, wind en verwarmingsvermogen. Nu is alleen `UA` kalibreerbaar en staan de drie andere hardcoded.
3. Aanvaardingsdrempel: R² boven 0,6 op een uitgehouden testperiode, of een gedocumenteerde verklaring waarom dat niet haalbaar is. Zonder dat gaat WP-CV7 niet door.
4. Valideer de quasi-statische reductie uit §1.4 op echte data: vergelijk de voorspelde kamertemperatuur volgens de algebraïsche relatie met de gemeten waarde. De uitkomst bepaalt of de solver 1D of 2D wordt.
5. Voeg een runtime-waarschuwing toe in `layer2_calibration/model_validator.py` wanneer voorspelling en meting meer dan 25 % uiteenlopen, zoals WP6 in het tapwaterplan dat voorschrijft.

### WP-CV6 — Eén doelfunctie voor beide planners

Spiegel `evaluate_plan_metrics` uit `dhw_optimizer.py` (regels ~706–880) naar `evaluate_heating_plan_metrics`. Die evalueert elk willekeurig vermogensprofiel met dezelfde fysica en dezelfde kosten:

```
J = Σ_k kosten(u_k)  +  c_start × aantal starts  −  restwaarde(T_vloer_eind)
```

met een straf voor overschrijding van de comfortband. Geeft terug: `j_objective_eur`, `electricity_cost_eur`, `start_cost_eur`, `terminal_value_eur`, `comfort_violation_kh`, `total_kwh_el`. Dit is de meetlat voor de nulmeting van fase 1 en voor het schaduwdraaien in fase 3.

---

## 4. Fase 3 — De solver

### WP-CV7 — `layer3_scheduling/space_heating_optimizer.py`

**Toestand.** Vloertemperatuur, kamertemperatuur en een discrete draaitoestand voor de minimale looptijd. Of de kamertemperatuur wegvalt, beslist WP-CV5 §4.

**Besturing.** Het modulatievermogen is continu tussen 0,85 en 4,2 kW. Discretiseer in vijf niveaus: uit, minimum, een derde, twee derde en maximum. Controleer bij oplevering of een fijnere verdeling de doelfunctie meetbaar verbetert.

**Dynamica.** De 2R1C-vergelijkingen zijn lineair. Integreer per slot exact via de analytische oplossing van het 2×2-stelsel in plaats van met een Euler-stap. Dat verwijdert de discretisatiefout die bij een kamertijdconstante van één slot juist het grootst is. Zet die stap als pure functie in `models/physics.py`, naast `dhw_step`.

**Randvoorwaarden.** Comfortband van ondergrens tot voorverwarmingsplafond, vloerplafond van 28 °C, harde piekblokkades, de hydraulische vergrendeling tijdens tapwaterruns en de minimale looptijd.

**Kritieke overname uit het tapwaterplan.** Interpoleer nooit lineair over oneindige waarden in de waardefunctie. Dat is WP1 uit het tapwaterplan; in twee dimensies stapelt die fout twee keer zo snel op. Gebruik dezelfde helper.

**Schaduwdraaien.** Neem het patroon van `dhw_shadow_logger.py` over. Draai de optimizer een aantal weken naast de bestaande heuristiek, evalueer beide met `evaluate_heating_plan_metrics` en log het verschil naar de beslisaudit. Schakel pas om als de optimizer aantoonbaar wint zonder de comfortband vaker te schenden.

### WP-CV8 — Omschakeling en presentatie

Uitleglaag in de stijl van `dhw_optimizer_explain.py`, met contrafeitelijke vergelijkingen die op de volledige doelfunctie rekenen en niet op stroomkosten alleen. Zie WP3 in het tapwaterplan voor de valkuilen: geen weggeklemde negatieve verschillen, en geen comfortgrens noemen die niet de bindende grens is. Verder: het schaduwveld in de beslisaudit, de grafiek met voorverwarmingsvensters en effectieve grens, bijwerken van `docs/openapi.json`, `docs/openapi.yaml` en `mcp_server.py` in lockstep, een nieuwe ADR-005 voor het ruimteverwarmingsprobleem, en vertalingen in `locales/nl.json` en `locales/en.json`.

---

## 5. Configuratie

Alle drempels uit §1.1 verhuizen naar een nieuwe sectie in `heatpump_config.json`, analoog aan `dhw_optimizer.comfort_margin` uit het tapwaterplan:

```json
"space_heating_optimizer": {
  "comfort_band": { "min_delta_c": 0.6, "max_preheat_c": 1.2 },
  "night_valley": { "start_hour": 2, "end_hour": 6, "margin_eur_kwh_th": 0.015 },
  "preheat": { "runway_hours": 3.0, "lookback_hours": 6.0, "solar_trigger_kw": 1.5 },
  "run_limits": { "min_run_slots": 8, "startup_boost_slots": 2 },
  "terminal_cheap_percentile": 0.20
}
```

Alle standaardwaarden gelijk aan het huidige gedrag, zodat fase 1 de plannen niet verandert door de configuratie zelf. Validatie en afkapping met een waarschuwing bij ongeldige waarden, en een klassemethode `from_config` zoals `DhwOptimizerParams.from_config`. De minimale looptijd blijft in de integratielaag begrensd door wat de warmtepomp aankan; de configuratie mag die ondergrens niet onderschrijden.

---

## 6. Definition of done fase 1

- [ ] Invarianten 1 tot en met 6 nagelopen.
- [ ] `PYTHONPATH=. pytest tests/` groen, inclusief de nieuwe tests uit WP-CV0 tot en met WP-CV4.
- [ ] Goldens alleen vernieuwd met een verklaring per verschil.
- [ ] Geen hardcoded UA-, COP- of prijsdrempels meer buiten `get_active_parameters` en de configuratie.
- [ ] Eén kostenmodel voor tapwater en ruimteverwarming, aantoonbaar via `test_thermal_cost_matches_dhw_financials`.
- [ ] Nulmeting met `evaluate_heating_plan_metrics` over minstens zeven dagen vastgelegd in de CHANGELOG.
- [ ] Live geverifieerd na `ha apps rebuild`: voorverwarmingsvensters op dezelfde kloktijden ongeacht het planmoment, kosten die dalen bij zonoverschot, en 192 slots bij een horizon van 48 uur.
