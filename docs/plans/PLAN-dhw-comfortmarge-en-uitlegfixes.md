# Hermes-plan — DHW comfortmarge configureerbaar + solver-, uitleg- en grafiekfixes

**Doelversie:** 0.103.52 (patch; als de repo al hoger staat: volgende patch)
**Bijgewerkt:** 2026-09-19 — WP6 toegevoegd (boilerspec 3,0 kW uit config + COP-herkalibratie) op aanwijzing van Matthijs
**Aangemaakt:** 2026-09-19 door Claude (Code-sessie), in opdracht van Matthijs
**Referentiedata:** `docs/plans/data/dhw_snapshot_2026-09-19.json` (live snapshot van prijzen, tapprofiel, traject en optimizer-output)
**Speelregels:** `AGENTS.md` (invarianten 1 t/m 6), `docs/adr/ADR-004-dhw-dispatch-optimization-problem.md`

---

## 0. Samenvatting voor de uitvoerder

Zes werkpakketten. Uitvoeringsvolgorde: **WP1 → WP6 → WP2 → WP3 → WP4 → WP5**. Goldens één keer regenereren, na WP1 + WP6 samen.

| WP | Wat | Waarom | Grootte |
|---|---|---|---|
| WP1 | Solver: geen lineaire interpolatie over oneindige V-waarden | Comfortgrens kruipt 0,25°C per geblokkeerd kwartier omhoog; veroorzaakt de onjuiste tekst "noodzakelijk om comfort te behouden" en te vroeg stoken vóór blokkades | M |
| WP2 | `demand_kwh_th` in optimizer-traject | Grafiek toont geen tapvraag-balken (API geeft overal 0,0) | S |
| WP3 | Kostenvergelijking op volledige doelfunctie J + eerlijke uitlegteksten | Gerapporteerde totalen en "bespaart €0,50" negeren startkosten en restwarmte en vergelijken plannen met verschillende eindtemperatuur | M |
| WP4 | Comfortmarge configureerbaar (p95 / p50 / vast) via config, API, UI, docs | Matthijs wil richting P50; marge kostte zaterdag 19-9 ca. €0,10 | L |
| WP5 | (optioneel, laag) Tekst "zakt naar 15,7°C om 00:00" | Minimum over 48 uur op de 15°C-vloer is misleidend | S |
| WP6 | Boilerspec uit config (compressor 3,0 kW) + COP-herkalibratie + hardcoded 1,8/2,4 kW opruimen | Optimizer rekent met 1,8 kW terwijl de warmtepomp ~3 kW trekt: kWh_el en € per run zijn een factor 1,67 te laag | M |

Definition of done staat in §7. Bouw en live-verificatie in §8.

---

## 1. Aanleiding en bewijs (zaterdag 2026-09-19, 00:20)

Het plan van de optimizer:

- Run 1: 03:15–04:00 tot 54,2°C, 1,35 kWh_el, €0,24 (all-in €0,139–0,150/kWh)
- Run 2: 14:30–15:15 tot 58,7°C, 1,50 kWh_el, €0,17
- Tekst: "Nu starten (03:15) is noodzakelijk om comfort te behouden (tank zou anders onder 40,0°C zakken)."

Feiten uit de snapshot:

1. Overdag is stroom goedkoper dan 's nachts: 09:50–16:20 kost €0,109–0,111/kWh all-in, 03:20–03:50 kost €0,139–0,150.
2. Ongestookt zakt het vat pas om ~10:35 onder 40,5°C. De solver rekent echter met grens `40 + m_k`, waarbij `m_k = Σ(P95−P50)/C` over 8 uur en `P95 = 1,5 × P50`. Zaterdagochtend is `m_k ≈ 2,4–2,7 K`, dus effectieve grens ≈ 42,5°C, ongestookt bereikt om **07:35**. Vóór 07:35 zijn 03:20–03:50 de goedkoopste kwartieren. Binnen de huidige regels is 03:15 correct.
3. Simulatie met dezelfde fysica en prijzen (zie snapshot `derived_analysis`): P50-variant (stoken ~10:20) is ~€0,10/dag goedkoper (netto na restwaarde €0,01 vs €0,12), 30% lager per kWh_th. Bij een P95-ochtend (1,5× tappen) zakt het vat dan om 09:20 naar 39,0°C.
4. De uitstel-counterfactual (stoken verboden t/m 04:00) meldt "onhaalbaar", terwijl een run om 06:50–07:35 in de simulatie gewoon haalbaar is (kost ~€0,03 meer dan 03:15). Oorzaak: zie WP1.
5. `demand_kwh_th` in `/api/model/dhw-status` is 197× 0,0; het model rekent wél met 4,73 kWh_th voor zaterdag.
6. `DhwOptimizerResult.total_cost_eur` telt alleen stroom (regel ~651 `total_cost_eur += c_slot`), geen startkosten en geen restwaarde. De counterfactuals (`cap50`, `delay`) vergelijken op dit getal en clampen negatieve verschillen weg (`max(0.0, cost_diff)`). "Bespaart €0,50 t.o.v. 50°C" op een totaal van €0,50 is niet geloofwaardig.
7. `dhw_boiler.compressor_power_kw = 3.0` in de site-config wordt nergens ingelezen; `DhwTankSpec.from_config` heeft geen aanroepers. De optimizer rekent met de codedefaults 1,8 kW (≤52°C) en 2,4 kW (>52°C). Matthijs: de warmtepomp trekt ~3 kW tijdens tapwaterruns. Gevolg: kWh_el en € per run zijn ×1,67 te laag (run 1 toont 1,35 kWh_el/€0,24; werkelijk ≈ 2,25 kWh_el/≈€0,38). De temperatuurvoorspellingen kloppen wél, want 1,8 kW × COP 2,85 ≈ 5,1 kW_th en de gemeten opwarming uit de 7-daagse historie is 12–15 K/uur ≈ 5,3–6,1 kW_th. Bij 3,0 kW elektrisch hoort dus een COP van ≈ 1,8–2,0 bij 50°C (de calibrator hanteert al `dhw_average_cop = 2.02`). Zie WP6.

---

## 2. WP1 — Solver: interpolatie over oneindige waarden (root cause, eerst reproduceren)

### 2.1 Mechanisme

In `layer3_scheduling/dhw_optimizer.py`, stap 8b (regels ~428–460) en de forward pass (regels ~519 en ~539), wordt `V_{k+1}(T')` bepaald met `np.interp(t_next, T_grid, v_next_col, left=np.inf, ...)`. Roosterpunten onder de comfortgrens hebben `V = inf`. NumPy's `interp` levert voor elk punt **tussen** een oneindig en een eindig roosterpunt `+inf` op (de NaN-fallback in `arr_interp` valt terug op de andere zijde, die `+inf` geeft). Gevolg: de haalbare ondergrens springt naar het eerstvolgende roosterpunt (0,25°C).

Dat is op zich hooguit 0,25°C conservatief, maar in een reeks kwartieren waarin `u=1` verboden is (harde blokkade, `OFF_DWELL`, of de counterfactual-lockout) stapelt het op: bij elke stap terug moet `T − δ_k ≥ T_grid[L_{k+1}]` met `δ_k > 0` (stilstand + tap), dus `L_k ≥ L_{k+1} + 1`. De ondergrens kruipt **0,25°C per geblokkeerd kwartier** omhoog, terwijl de echte afkoeling ~0,04°C per kwartier is.

- Uitstel-counterfactual: 15 geblokkeerde slots → +3,75°C → `T0 = 44,1` wordt "onhaalbaar" → tekst "noodzakelijk om comfort te behouden". Klopt niet.
- Normale solve: vóór de harde avondblokkade (zo 19:00–21:30, 10 slots) eist de solver ~2,5°C extra buffer; ook elke dwell-periode (3 slots) kost 0,75°C. De optimizer stookt daardoor structureel te vroeg en te hoog vóór blokkades.

### 2.2 Fix

1. Interpoleer uitsluitend over eindige waarden. Per `r'`: `fin = np.isfinite(v_next_col)`; als `fin.any()`: `np.interp(t_next[mask], T_grid[fin], v_next_col[fin], left=v_next_col[fin][0], right=v_next_col[fin][-1])`, waarbij `mask = t_next >= t_min_feas_next − 1e-4` (de continue comfortcheck blijft de enige haalbaarheidstoets). Onder de continue grens blijft het `inf`.
   - Toelichting: voor `T'` tussen de continue grens en het eerste eindige roosterpunt gebruik je de waarde van dat roosterpunt (clamp). Fout hooguit één roosterstap in de kosten, geen opstapeling in haalbaarheid.
2. Zelfde aanpassing in de forward pass op de twee `np.interp`-aanroepen (regels ~519 en ~539); maak er één helper `_interp_finite(t, T_grid, v_col)` van die op beide plekken wordt gebruikt.
3. Voeg een defensieve guard toe: na de Bellman-update `assert not np.isnan(V[k]).any()` (of vervang NaN door `inf` met een logregel). Er mag nooit NaN in `V` staan.

### 2.3 Tests (nieuw in `tests/unit/test_dhw_optimizer.py` of nieuw bestand `test_dhw_optimizer_feasibility.py`)

- `test_locked_coast_does_not_inflate_comfort_boundary`: 16 slots hard lockout vanaf k=0, `T0 = grens + 1,0 K`, geen tap, stilstand 2,38 W/K → verwacht: `validation_issue is None`, eerste run start direct na de lockout (of later als dat goedkoper is), `min(trajectory) ≥ t_comf + margin − 0,05`.
- `test_delay_counterfactual_feasible_when_physics_allows`: snapshot-fixture (zie §6) → `compute_counterfactual_delay(...)["is_comfort_forced"] is False` en `cost_diff_eur` tussen +€0,00 en +€0,10.
- `test_no_nan_in_value_function`: solve met hoog `min_dwell_slots` (8) en meerdere lockouts; via een debug-hook of door `evaluate_plan_metrics` op het resultaat: alle kosten eindig.
- Bestaande tests (`test_dhw_optimizer_behaviors.py`, `test_dhw_optimizer_adapter.py`) moeten groen blijven.

### 2.4 Golden replay

`tests/unit/test_replay_harness.py` gaat vrijwel zeker verschillen tonen (later/lager stoken vóór blokkades). Werkwijze: draai eerst, leg per golden het verschil uit in de commit-body (welke run verschuift, hoeveel graden lager, kostenverschil), en regenereer daarna pas de snapshots. Ongeverklaarde verschillen zijn een blocker.

---

## 3. WP2 — Tapvraag in het traject

- `dhw_optimizer.solve()` retourneert `trajectory={"temperatures_c", "temperatures_p05_c", "temperatures_p95_c"}` (regel ~695). Voeg toe: `"demand_kwh_th": [round(q, 4) for q in q_tap_p50]` en `"demand_p95_kwh_th"` (zelfde lengte als `temperatures_c` minus 1, of pad met 0,0 tot gelijke lengte; kies één conventie en documenteer die in `docs/SEMANTIC_DATA_MODEL.md`).
- `layer3_scheduling/dhw_plan_adapter.py` regel ~156 geeft `opt_result.trajectory` door; niets extra nodig als de sleutel erin zit.
- `api/routes_model.py` regel ~248 leest `raw_dem = traj.get("demand_kwh_th", [])`; de uur-aggregatie (regels ~296–330) moet `h_demand` als som van 4 kwartieren vullen. Controleer dat de history-padding (regel ~412) de lengtes gelijk houdt.
- Verificatie: `/api/model/dhw-status?resolution=15m` → `sum(demand_kwh_th)` over de eerste 96 planslots ≈ dagtotaal van het profiel (zaterdag 4,73 kWh_th ± wat over de daggrens valt). Headless-browser screenshot: blauwe balken zichtbaar (invariant 6).
- Test: `test_dhw_status_trajectory_contains_demand` (contracttest op de adapter-output: lengte en som > 0 bij een profiel met vraag).

---

## 4. WP3 — Kosten eerlijk vergelijken en eerlijk uitleggen

### 4.1 Doelfunctie als enige vergelijkingsmaat

De DP minimaliseert `J = Σ stroomkosten + c_start × starts − restwaarde(T_N)`. Alle vergelijkingen in de uitleglaag moeten op `J` gebeuren, niet op stroom alleen.

- `DhwOptimizerResult` uitbreiden met `electricity_cost_eur`, `start_cost_eur`, `salvage_value_eur`, `j_objective_eur`. Bereken ze in de forward pass (restwaarde = `terminal_val_per_kelvin × max(0, T_N − t_comf)`, exact zoals in `evaluate_plan_metrics`).
- `total_cost_eur` krijgt de betekenis **stroom + startkosten** (nu: alleen stroom). Dit is een semantische wijziging: vermeld in CHANGELOG en pas `cost_total_eur` in `dhw_plan_adapter.py` (regel ~155) en de UI-kaart "Kosten & Besparing" aan. Voeg `net_objective_eur` toe aan `decision_details`.
- `compute_counterfactual_cap50` en `compute_counterfactual_delay` in `dhw_optimizer_explain.py`: `cost_diff = res_cf.j_objective_eur − opt_result.j_objective_eur`. Verwijder de clamp `max(0.0, cost_diff)`; een negatief verschil betekent dat de solver niet optimaal was → `print("[WARN] ...")` + veld `solver_suboptimal: true`. Test: `cost_diff ≥ −0,005` na WP1.
- Tests in `tests/unit/test_dhw_optimizer_adapter.py` regels ~59–78 vergelijken nu `cost_*_eur ≥ total_cost_eur`; herschrijf naar `j_objective`.

### 4.2 Teksten (Nederlands, één zin per feit, cijfers uit het resultaat)

- Uitstel, haalbaar en duurder: `Nu starten ({start}) i.p.v. wachten tot {alt_start} bespaart netto €{diff} (incl. startkosten en restwarmte).`
- Uitstel, echt onhaalbaar: `Uiterlijk om {deadline} starten: zonder stoken zakt het vat om {deadline} onder de comfortgrens van {grens}°C ({t_comf}°C + {marge} K marge).` Hiervoor levert `solve()` `comfort_deadline_idx` en `binding_margin_c` mee (eerste slot waar het ongestookte traject `t_comf + m_k` kruist). De huidige zin "tank zou anders onder 40,0°C zakken" mag niet meer voorkomen wanneer de bindende grens ≠ 40,0.
- Cap-50: `Doorstoken tot {t_end}°C i.p.v. aftoppen op 50°C bespaart netto €{diff} (één start minder, {n} kWh_th meer stilstandsverlies).` Alleen tonen als `diff ≥ 0,01`.
- Comfortkaart: laat het 48-uurs minimum weg (zie WP5) en noem het eerste kruispunt onder de **effectieve** grens: `Zonder stoken zakt het vat om {t_cross} onder {grens}°C.`
- Financiële kaart: `Plan 48u: stroom €{el}, starts €{st}, restwarmte −€{sv} → netto €{j}.`

Locale-sleutels in `locales/nl.json` en `locales/en.json` (test `tests/unit/test_i18n.py`).

---

## 5. WP4 — Comfortmarge configureerbaar

### 5.1 Configuratie (site-config `heatpump_config.json`, pad uit add-on optie `site_config`)

Nieuwe top-level sectie `dhw_optimizer` (toekomstige solver-parameters horen hier ook):

```json
"dhw_optimizer": {
  "comfort_margin": {
    "mode": "p95",
    "tap_stress_factor": 1.5,
    "horizon_hours": 8,
    "min_margin_c": 0.5,
    "fixed_margin_c": 2.0
  }
}
```

| Sleutel | Type / bereik | Default | Betekenis |
|---|---|---|---|
| `mode` | `"p95"` \| `"p50"` \| `"fixed"` | `"p95"` | p95: `m_k = max(min_margin_c, Σ_{k..k+H}(P95−P50)/C)`; p50: `m_k = min_margin_c`; fixed: `m_k = fixed_margin_c` |
| `tap_stress_factor` | float 1,0–3,0 | 1,5 | `P95 = factor × P50` (mode p95; ook voor de P95-band in de grafiek) |
| `horizon_hours` | float 1–24 | 8 | venster H voor de P95-som (`H_slots = round(horizon_hours × 4)`) |
| `min_margin_c` | float 0–5 | 0,5 | ondergrens van de marge (p95 en p50) |
| `fixed_margin_c` | float 0–10 | 2,0 | alleen mode fixed |

Defaults in code = huidig gedrag (p95, 1,5, 8 h, 0,5). Daardoor blijven goldens onveranderd door WP4 zelf.

**Site-config Matthijs:** `mode = "p50"`, `min_margin_c = 0.5` (effectieve grens 40,5°C). Verwacht effect zaterdag 19-9: run 1 verschuift van 03:15 naar ~09:50–10:35 (€0,111), besparing ~€0,10/dag. Matthijs kan `min_margin_c` op 0,0 zetten voor strikt 40°C.

### 5.2 Code

- `DhwOptimizerParams` (`dhw_optimizer.py` regel ~60): velden `comfort_margin_mode: str = "p95"`, `tap_stress_factor: float = 1.5`, `min_comfort_margin_c: float = 0.5`; `dynamic_horizon_slots` en `fixed_comfort_margin_c` bestaan al. `use_dynamic_margin` behouden als afgeleide (`mode == "p95"`) voor bestaande aanroepers. Nieuw: `@classmethod from_config(cls, cfg: dict) -> DhwOptimizerParams` met validatie en clamping naar de bereiken hierboven (ongeldige waarde → default + `[WARN]`).
- `solve()` stap 4 (regel ~272): `p95 = p50 × p.tap_stress_factor` (niet meer via `dhw_model.get_learned_tap_kwh_th_p95`), `p05` analoog met de bestaande `TAP_FACTOR_P05`. Margeberekening (regels ~285–296) per mode. `DhwThermalModel.TAP_FACTOR_P95` blijft alleen als fallback voor legacy-paden (`simulate_trajectory`); de optimizer-keten gebruikt uitsluitend `params`.
- `dhw_optimizer_explain.py` regel ~78 (P95-band van het ongestookte traject): zelfde factor uit `params`.
- Doorgifte: `api/context.py` regel ~340 `CentralPlanner.plan(..., dhw_optimizer_params=DhwOptimizerParams.from_config(cfg))`; `central_planner.py` geeft `params` door aan `solve`, `adapt_optimizer_to_dhw_summary`, `explain_dhw_optimization` en `dhw_shadow_logger`. Alle `params or DhwOptimizerParams()`-defaults blijven staan.
- `DhwTankSpec` komt uit de config via WP6; WP4 bouwt daarop voort en geeft `spec` én `params` samen door.

### 5.3 API (invariant 5: openapi + MCP in lockstep)

- `GET /api/settings` (bestaat nog niet; nu alleen POST op regel ~501 van `api/routes_system.py`): retourneert `baseload_watts`, `solar_cost_eur_kwh`, `dhw_optimizer.comfort_margin` (effectieve waarden na defaults).
- `POST /api/settings`: accepteert `dhw_optimizer.comfort_margin` (deel-updates toegestaan), valideert, slaat op met `save_json(CONFIG_FILE, cfg)`, roept daarna `ensure_active_canonical_plan(force_refresh=True)` aan zodat het plan direct herrekend wordt, en retourneert de effectieve waarden. Fout → 400 met veldnaam.
- `docs/openapi.json` + `docs/openapi.yaml`: beide operaties met schema. `mcp_server.py`: `openhems_get_settings` en `openhems_update_settings` (of uitbreiding van bestaande settings-tool). `tests/architecture/test_api_mcp_lockstep.py` groen.
- Test: `tests/unit/test_settings_api.py` roundtrip (POST p50 → GET p50 → plan herrekend, `decision_details.comfort_margin_mode == "p50"`). Voeg `comfort_margin_mode`, `binding_margin_c` en `comfort_boundary_c` toe aan `decision_details` zodat UI en MCP kunnen tonen welke grens gold.

### 5.4 UI (invariant 1: geen rekenwerk in de view; invariant 6: headless verificatie)

- `web/index.html` instellingen-tab, naast `tab-baseload-input` (regel ~1487): blok "Comfortmarge warm water" met `<select id="tab-dhw-margin-mode">` (P95 veilig / P50 gemiddeld / Vast), inputs `tab-dhw-margin-factor`, `tab-dhw-margin-horizon`, `tab-dhw-margin-min`, `tab-dhw-margin-fixed` (toon/verberg per mode) en een opslaan-knop. Semantische ids (`tests/architecture/test_frontend_contracts.py`).
- `web/js/app.js`: `loadSettingsTab()` (GET) en `saveDhwMarginFromTab()` (POST), zelfde patroon als `saveSolarCostFromTab()` (regel ~3435). Na opslaan: dhw-status opnieuw laden zodat de grafiek verschuift.
- Uitlegtekst in de UI (letterlijk overnemen, via locales):

  > **Comfortmarge** bepaalt hoeveel graden het vat minimaal boven de comfortgrens van 40°C moet blijven, als buffer voor een ochtend met meer tapwater dan gemiddeld.
  > **P95 (veilig):** rekent met 1,5× het geleerde tapprofiel over de komende 8 uur. Op een normale zaterdag is dat ~2,5°C extra, waardoor de nachtrun naar voren schuift. Kost ongeveer €0,10 per dag extra.
  > **P50 (gemiddeld):** rekent met het gemiddelde profiel plus de ondergrens. Goedkoper; bij een uitzonderlijk drukke ochtend kan het water tijdelijk lauw zijn. De planner herrekent elke minuut en start dan eerder.
  > **Vast:** altijd dezelfde marge in °C, onafhankelijk van het profiel.

- Grafiek: de grens-lijn "Comfort 40°C" krijgt een tweede gestippelde lijn "Effectieve grens {x}°C" uit `decision_details.comfort_boundary_c` (alleen als ≠ 40,0). Geen berekening in JS.

### 5.5 Documentatie

- `docs/adr/ADR-004-...md` §Constraints: formule per mode + verwijzing naar de instelling.
- `README.md`: sectie "Instellingen → Comfortmarge warm water" met de tabel uit §5.1.
- `CHANGELOG.md`: entry 0.103.52 met de fixes, de nieuwe instelling en een expliciete vermelding dat kosten per DHW-run tot deze versie ~40% te laag werden getoond (1,8 kW i.p.v. 3,0 kW).

---

## 5b. WP6 — Boilerspec uit config (3,0 kW) en COP-herkalibratie

### 5b.1 Waarom dit cruciaal is

De config zegt 3,0 kW, de code rekent met 1,8 kW. Alle stroomkosten in het plan, de uitlegteksten, de `dhw_kw` in dispatch-slots (accu- en netplanning) en de sensor-pusher zijn daardoor een factor 1,67 te laag. Tegelijk **mag 3,0 kW niet zomaar ingevuld worden** met de huidige COP-curve (`cop_50 = 2.85`): dan zou het model 8,5 kW_th leveren en het vat twee keer zo snel opwarmen als in werkelijkheid (gemeten 12–15 K/uur). De optimizer plant dan te korte runs → comfortrisico. Vermogen en COP moeten samen kloppen: `P_el × COP(50°C) ≈ 5,5–6,5 kW_th`.

### 5b.2 Stappen

1. **Spec uit config.** `api/context.py` (regel ~340): `CentralPlanner.plan(..., dhw_spec=DhwTankSpec.from_config(cfg))`. `central_planner.py`: klasseconstanten `DHW_HEAT_PUMP_ELECTRIC_KW` / `DHW_SOLAR_BOOST_ELECTRIC_KW` (regels 47–48) verwijderen en overal `spec` gebruiken. `from_config` leest `compressor_power_kw` (3.0), nieuw `solar_boost_power_kw` (default = `compressor_power_kw` als de sleutel ontbreekt; noteer in README dat dit nog te meten is), `thermal_output_kw`, `min_comfort_temp_c`, `fallback_setpoint_temp`, `boost_setpoint_temp`; valideert 0,5–6,0 kW en logt de effectieve spec één keer bij opstarten. Codedefaults in `DhwTankSpec` blijven 1,8/2,4 (tests), maar productie leest altijd de config.
2. **COP-parameters uit het modelbestand.** `/config/heatpump_model_parameters.json` krijgt een blok `dhw_cop` (`cop_50`, `k_t`, `k_out`, `cop_min`, `cop_max`); `models.physics.get_dhw_cop_params` leest dat blok al. Nieuwe helper `models.physics.load_dhw_cop_params(path)` (één keer laden per solve) en `params=` doorgeven aan álle aanroepen: `dhw_optimizer.py` regels 314, 389, 404, 507, 527, 595, 605, 775, 818; `dhw_optimizer_explain.py` regels 81, 91; `dhw_specs.get_cop`. Geen aanroep zonder `params` meer in de optimizer-keten (guardrail-test met AST of grep in `tests/architecture/`).
3. **Herkalibratie.** `scripts/calibrate_dhw.py` uitbreiden: fit `cop_50`, `k_t`, `k_out` uit gemeten runs (InfluxDB `energy_telemetry`, `device_id='daikin_heat_pump'`, `mode='dhw'`, `power_w`) en de tanktemperatuur; schrijf het resultaat naar het `dhw_cop`-blok met backup en `--dry-run`. Startwaarden bij te weinig data: `cop_50 = 2.0`, `k_t = 0.05`, `k_out = 0.03`, `cop_min = 1.4`, `cop_max = 3.0`. Controle: `3,0 × COP(50, 12°C) × 0,75 h / 0,4068 ≈ 9,5–11,5 K per 45 min`.
4. **Consistentie-guardrail.** Test `test_dhw_spec_thermal_consistency`: voorspelde opwarming per 45 min (`P_el × COP × 0,75 / C`) binnen ±25% van de gemeten referentie (9,3–11,3 K per 45 min uit de 7-daagse historie; vastleggen in de snapshot onder `measured_heating_runs`). Runtime: `layer2_calibration/model_validator.py` logt `[WARN]` als voorspelde en gemeten opwarmsnelheid >25% uiteenlopen.
5. **Hardcoded vermogens opruimen** (gebruik `spec`): `api/routes_analytics.py` regels 160, 166, 178, 878; `api/routes_model.py` 262; `layer3_scheduling/plan_decision_evaluator.py` 94, 188, 282; `layer2_calibration/dhw_thermal_model.py` 218 (default `heat_pump_power_kw`) en docstring 115; `dhw_specs.py` docstrings 53–54. Legacy configsleutels `average_cop`, `cop_temp_slope`, `cop_temp_intercept`, `min_topup_delta_t` in README als deprecated markeren, niet stil verwijderen.
6. **Docs.** `docs/REFERENCE_SITE_CULEMBORG.md` regels 52–53 (1,8/2,4 kW → 3,0 kW, COP ≈ 2,0), `docs/adr/ADR-004` regel 37, `docs/SEMANTIC_DATA_MODEL.md` waar vermogens staan.
7. **Benoem de effecten in de commit-body:** kWh_el en € per run ×1,67; restwaarde `p_hat / COP_hat` stijgt ~45% per graad (COP 2,0 i.p.v. 2,95), dus bufferen wordt relatief aantrekkelijker; `dhw_kw` in dispatch-slots en de sensor-pusher gaan naar 3,0.

### 5b.3 Tests

- `test_dhw_spec_from_config_is_used_by_planner`: config met 3.0 → `plan.dhw_summary.power_kw == 3.0` en `runs[0].kwh_el ≈ 2.25` voor een run van 3 slots.
- `test_dhw_cop_params_loaded_from_model_parameters`: `dhw_cop(50.0, 10.0, params)` == `cop_50` uit het bestand.
- `test_dhw_spec_thermal_consistency` (stap 4).
- `test_no_dhw_cop_call_without_params` (architectuur-guardrail, stap 2).
- Goldens: één keer regenereren samen met WP1, met verklaring per verschil.

---

## 6. Reproductiefixture uit de snapshot

Maak `tests/fixtures/dhw_snapshot_2026_09_19.json` uit `docs/plans/data/dhw_snapshot_2026-09-19.json` en een helper `load_snapshot_slots()` die `OptimizerMockSlot`-objecten bouwt (`dt` lokaal Europe/Amsterdam vanaf 2026-09-19 00:20, `price_all_in`, `solar_kw`, `unallocated_kw = 0.30`, `outdoor_temp_c = 12.0`, labels). `DhwThermalModel` met een in-memory profiel (zaterdag/zondag uit de snapshot) en `standby_loss_w_per_k = 2.38`.

Verwachtingen (na WP1, defaults p95):

- Geen `validation_issue`; run 1 in 03:20–03:50 (goedkoopste vóór de P95-deadline 07:35); uitstel-counterfactual haalbaar met `cost_diff_eur` in [0,00; 0,10].
- Met `mode="p50", min_margin_c=0.5`: run 1 start tussen 09:35 en 10:35; `j_objective_eur` minstens €0,05 lager dan onder p95.
- Met `mode="fixed", fixed_margin_c=2.5`: gedrag ≈ p95 op deze dag.
- Na WP6 (spec 3,0 kW, `cop_50 ≈ 2.0`): een run van 3 slots = 2,25 kWh_el; run 1 om 03:20–03:50 kost ≈ €0,33 stroom + €0,05 start; opwarming per 45 min blijft 9,5–11,5 K.

Kerngetallen ter controle: `T0 = 44,1°C`; ongestookt kruist 42,49°C om 07:35 en 40,5°C om ~10:35; prijzen 03:20/03:35/03:50 = 0,150/0,145/0,139; 06:50–07:20 = 0,167–0,172; 09:50–16:20 = 0,109–0,111 €/kWh.

---

## 7. Definition of done

- [ ] Invarianten 1 t/m 6 uit `AGENTS.md` nagelopen; geen rekenwerk in views/handlers, geen entity-strings in core, geen mockdata in productie.
- [ ] `PYTHONPATH=. pytest tests/` groen, inclusief nieuwe tests uit §2.3, §3, §4.1, §5.3 en de fixture uit §6.
- [ ] Goldens alleen geregenereerd met een verklaring per verschil in de commit-body.
- [ ] `docs/openapi.json`, `docs/openapi.yaml`, `mcp_server.py` in lockstep; `test_api_mcp_lockstep.py` groen.
- [ ] `locales/nl.json` en `locales/en.json` compleet; `test_i18n.py` groen.
- [ ] UI headless geverifieerd: instellingen-blok zichtbaar en werkend, tapvraag-balken zichtbaar, effectieve-grenslijn zichtbaar, geen console-errors.
- [ ] `VERSION`, `config.yaml` en `CHANGELOG.md` op 0.103.52.
- [ ] Site-config van Matthijs op `mode = "p50"`, `min_margin_c = 0.5`.
- [ ] Planner gebruikt `DhwTankSpec.from_config` (3,0 kW); `dhw_cop`-blok in `heatpump_model_parameters.json` gekalibreerd of op startwaarden gezet; consistentietest (±25%) groen.
- [ ] Geen hardcoded 1,8/2,4 kW meer buiten `DhwTankSpec`-defaults en tests; `REFERENCE_SITE_CULEMBORG.md` en ADR-004 bijgewerkt.

---

## 8. Bouw en live-verificatie

De add-on wordt uit `/addons/open-hems` gebouwd (`COPY . /opt/open-hems`); een restart pakt geen nieuwe code op.

```bash
rsync -a --delete --exclude .git --exclude .pytest_cache /addon_configs/local_hermes_agent/addons/open-hems/ /addons/open-hems/
ha apps rebuild local_open_hems
```

Daarna controleren:

1. `ha apps logs local_open_hems` → geen traceback, geen `[WARN] Failed to initialize`.
2. `curl -s http://172.30.33.10:8099/api/settings` → `comfort_margin.mode == "p50"`.
3. `curl -s 'http://172.30.33.10:8099/api/model/dhw-status?resolution=15m'` → `planner == "optimizer"`, run 1 overdag in het goedkope venster, `decision_details.comfort_boundary_c == 40.5`, `sum(trajectory.demand_kwh_th) > 0`, `finance_text` bevat "netto", `runs[0].kwh_el ≈ 2,25` bij 45 min en `dhw_summary.power_kw == 3.0`.
4. Screenshot van de boilergrafiek via headless browser en de uitlegkaarten.
5. Rapporteer in de afsluitende melding: oude vs nieuwe run-tijden, `j_objective_eur` oud vs nieuw, en welke goldens veranderd zijn.
