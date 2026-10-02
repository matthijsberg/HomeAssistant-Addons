# Hermes-plan — Adapterregister: twee geaccepteerde ADR's alsnog implementeren

**Doelversies:** 0.104.x (fase 1 en 2), 0.105.x (fase 3 en 4), 0.106.x (fase 5)
**Aangemaakt:** 2026-09-25 door Claude (Code-sessie), in opdracht van Matthijs
**Implementeert:** `docs/adr/ADR-002-device-agnostic-dispatch-contract.md` en `docs/adr/ADR-003-ha-as-adapter-plugin.md` — beide **Accepted**, beide niet gebouwd
**Blokkeert deels:** `docs/plans/PLAN-thuisbatterij-dispatch.md` — zie §10 voor de precieze volgorde
**Speelregels:** `AGENTS.md` invarianten 1 t/m 7
**Uitgangspunt:** dit is een **pure refactor**. Geen enkele beslissing verandert. Het acceptatiecriterium van elke fase is bit-identieke uitvoer, niet "het werkt nog steeds".

---

## 0. Samenvatting voor de uitvoerder

### 0.1 Ja, dit gaat grotendeels vóór de accu — maar niet alles, en één accu-WP gaat vóór dit plan

Het accuplan bevat `WP-BAT3 — Deviceresolutie en levenscyclus`. Dat is een accu-vormig deelverzameling van wat dit plan fatsoenlijk doet. Bouwen we dat eerst, dan schrijven we `models/device_resolution.py` om het daarna weer weg te gooien. Erger: `WP-BAT2` maakt een `integrations/home_battery/`-pakket dat nergens op is aangesloten, en `WP-BAT8` zou een **tweede** hardgecodeerde actuatietak toevoegen aan een Laag 4 die nu al een Daikin-monoliet van bijna 280 regels is.

Maar er is één uitzondering die niet kan wachten: **`WP-BAT0` (accu uit `unallocated_w`) is één regel met een harde deadline** — het moment dat de accu fysiek wordt aangesloten. Die doe je los, deze week, ongeacht dit plan.

En er is één fase die juist **niet** vooruit moet: fase 4 raakt de live aansturing van warmtepomp en boiler, en levert zonder tweede apparaat niets op. Zie §0.5.

### 0.2 Werkpakketten

| Fase | WP | Wat | Grootte |
|---|---|---|---|
| 1 | REG0 | ADR-005 en de interfacecontracten `ITelemetryReader` / `IDeviceActuator` | S |
| 1 | REG1 | `integrations/registry.py`: registratie, binding en capability-resolutie | M |
| 2 | REG2 | Readers per integratie, met dubbelloop naast `sample_devices` | L |
| 2 | REG3 | Omschakeling Laag 1; 51 entiteitstrings naar configuratie | M |
| 2 | REG4 | `api/context.py`: het tweede ingestiepad op dezelfde readers | M |
| 3 | REG5 | ADR-002: `device_dispatches` op de dispatchslot | M |
| 4 | REG6 | `IDeviceActuator`; Daikin erachter, schaduwdraaiend | L |
| 4 | REG7 | Laag 4 krijgt een device-lus, `safe_state` en een generieke watchdog | M |
| 5 | REG8 | Laag 5 analytics en frontend uit de hardcodering | M |

### 0.3 Wat er in één weekend past

Eerlijke inschatting, met de expliciete eis dat warmtepomp en boiler onaangeroerd blijven.

**Wel, en het is precies de set die alles deblokkeert:**

| WP | Waarom het past | Verificatie |
|---|---|---|
| `WP-BAT0` (accuplan) | Eén regel, harde deadline, geen afhankelijkheden | Unit-test op beide tekenconventies |
| REG0 | Alleen ABC's en dataclasses; niets wordt herbedraad | Contracttests |
| REG1 | Nieuw register naast de bestaande code; niemand gebruikt het nog verplicht | Unit-tests plus de vocabulaire-guard |
| REG5 | `device_dispatches` defaultet leeg; leeg = huidige plan | Golden-fixtures |

Samen: geen enkele regel in `execute_live_dispatch`, geen enkele regel in `sample_devices`, geen enkele regel in `api/context.py`. De aansturing van warmtepomp en boiler blijft letterlijk ongewijzigd. En de opbrengst is groot: `WP-BAT3` vervalt, de stille typefout is dicht, `WP-BAT5` heeft een plek, en **`WP-BAT6` en `WP-BAT7` — de financials en de DP-optimizer — kunnen daarna volledig parallel gebouwd worden**, want die hebben nul afhankelijkheid van het register.

**Niet, en waarom niet:**

| WP | Reden |
|---|---|
| REG2 + REG3 | L plus M, en 14 entiteitstrings verhuizen naar configuratie. De faalmodus is stil: een gemiste meting vervuilt het geleerde 7×96-profiel en dat herstelt niet achteraf. Dit verdient een eigen weekend waarin het het enige is dat je doet |
| REG4 | Raakt het frame, dus het plan, dus de SG-stand van de warmtepomp. Direct in strijd met de eis dat de aansturing blijft lopen zoals nu |
| REG6 + REG7 | Zie §0.5 — uitgesteld tot de accuhardware er is |
| REG8 | Geen urgentie, geen afhankelijkheid |

**Maandagcheck, ongeacht wat er landt:** kijk met eigen ogen naar `unallocated_w` op `/api/pipeline/status` en naar het geleerde profiel. De enige fout in deze categorie die zichzelf niet meldt, is een telemetriefout die het profiel vervuilt.

Let op: `pytest` ontbreekt in de omgeving waarin ik werk. Draai `PYTHONPATH=. pytest tests/` in de add-oncontainer, niet hier.

---

### 0.4 Hoeveel verificatie is genoeg?

Schaduwdraaien is duur en het is niet overal nodig. De bepalende vraag is niet "hoe groot is de wijziging" maar **hoe snel zie je dat het fout is, en wat kost het terugdraaien**.

| WP | Faalmodus | Zichtbaar binnen | Reversibel | Verificatie |
|---|---|---|---|---|
| REG0, REG1 | Import- of resolutiefout | direct | triviaal | Unit-tests. Niets wordt herbedraad |
| REG5 | Contractfout | direct, goldens | triviaal | Golden-fixtures. `device_dispatches` leeg = bit-identiek plan |
| REG2, REG3 | Ontbrekende of verkeerde meting → vervuild 7×96-profiel | **uren tot dagen** | ja, maar de vervuilde historie niet | Fixture-tests **plus** enkele uren dubbelloop met inline diff |
| REG4 | Verkeerd frame → verkeerd plan → **andere warmtepompstand** | uren | ja | Golden-fixtures plus meerdere dagen dubbelloop |
| REG6, REG7 | Verkeerd commando → huis koud of onnodig duur | soms pas achteraf | ja, maar het comfort is al weg | Volledig schaduwdraaien |

Twee dingen volgen hieruit, en ze corrigeren de eerste versie van dit plan:

* **Voor Laag 1 was zeven dagen te zwaar.** De omschakeling is reversibel, de fout is zichtbaar op `/api/pipeline/status` en in de live balans, en de transformatie is deterministisch: gegeven `ha_states` en `mqtt_cache` ligt de uitkomst vast. Vastgelegde snapshots als fixture bewijzen dat uitputtend. Wat fixtures niet dekken zijn zeldzame toestanden — `unavailable`, een ontbrekende entiteit, een MQTT-topic dat maar soms verschijnt. Daarvoor is een dubbelloop van enkele uren met een diff-log genoeg, niet een week.
* **Voor Laag 4 blijft schaduwdraaien staan**, maar dat is grotendeels academisch geworden: zie §0.5.

---

### 0.5 Laag 4 valt nu buiten scope

De eis is: de aansturing van warmtepomp en boiler blijft werken zoals hij nu werkt. Dat is geen argument vóór schaduwdraaien, het is een argument om Laag 4 **niet aan te raken**.

Daar komt een tweede reden bij die zelfstandig overtuigt: zonder accu heeft de device-lus uit REG7 precies één element. Een lus over één actuator, die zich identiek moet gedragen aan de code die er al staat, is risico zonder enige opbrengst. De opbrengst ontstaat pas bij de tweede actuator, en die arriveert met `WP-BAT8`.

**Besluit:** REG6 en REG7 verhuizen naar het moment dat de accuhardware wordt bedraad. Dat lost ook de seizoensvraag uit §12 op — die vraag verdwijnt. `WP-BAT8` bouwt de actuator dan meteen achter `IDeviceActuator` in plaats van als tweede hardgecodeerde tak, en de eenmalige refactor van `execute_live_dispatch` gebeurt op het moment dat er daadwerkelijk twee apparaten zijn.

---

### 0.6 Harde volgorde-eisen

1. **`WP-BAT0` uit het accuplan gaat vóór dit hele plan.** Eén regel, harde deadline, geen afhankelijkheden.
2. **Elke fase levert bit-identieke uitvoer op.** Golden-fixtures ongewijzigd, InfluxDB line-protocol regel voor regel gelijk, actuatiecommando's identiek. Wijkt iets af, dan is dat een bug in de refactor, geen verbetering.
3. **Verificatie is proportioneel aan het risico, niet uniform.** Zie §0.4. Kort samengevat: waar de fout binnen minuten zichtbaar is en de omschakeling reversibel, volstaan fixture-tests plus een dubbelloop van enkele uren. Schaduwdraaien over dagen is gereserveerd voor wat fysiek en niet-attribueerbaar misgaat.
4. **Fase 4 wacht op de accuhardware.** REG6 en REG7 raken de live aansturing van warmtepomp en boiler. Zolang de accu er niet is, heeft de device-lus één element en is de refactor risico zonder opbrengst (§0.5). Bouw ze samen met `WP-BAT8`, wanneer er daadwerkelijk twee apparaten zijn.

---

## 1. Bevindingen die dit plan sturen

### 1.1 Twee geaccepteerde ADR's staan al maanden op papier

Dit plan verzint geen architectuur. Het bouwt wat er al besloten is.

**ADR-002 — Device-Agnostic Dispatch Plan Contract**, status *Accepted*, zegt letterlijk:

> Each slot contains `device_dispatches: Dict[str, DeviceSlotDispatch]` keyed by `device_id` (…) Status codes are defined per functional archetype (…) rather than hardcoded to a specific manufacturer's relays.

De werkelijkheid: `DispatchPlanSlot` heeft `heating_kw` en `dhw_kw` als vaste velden, `device_dispatches` bestaat niet, `DeviceSlotDispatch` bestaat niet, en `schema_version` bestaat niet. Grep op alle vier levert nul treffers.

**ADR-003 — Home Assistant as an Optional Adapter Plugin**, status *Accepted*, zegt:

> `HAEntityCollector` implements `ITelemetryProvider`. `HAServiceActuator` implements `IDeviceActuator`. (…) If Home Assistant restarts, fails, or is absent, the core planning engine operates unaffected.

De werkelijkheid: geen van die vier namen bestaat. `daemon.py` en `api/context.py` bevragen Home Assistant rechtstreeks, met 32 entiteitstrings tussen beide.

Dat is de kern van dit plan: geen nieuw ontwerp, maar geaccepteerde besluiten die zijn weggedreven.

### 1.2 `adapter` en `capabilities` zijn dode velden

Beide worden geschreven door `api/routes_system.py:776-777`, beide worden door **geen enkele** module gelezen. `grep` op `get("adapter")` levert precies één treffer: de regel die het opslaat.

De waarden zijn bovendien onderling inconsistent: `api/secrets_store.py:142` zet `p1_modbus`, `config/site_example.yaml:28` zet `p1_dsmr`. Niemand merkt het, want niemand leest het.

Dat is precies het veld waar een register op draait. Het contract ligt er al; er hangt alleen niets aan.

### 1.3 Er zijn twee parallelle ingestiepaden met 51 hardgecodeerde entiteiten

| Pad | Waar | Voedt | Entiteiten |
|---|---|---|---|
| `HemsBackgroundCollector.sample_devices` | `daemon.py:672-849` | InfluxDB-telemetrie | 14 |
| `ensure_active_canonical_plan` | `api/context.py:97+` | `CleanTelemetryFrame` → planner | 18 |

Twee keer dezelfde warmtepomp uitlezen, uit twee stukken code, met twee sets hardgecodeerde sensornamen. `api/energy_feed.py` voegt er nog 9 toe, `api/routes_schedule.py` 5.

Erger dan het aantal is de vorm. Elke tak in `sample_devices` doet:

```python
p_hp = get_val_w(dev.get("ha_power_entity", "sensor.warmtepomp_power"))
```

Een **fallback op een verzonnen sensornaam**. Ontbreekt de configuratie, dan raadt de code een entiteit in plaats van niets te rapporteren. Dat is invariant 3 in zijn kern: bij een ontbrekende binding hoort geen meting, geen gok.

### 1.4 Laag 4 heeft geen device-lus

`execute_live_dispatch` (`daemon.py:382-656`) is één functie die achtereenvolgens doet: tapwatertemperatuur lezen, in-flight continuïteit bewaken, de kamerthermostaat voorverwarmen, twee watchdogs draaien, de spitsduur uit een relaisstand afleiden, `make_daikin_ha_actuator()` instantiëren en een modus uitvoeren. Alles met letterlijke entiteitnamen, alles Daikin-specifiek, zonder enige iteratie over apparaten.

Een tweede actuator toevoegen betekent vandaag: een tweede blok in dezelfde functie. Een derde: een derde blok. Dat is exact de drift die `AGENTS.md` invariant 2 verbiedt.

`integrations/daikin_altherma/actuator.py:27` implementeert al `IActuatorController`. Maar die interface heeft `apply_smart_grid_mode` als methode — een Daikin-begrip op een algemeen contract. Het contract lekt het apparaat.

### 1.5 Laag 5 heeft device-id's als letterlijke strings in SQL

`api/routes_analytics.py` bevat elf queries met een hardgecodeerde `device_id`:

| Id | Aantal queries |
|---|---|
| `daikin_heat_pump` | 4 |
| `main_grid_meter` | 4 |
| `rooftop_solar` | 2 |
| `dhw_tank` | 1 |

Noemt een gebruiker zijn warmtepomp anders, dan blijft de analysepagina leeg. Verwijdert hij een apparaat, dan verdwijnt ook de historie uit beeld — terwijl invariant 7 juist eist dat historische telemetrie zichtbaar blijft.

### 1.6 Er zijn twee adapterconventies, en dat is goed

`integrations/` en `site_adapters/` lijken dubbelop maar zijn het niet:

* `integrations/<apparaatfamilie>/` — stuurprogramma's: `reader.py`, `actuator.py`, `interlocks.py`. Dit worden de geregistreerde adapters.
* `site_adapters/<protocol>/` — signaalinterpretatie voor de eigenaardigheden van één installatie. `DaikinP1P2StateClassifier` leidt uit één vermogensmeting af of de warmtepomp tapwater of ruimteverwarming draait. Dat is geen apparaatstuurprogramma.

**Niet samenvoegen.** Wel vastleggen: een `site_adapter` wordt *gebruikt door* een reader en registreert zichzelf nooit als adapter. Dit plan legt het verschil vast in ADR-005 en borgt het met een importguard.

### 1.7 Wat dit plan uitdrukkelijk niet doet

Scopebewaking, zodat het plan eindig blijft:

* **Geen nieuwe protocollen.** Geen Modbus-client, geen OCPP, geen standalone-modus zonder Home Assistant. ADR-003 noemt `ModbusTcpCollector` en `P1SerialCollector`; dit plan maakt alleen de *plek* waar die later inpluggen. Wie ze nu bouwt, bouwt ongebruikte code.
* **Geen tweede site, geen multi-tenant.** Eén installatie blijft het uitgangspunt.
* **Geen gedragswijziging.** Zie het uitgangspunt. Elke verbetering die je onderweg ziet wordt een issue, geen commit.

---

## 2. Het contract

### 2.1 Registratie is expliciet, niet magisch

```python
# integrations/registry.py
@dataclass(frozen=True)
class AdapterSpec:
    slug: str                                   # "daikin_altherma", "deye_modbus_tcp"
    supported_types: frozenset[str]             # device.type waarden die deze adapter dekt
    capabilities: frozenset[DeviceCapability]   # wat de adapter feitelijk kan
    reader: Optional[Type["ITelemetryReader"]] = None
    actuator: Optional[Type["IDeviceActuator"]] = None
    interlocks: Optional[Type["IInterlock"]] = None
```

Elk integratiepakket exporteert één `ADAPTER: AdapterSpec` in zijn `__init__.py`. `integrations/__init__.py` importeert ze en roept `register()` aan — een expliciete lijst, geen mapscan en geen dynamische import.

Waarom expliciet: een scan die een pakket niet vindt faalt stil, en de bestaande guard `test_third_party_imports_declared_in_dockerfile` (`tests/architecture/test_anti_drift_guards.py:175`) loopt op statische imports. Magie breekt allebei.

### 2.2 Binding koppelt configuratie aan adapter

```python
@dataclass(frozen=True)
class DeviceBinding:
    device: dict          # de ruwe configuratie-entry
    spec: AdapterSpec
    device_id: str
    device_type: str
    is_active: bool       # installed AND enabled, beide default True

def bind_all(cfg) -> List[DeviceBinding]
def bind_one(cfg, device_id) -> Optional[DeviceBinding]
def by_capability(cfg, cap: DeviceCapability, *, active_only=True) -> List[DeviceBinding]
def by_type(cfg, device_type: str, *, active_only=True) -> List[DeviceBinding]
def single_by_capability(cfg, cap) -> Optional[DeviceBinding]   # 0 of 1; >1 werpt
```

`by_capability` is wat Laag 3 straks gebruikt: "geef me het apparaat dat `can_store` én vector elektriciteit is" in plaats van een typestring vergelijken. Dat is de belofte uit `AGENTS.md` Recept 1, voor het eerst waargemaakt.

Een apparaat met een onbekende `adapter` levert een binding met `spec=None` en een expliciete diagnose in `/api/health/consistency` — niet een stille overslag zoals nu.

### 2.3 Lezen levert canonieke metingen

```python
@dataclass(frozen=True)
class SourceContext:
    ha_states: Dict[str, Any]
    mqtt_cache: Dict[str, Any]
    now: datetime

class ITelemetryReader(ABC):
    @abstractmethod
    def read(self, binding: DeviceBinding, ctx: SourceContext) -> List[Measurement]: ...
```

`Measurement` bestaat al (`models/canonical.py:127`) met `device_id`, `vector`, `flow`, `value`, `unit`, `timestamp`, `quality` en `metadata`. De accumulatorsleutel die `sample_devices` nu vijf keer met de hand opbouwt, wordt één afleiding uit een `Measurement`.

Harde regel: **een reader die zijn binding niet vindt levert een lege lijst**, eventueel met een `Quality.EXCLUDED`-diagnose. Nooit een gegokte entiteitnaam.

### 2.4 Aansturen is per apparaat, met een verplichte veilige stand

```python
class IDeviceActuator(ABC):
    @abstractmethod
    def execute(self, binding, command: DeviceCommand) -> ActuationResult: ...

    @abstractmethod
    def safe_state(self, binding) -> DeviceCommand:
        """De stand waar dit apparaat naartoe moet bij verwijdering, storing of watchdog."""

    @abstractmethod
    def read_effective_state(self, binding, ctx: SourceContext) -> EffectiveState: ...
```

Drie dingen die vandaag ontbreken en die geen van drieën optioneel zijn:

* `safe_state` is wat het accuplan nodig heeft bij `DELETE /api/devices/<id>` en wat de watchdog nodig heeft. Nu declareert geen enkele actuator zijn terugvalstand.
* `read_effective_state` levert `effective_mode` en `realized_power_kw`, die `AGENTS.md` van elke Laag 4-actuator eist en waarop Laag 2 hoort te trainen.
* `apply_smart_grid_mode` verdwijnt van het algemene contract en blijft een methode op `DaikinActuator`.

---

## 3. Fase 1 — Fundament

### WP-REG0 — ADR-005 en de interfacecontracten

**Bestanden:** `docs/adr/ADR-005-adapter-registry.md` (nieuw), `integrations/interfaces.py` (nieuw), `layer4_control/interfaces.py`.

1. Schrijf ADR-005 met de vier contracten uit §2 en het onderscheid `integrations/` versus `site_adapters/` uit §1.6.
2. Voeg aan ADR-002 en ADR-003 een regel toe: *"Implementatie belegd in `docs/plans/PLAN-adapter-register.md` (2026-09-25)."* Een geaccepteerde ADR die jarenlang niet gebouwd is hoort dat zichtbaar te maken.
3. `integrations/interfaces.py` krijgt `ITelemetryReader`, `IDeviceActuator`, `IInterlock`, `SourceContext`, `EffectiveState`, `AdapterSpec`, `DeviceBinding`. Puur ABC's en dataclasses, nul implementatie, nul entiteitstrings.
4. `IActuatorController` in `layer4_control/interfaces.py` blijft bestaan voor `DaikinActuator`, maar krijgt een docstring die zegt dat het een apparaatspecifiek contract is en dat `IDeviceActuator` het algemene is. Niet verwijderen in deze WP — dat is REG6.

**Tests** `tests/architecture/test_adapter_contracts.py`:

* `test_interfaces_have_no_entity_strings`.
* `test_interfaces_import_nothing_from_layer3_or_layer4`: het contract mag niet naar boven wijzen.
* `test_adr_002_and_003_reference_this_plan`.

---

### WP-REG1 — Het register en de resolutie

**Bestand:** `integrations/registry.py` (nieuw), `integrations/__init__.py`.

Dit werkpakket **vervangt `WP-BAT3` uit het accuplan volledig.**

1. Implementeer `AdapterSpec`, `DeviceBinding` en de resolutiefuncties uit §2.1 en §2.2.
2. Registreer twee adapters om te beginnen: `daikin_altherma` (bestaat al als pakket) en `generic_ha_sensor` (de vangnetadapter voor alles wat nu via `ha_power_entity` binnenkomt: netmeter, PV, tapwatervat). Meer niet — nieuwe adapters horen bij de fase die ze nodig heeft.
3. **Eén typevocabulaire.** Vandaag bestaan er vier voor de accu:

| Plek | Waarde |
|---|---|
| `docs/openapi.json:541` | `battery` |
| `web/index.html:3042` | `home_battery` |
| `daemon.py:774` | alle drie varianten |
| `api/routes_schedule.py:75` | exact `home_battery` |

   Normaliseer op de waarde die `DeviceType` in `models/canonical.py` al hanteert. Oude waarden blijven als alias in `supported_types`; `ensure_framework_defaults` herschrijft bestaande configs eenmalig.

4. `is_active(binding)` wordt de enige poort op `installed` en `enabled`. Vandaag negeert `sample_devices` beide en kijkt alleen `routes_schedule.py:75` ernaar.
5. Diagnose in `/api/health/consistency`: per apparaat de gevonden adapter, de opgeloste capabilities en of het actief is. Een onbekende `adapter` is een zichtbare waarschuwing, geen stilte.
6. Laat `capabilities` uit de configuratie voorlopig **adviserend** zijn: de `AdapterSpec` is de waarheid, het configuratieveld wordt ertegen gevalideerd en bij afwijking gelogd. Anders erft het register de rommel die er nu in staat.

**Tests** `tests/unit/test_adapter_registry.py`:

* `test_bind_resolves_known_adapter` en `test_bind_reports_unknown_adapter_without_raising`.
* `test_by_capability_filters_on_adapterspec_not_config`.
* `test_single_by_capability_raises_on_duplicates`.
* `test_disabled_device_excluded_from_active_queries`.
* `test_type_aliases_resolve_to_canonical_devicetype`.

`tests/architecture/test_device_vocabulary.py`:

* `test_openapi_enum_matches_ui_dropdown_and_devicetype`: sluit de stille fout uit §1.2 en het accuplan definitief.

**Verificatie:** `/api/health/consistency` toont voor elk geconfigureerd apparaat een adapter en een capabilityset, zonder dat er iets aan gedrag verandert.

---

## 4. Fase 2 — Laag 1 door het register

### WP-REG2 — Readers, met dubbelloop

**Bestanden:** `integrations/*/reader.py`, `daemon.py`.

1. Implementeer `ITelemetryReader` per adapter. Elke reader retourneert `List[Measurement]`; de vijf takken uit `daemon.py:694-785` worden vijf readermethodes met dezelfde inhoud.
2. De Daikin-reader roept `site_adapters.daikin_p1p2.DaikinP1P2StateClassifier` aan voor de modusdisaggregatie en zet het resultaat in `Measurement.metadata["mode"]`. Zo blijft §1.6 gerespecteerd.
3. **Dubbelloop.** `sample_devices` blijft het gezag. Ernaast draait `registry.read_all(cfg, ctx)` en een vergelijker die per cyclus de twee accumulatorsleutelsets diff't. Elk verschil komt in het log met sleutel, oude en nieuwe waarde.
4. **Enkele uren volstaat, mits die uren de juiste toestanden dekken** (§0.4). Vereist vóór REG3 omschakelt: minstens één tapwaterrun, één ruimteverwarmingsrun, één periode met zonoverschot en één compressorstilstand, allemaal met nul verschillen. Dat is een halve dag, geen week. Log per toestand dat hij gezien is, zodat de dekking aantoonbaar is in plaats van gehoopt.
5. Aanvullend en belangrijker: leg van elk van die vier momenten een `ha_states`- plus `mqtt_cache`-snapshot vast als fixture. Die dekking blijft, de dubbelloop is eenmalig.

**Tests** `tests/unit/test_telemetry_readers.py`:

* Per reader: `test_<adapter>_reader_matches_legacy_branch` met een vastgelegde `ha_states`- en `mqtt_cache`-snapshot als fixture.
* `test_reader_returns_empty_on_missing_binding`: geen gegokte entiteit, geen meting.
* `test_measurement_key_derivation_matches_legacy_accumulator_key`: dit is de test die de omschakeling veilig maakt.

---

### WP-REG3 — Omschakeling en de entiteiten naar configuratie

**Bestanden:** `daemon.py`, `api/secrets_store.py`.

1. Vervang de `if/elif`-keten door:

```python
for binding in registry.readers(cfg):
    for m in binding.spec.reader().read(binding, ctx):
        self._accumulate(m)
```

2. Verwijder alle fallback-entiteitstrings uit `daemon.py`. De 14 treffers verhuizen naar `ensure_framework_defaults` als **configuratiedefaults van deze installatie**, niet als codedefaults. Onderscheid dat expliciet in de commitmelding: een default in de configuratie is een installatiekeuze, een default in de code is invariant 2.
3. Scherp de guard `test_core_entity_isolation` aan van vier letterlijke namen naar een patroon over `daemon.py`, `api/`, `models/`, `layer1..layer4` en `integrations/` — met `integrations/*/reader.py` en `site_adapters/` als enige toegestane plekken, en zelfs daar alleen uit de binding.
4. Een apparaat zonder geconfigureerde entiteit levert nul metingen en een zichtbare diagnose. Niet een gok.

**Verificatie:** de InfluxDB line-protocol uitvoer van één volledig etmaal is regel voor regel identiek aan het etmaal ervoor, afgezien van tijdstempels en meetwaarden. Leg de diff vast in de CHANGELOG.

---

### WP-REG4 — Het tweede ingestiepad

**Bestand:** `api/context.py`.

**Probleem.** `ensure_active_canonical_plan` leest 18 entiteiten rechtstreeks om de `CleanTelemetryFrame` te bouwen — een compleet tweede ingestiepad naast `sample_devices`, met eigen hardgecodeerde namen. Twee paden die dezelfde warmtepomp anders kunnen lezen is een bron van onverklaarbare verschillen tussen grafiek en plan.

1. Laat `api/context.py` dezelfde readers gebruiken. De frame-opbouw wordt een projectie van `List[Measurement]` op `TelemetrySlot`, niet een tweede set `get_val` aanroepen.
2. Waar het planpad een grootheid nodig heeft die de telemetriereader niet levert (kamertemperatuur, klepstanden, vloertemperatuur), breid je de reader uit — je maakt geen tweede pad.
3. Dubbelloop zoals REG2, maar hier wél over meerdere dagen: een verkeerd frame geeft een verkeerd plan en daarmee een andere SG-stand van de warmtepomp (§0.4). Het oude pad houdt gezag tot de frames over minstens drie volledige etmalen identiek zijn, inclusief één nacht met een nachtrun.

**Tests:** `test_frame_from_readers_matches_legacy_frame` over alle zes golden-fixtures, veld voor veld.

**Verificatie:** `PYTHONPATH=. pytest tests/unit/test_replay_harness.py` groen zonder de goldens te vernieuwen. Moet een golden wél bewegen, dan is dat een bug in deze WP.

---

## 5. Fase 3 — Het slotcontract

### WP-REG5 — ADR-002: `device_dispatches`

**Bestand:** `models/canonical.py`.

**Waarom nu en niet later.** Het accuplan wil in `WP-BAT5` zeven accuvelden aan `DispatchPlanSlot` hangen. ADR-002 verbiedt precies dat. Landt REG5 eerst, dan schrijft het accuplan zijn detail op de goede plek; landt REG5 later, dan moeten we het verplaatsen inclusief frontend, API en goldens.

Let op de nuance in ADR-002: die vraagt **allebei**. Per-apparaat detail in `device_dispatches`, én geaggregeerde stroomgrootheden op slotniveau. De aggregaten zijn dus ADR-conform; alleen het detail hoort verplaatst.

1. Voeg toe:

```python
@dataclass
class DeviceSlotDispatch:
    device_id: str
    device_type: str
    mode_code: str                 # per archetype uit config/mode_catalog.json
    mode_label: str
    electric_kw: float             # + = afname, - = levering
    payload: Dict[str, Any] = field(default_factory=dict)   # archetype-specifiek detail
```

2. `DispatchPlanSlot` krijgt `device_dispatches: Dict[str, DeviceSlotDispatch] = field(default_factory=dict)` en `CanonicalDispatchPlan` krijgt `schema_version: str = "1.1.0"`.
3. `heating_kw` en `dhw_kw` blijven staan als geaggregeerde grootheden, conform ADR-002. Ze worden **afgeleid** uit `device_dispatches` zodra Laag 3 die vult, met een invariantentest die eist dat de aggregaten en de som van de details overeenkomen.
4. `mode_code` per device valideert tegen het bijbehorende archetype in `config/mode_catalog.json`. De accu krijgt `battery_storage`, de warmtepomp `thermal_buffer`. Dat lost meteen op dat `DispatchPlanSlot.__post_init__` elke niet-thermische code door `StandardizedState(raw)` duwt en er een `ValueError` op gooit.

**Tests** `tests/unit/test_slot_contract.py`:

* `test_aggregates_equal_sum_of_device_dispatches`.
* `test_device_mode_code_validated_against_its_archetype`.
* `test_empty_device_dispatches_is_bit_identical_to_current_plan`: de terugvalgarantie.
* `test_schema_version_present_and_bumped`.

---

## 6. Fase 4 — Laag 4 door het register

> **Uitgesteld tot de accuhardware wordt bedraad** (§0.5). Bouw deze fase samen met `WP-BAT8`, niet eerder. Zolang er één actuator is, levert een device-lus niets op en zet je wel de werkende aansturing van warmtepomp en boiler op het spel.

### WP-REG6 — `IDeviceActuator` en Daikin erachter

**Bestanden:** `integrations/daikin_altherma/actuator.py`, `integrations/interfaces.py`.

1. Laat `DaikinActuator` `IDeviceActuator` implementeren naast het bestaande `IActuatorController`. `apply_smart_grid_mode` blijft een Daikin-methode; `execute`, `safe_state` en `read_effective_state` komen erbij.
2. `safe_state` voor de Daikin is SG2 / normaal: relais los, geen geforceerde stand. Leg dat expliciet vast in plaats van het impliciet te laten.
3. `read_effective_state` leest de werkelijke relaisstanden en het werkelijke vermogen terug en levert `effective_mode` plus `realized_power_kw`. Vandaag rapporteert `ActuationResult` wat er *gestuurd* is, niet wat er *gebeurde*.
4. **Schaduwdraaien.** `execute_live_dispatch` blijft aansturen. Ernaast berekent het nieuwe pad het commando en logt het. Pas na zeven dagen identieke commando's schakelt REG7 om. Dit is de enige fase waarin een fout fysieke gevolgen heeft; schaduwdraaien is hier geen formaliteit.

**Tests:** `test_daikin_safe_state_is_sg2`, `test_effective_state_reflects_relays_not_request`, `test_shadow_command_matches_legacy_for_all_six_modes`.

---

### WP-REG7 — De device-lus in Laag 4

**Bestand:** `daemon.py:382-656`.

1. Splits `execute_live_dispatch` in drie delen:
   * **Orkestratie** (blijft in Laag 4): in-flight continuïteit, spitsduurbepaling, annotaties.
   * **Per-apparaat actuatie** (wordt een lus): `for binding in registry.actuators(cfg): actuator.execute(binding, command_for(binding, slot))`.
   * **Apparaatspecifiek** (verhuist naar `integrations/daikin_altherma/`): kamerthermostaat-voorverwarming, tapwatersetpoint-watchdog, relaisuitlezing.
2. Generaliseer de watchdog. Nu zijn er twee, allebei Daikin-specifiek en met de hand geschreven. Eén generieke watchdog per binding die na `watchdog_timeout_s` `safe_state()` aanroept, dekt de bestaande twee én de accu uit `WP-BAT8`.
3. `DELETE /api/devices/<id>` roept `safe_state()` aan voordat het apparaat uit de configuratie verdwijnt. Dat is het gat dat het accuplan in `WP-BAT3` signaleerde, hier structureel opgelost voor elk apparaat.
4. Laat `GLOBAL_ROOM_BUFFER_CTRL` waar het is. Het is een regelaar met een eigen toestand, geen actuator; door het register trekken levert niets op.

**Tests:** `test_actuator_loop_executes_once_per_active_binding`, `test_watchdog_reverts_every_binding_to_safe_state`, `test_device_removal_triggers_safe_state_before_config_write`.

---

## 7. Fase 5 — Laag 5 en frontend

### WP-REG8 — Analytics en presentatie uit de hardcodering

**Bestanden:** `api/routes_analytics.py`, `api/energy_feed.py`, `web/js/app.js`.

1. Vervang de elf letterlijke `device_id`-vergelijkingen door id's uit de binding. Voor historie van een verwijderd apparaat: leid de id af uit de opslag, niet uit de huidige configuratie.

```sql
SHOW TAG VALUES FROM "energy_telemetry" WITH KEY = "device_id" WHERE "device_type" = 'battery'
```

2. Invariant 7 expliciet: een apparaat dat is verwijderd blijft zichtbaar in grafieken over de periode dat het bestond. Er wordt niets uit InfluxDB verwijderd, en de query vindt het apparaat ook zonder configuratie-entry.
3. Frontend: series en legenda afleiden uit de apparatenlijst plus `data/theme_colors.json`, niet uit een vaste lijst in `app.js`.
4. Headless-browserverificatie conform invariant 6.

---

## 8. Configuratie en migratie

Geen nieuwe configuratiesectie. Wel drie wijzigingen aan bestaande velden:

| Veld | Nu | Na dit plan |
|---|---|---|
| `adapter` | dood, inconsistente waarden | sleutel van het register; gevalideerd, zichtbaar in health |
| `capabilities` | dood | adviserend, gevalideerd tegen `AdapterSpec` |
| `type` | vier vocabulaires voor de accu | één, met aliassen als migratiepad |

`ensure_framework_defaults` doet de migratie eenmalig en idempotent: typealiassen normaliseren, `p1_modbus` versus `p1_dsmr` gelijktrekken, ontbrekende `adapter` afleiden uit `type`. Een gebruiker merkt er niets van.

---

## 9. Guardrails

| Test | Bewaakt |
|---|---|
| `test_core_entity_isolation` (aangescherpt, REG3) | Entiteitstrings alleen nog in `integrations/*/reader.py` en `site_adapters/`, en daar alleen uit de binding |
| `test_device_vocabulary.py` (nieuw, REG1) | OpenAPI, UI, `DeviceType` en register spreken één taal |
| `test_adapter_contracts.py` (nieuw, REG0) | Contracten wijzen niet naar Laag 3 of 4 en bevatten geen entiteiten |
| `test_site_adapters_never_register` (nieuw, REG1) | `site_adapters/` levert geen `AdapterSpec`; het onderscheid uit §1.6 blijft |
| `test_measurement_key_derivation` (REG2) | Line protocol blijft identiek |
| `test_aggregates_equal_sum_of_device_dispatches` (REG5) | ADR-002-invariant |
| `test_every_actuator_declares_safe_state` (REG6) | Geen apparaat zonder terugvalstand |
| `test_replay_harness` (bestaand) | Goldens bewegen niet |

Werk `AGENTS.md` bij onder *Exact Codename Parity* met `AdapterSpec`, `DeviceBinding`, `ITelemetryReader` en `IDeviceActuator`, en breid *Recept 1* uit met de registratiestap — dat recept belooft nu iets wat het systeem nog niet kan.

---

## 10. Verhouding tot het accuplan

Dit is de tabel waar het sequencing-besluit op neerkomt.

| Accu-WP | Wacht op | Waarom |
|---|---|---|
| **BAT0** accu uit `unallocated_w` | **niets** | Eén regel, harde deadline bij fysieke installatie. Doe dit los en meteen |
| BAT1 SoC-ingestie | REG3 | Landt als reader in plaats van als zesde `if/elif`-tak |
| BAT2 entiteiten naar `integrations/` | REG1 + REG2 | Wordt grotendeels overbodig: de reader *is* de integratie |
| **BAT3** deviceresolutie | — | **Vervalt.** Volledig vervangen door REG1 |
| BAT4 heuristiek uit `routes_schedule.py` | niets | Onafhankelijk, kan parallel |
| BAT5 datacontracten | REG5 | Anders schrijf je velden die ADR-002 verbiedt en verplaats je ze later |
| BAT6 financials | niets | Pure module, volledig parallel te bouwen |
| BAT7 optimizer | niets | Pure module, volledig parallel te bouwen |
| BAT8 hardware-actuatie | REG7 | Anders een tweede hardgecodeerde tak in de Laag 4-monoliet |
| BAT9 t/m BAT13 | volgen | Geen extra afhankelijkheid |

Praktische lezing: **BAT0 nu, dan REG0 t/m REG5, en ondertussen BAT6 en BAT7 parallel** — die twee zijn pure wiskunde zonder enige afhankelijkheid van het register. Je verliest dus geen tijd op de rekenkern terwijl het fundament wordt gelegd.

Na dit plan wordt het accuplan aangepast: BAT3 vervalt, BAT1 en BAT2 krimpen, BAT5 verwijst naar `device_dispatches`, en §11 van dat plan ("Buiten scope: de bredere device-abstractie") wordt vervangen door een verwijzing hierheen.

---

## 11. Definition of done

**Fase 1:**

- [ ] ADR-005 geschreven; ADR-002 en ADR-003 verwijzen naar dit plan.
- [ ] `/api/health/consistency` toont per apparaat adapter, capabilities en actieve status.
- [ ] Eén typevocabulaire, geborgd met een guard.
- [ ] Nul gedragswijziging: `PYTHONPATH=. pytest tests/` groen zonder goldens te vernieuwen.

**Fase 2:**

- [ ] Dubbelloop met nul verschillen over vier aantoonbaar gedekte toestanden: tapwaterrun, ruimteverwarmingsrun, zonoverschot en compressorstilstand.
- [ ] Van elk van die vier momenten een snapshot-fixture vastgelegd.
- [ ] Nul entiteitstrings buiten `integrations/*/reader.py` en `site_adapters/`.
- [ ] Line protocol van een volledig etmaal aantoonbaar identiek.
- [ ] `CleanTelemetryFrame` uit de readers, veld voor veld gelijk aan het oude pad over alle zes goldens.

**Fase 3:**

- [ ] `device_dispatches` en `schema_version` in het contract; aggregaten afgeleid en getest.
- [ ] Leeg `device_dispatches` levert een bit-identiek plan.

**Fase 4:**

- [ ] Zeven dagen identieke actuatiecommando's in schaduw — hier wél onverkort (§0.4).
- [ ] Elke geregistreerde actuator declareert `safe_state`.
- [ ] `DELETE /api/devices/<id>` zet het apparaat aantoonbaar terug naar zijn veilige stand.
- [ ] Gebouwd samen met `WP-BAT8`, niet eerder.

**Fase 5:**

- [ ] Nul letterlijke `device_id`-strings in InfluxQL.
- [ ] Historie van een verwijderd apparaat blijft zichtbaar; aangetoond met een verwijder-en-bekijk-test.
- [ ] Headless-browserverificatie groen.

---

## 12. Openstaande beslissingen

| # | Vraag | Waarom het uitmaakt |
|---|---|---|
| 1 | Blijft `IActuatorController` bestaan naast `IDeviceActuator`? | Twee contracten naast elkaar is verwarrend; samenvoegen raakt `DaikinActuator` dieper dan REG6 nu doet |
| 2 | Wordt `capabilities` in de configuratie ooit leidend, of blijft de `AdapterSpec` de waarheid? | Leidend maken geeft gebruikers touw om zich mee op te hangen; adviserend houden maakt het veld half-dood |
