# Werkinstructie voor Hermes — Parameterbestand aansluiten en k_out zelf valideren

**Opdrachtgever:** Matthijs
**Opgesteld:** 2026-09-19 door Claude (Code-sessie)
**Uitgangsversie:** v0.103.56
**Betreft:** `models/physics.py`, `layer3_scheduling/dhw_specs.py`, `layer3_scheduling/dhw_optimizer.py`, `layer3_scheduling/dhw_plan_adapter.py`, `layer2_calibration/calibrator.py`, `scripts/calibrate_dhw.py`

---

## 0. Besluit vooraf, niet ter discussie

De vermogenswaarden uit v0.103.56 blijven staan: `p_nom_50 = 2.72`, `k_t_tank = 0.074`, clamp `[1.6, 3.5]`. Matthijs heeft die beoordeeld en goedgekeurd. Deze instructie gaat **niet** over het aanpassen van die getallen.

Wel is gesignaleerd dat de curve op basis van een externe herfit aan de onderkant ongeveer 230 W hoger ligt dan de gemeten mediaan. Dat is geen opdracht tot wijziging. Het is een reden om de kalibratieketen werkend te maken, zodat toekomstige metingen de waarden zelf kunnen corrigeren in plaats van dat iemand ze met de hand zet.

---

## Deel A — Het parameterbestand daadwerkelijk aansluiten

### A.1 Het probleem in drie lagen

Het blok `dhw_power` is toegevoegd aan `data/heatpump_model_parameters.json`, maar het heeft vandaag geen enkel effect op het gepubliceerde plan. Er zijn drie onafhankelijke oorzaken. Alle drie moeten worden opgelost; één ervan repareren volstaat niet.

**Laag 1: de parameters worden niet doorgegeven.**
`DhwTankSpec.get_electric_power_kw` accepteert sinds v0.103.56 een argument `params`, maar geen enkele aanroeper vult dat. Geverifieerd met:

```bash
grep -rn 'get_electric_power_kw(' layer3_scheduling/ api/ | grep -v __pycache__ | grep -c 'params='
```

Dat telt nul. Gevolg: `get_dhw_power_params(None)` valt altijd terug op de hardcoded tuple in `models/physics.py`. De aanroepers staan in `dhw_optimizer.py` op de regels 453, 582, 648, 649, 702 en 891, en in `dhw_plan_adapter.py` op de regels 84 en 232.

De infrastructuur bestaat al: `solve()` ontvangt `model_parameters` (regel 233) en geeft die correct door aan `dhw_cop` en `dhw_step`. Alleen de vermogensfunctie is overgeslagen.

**Laag 2: de blokken staan in het verkeerde bestand.**
De solverketen leest `PARAMS_FILE`, gedefinieerd in `api/secrets_store.py:14` als `/config/heatpump_model_parameters.json`. Dat live bestand bevat vandaag noch `dhw_cop`, noch `dhw_power`:

| Bestand | Bevat `dhw_cop` | Bevat `dhw_power` |
|---|---|---|
| `/config/heatpump_model_parameters.json` (live, wordt gelezen) | nee | nee |
| `data/heatpump_model_parameters.json` (repo, alleen terugval) | ja | ja |

**Laag 3: de kalibratie wist onbekende blokken.**
`layer2_calibration/calibrator.py` bouwt op regel 378 een verse dict `updated_params`, wijst die op regel 390 toe aan `self.params` en schrijft het geheel weg op regel 391. Elke kalibratieronde verwijdert dus elk blok dat niet in `updated_params` voorkomt. Dat verklaart waarom het live bestand de nieuwe blokken mist. Zonder deze fix worden ze na de eerstvolgende kalibratie opnieuw gewist.

### A.2 Uit te voeren

1. **Parameters doorgeven.** Vul `params=model_parameters` in bij alle acht aanroepen van `get_electric_power_kw`. Geef in dezelfde beweging `outdoor_temp_c` mee waar de buitentemperatuur van het slot beschikbaar is; in `dhw_optimizer.py` is dat `out_temps[k]` respectievelijk `t_out_k`. Zonder dat argument valt de functie terug op 10,0 °C en is `k_out` per definitie dood, ongeacht de uitkomst van deel B.
2. **Kalibratie laat samenvoegen in plaats van vervangen.** Vervang regel 390 door een samenvoeging die bestaande sleutels behoudt en alleen de opnieuw berekende sleutels overschrijft. Leg in een korte docstring vast welke blokken de calibrator bezit en welke hij ongemoeid moet laten. Schrijf een test die een bestand met een onbekend blok inleest, een kalibratieronde draait en controleert dat het blok er daarna nog in staat.
3. **Blokken naar het live bestand brengen.** Voeg `dhw_cop` en `dhw_power` toe aan `/config/heatpump_model_parameters.json`, met dezelfde waarden als in de repo. Doe dit pas na stap 2, anders wist de eerstvolgende kalibratie ze weer. Maak vooraf een kopie van het live bestand.
4. **Terugval zichtbaar maken.** Laat `get_dhw_power_params` en `get_dhw_cop_params` één regel loggen op niveau waarschuwing wanneer zij op de ingebouwde standaardwaarden terugvallen, met vermelding van het pad dat is geprobeerd. Een stille terugval is precies de reden dat dit drie versies lang onopgemerkt bleef.

### A.3 Aanvaardingstests

Deze tests moeten falen op de huidige code en slagen na de wijziging. Een test die nu al slaagt, toetst niets.

- `test_power_params_are_read_from_file`: schrijf een tijdelijk parameterbestand met een afwijkende `p_nom_50`, bijvoorbeeld 9,99, draai een solve en controleer dat het gerapporteerde vermogen die waarde volgt. Slaagt deze test ook zonder de wijziging uit A.2 stap 1, dan is hij verkeerd opgezet.
- `test_calibration_preserves_unknown_blocks`: zie A.2 stap 2.
- `test_live_params_file_contains_dhw_blocks`: controleer op de aanwezigheid van beide blokken in `PARAMS_FILE`, met een duidelijke foutmelding die naar deze instructie verwijst.
- `test_outdoor_temperature_reaches_power_function`: twee solves met dezelfde invoer maar verschillende buitentemperaturen moeten verschillende vermogens opleveren zodra `k_out` niet nul is. Bij `k_out = 0` mag deze test overgeslagen worden, met vermelding van de reden.

### A.4 Verificatie na uitrol

Bouwen met `rsync` naar `/addons/open-hems` en daarna `ha apps rebuild local_open_hems`; een herstart pakt geen nieuwe code op. Controleer vervolgens:

1. Zet in het live bestand tijdelijk `p_nom_50` op 2,20, wacht één planningscyclus en controleer dat het gerapporteerde verbruik per run meetbaar daalt. Zet de waarde daarna terug op 2,72.
2. Controleer dat er geen waarschuwing over terugval naar standaardwaarden in het log staat.

---

## Deel B — k_out zelf valideren

### B.1 Wat er nu staat en waarom het niet volstaat

`models/physics.py` bevat `k_out = 0.005` met de vorm:

```
P_el = p_nom_50 + k_t_tank · (T_tank − 50) − k_out · (T_out − 10)
```

Het teken zegt dat het opgenomen vermogen daalt bij warmer weer. Die keuze is niet onderbouwd met een fit op eigen data, en de gebruikte meetperiode van zestig dagen nazomer beslaat slechts ongeveer 13 tot 20 °C buitentemperatuur. Een externe analyse vond aanwijzingen voor het tegenovergestelde teken, maar noemde die zelf niet overtuigend.

**Neem geen waarde over van die externe analyse.** Voer een eigen validatie uit en trek een eigen conclusie. Dit onderdeel is nadrukkelijk een onderzoeksopdracht, geen implementatieopdracht. Een uitkomst van nul is een geldig en mogelijk het juiste antwoord.

### B.2 Databron

InfluxDB, database `openhems`, measurement `energy_telemetry`. Inloggegevens via `api.secrets_store.load_secrets()`, sleutel `influxdb.local_ha_influxdb`, gebruiker `openhems`.

| Reeks | Tag | Veld |
|---|---|---|
| Compressorvermogen | `device_id='daikin_heat_pump'`, `mode='dhw'` | `power_w` |
| Tanktemperatuur | `device_id='dhw_tank'` | `temperature_c` |
| Buitentemperatuur | `device_id='outdoor_weather'` | `temperature_c` |

Neem een zo lang mogelijke periode, minimaal negentig dagen. Groepeer op vijf minuten zonder opvulling en koppel de drie reeksen op tijdstempel.

### B.3 Methode

1. **Filter naar stationaire intervallen.** Behoud alleen intervallen waarin de compressor het volledige interval draaide, te herkennen aan een actieve buur aan beide zijden. Deelbelaste rand-intervallen verstoren de fit en zijn niet representatief voor hoe de solver plant, want die rekent met hele kwartieren. Hanteer daarnaast een ondergrens van 800 W.
2. **Leg de aanvaardingscriteria vast vóór je de fit draait.** Schrijf ze op in de commit-body. Voorstel, aan te passen met motivatie:
   - spreiding in buitentemperatuur ten minste 15 K tussen het 5e en 95e percentiel;
   - ten minste 200 bruikbare intervallen;
   - ten minste 30 intervallen in zowel de koudste als de warmste kwintiel van de buitentemperatuur;
   - de geschatte coëfficiënt moet significant van nul verschillen, met een 95 %-betrouwbaarheidsinterval dat nul niet omvat.
3. **Schat beide coëfficiënten tegelijk.** Een meervoudige regressie van het vermogen op tanktemperatuur en buitentemperatuur samen. Doe dit niet in twee stappen: tanktemperatuur en buitentemperatuur zijn in deze dataset gecorreleerd, doordat er 's winters vaker en bij lagere tanktemperaturen wordt gestookt. Een fit die eerst de tanktemperatuur verwijdert en daarna het restant verklaart, schrijft die correlatie ten onrechte toe aan de buitentemperatuur.
4. **Rapporteer de onzekerheid.** Geef per coëfficiënt de schatting, de standaardfout en het betrouwbaarheidsinterval. Zonder interval is de uitkomst niet te beoordelen.
5. **Toets de uitkomst aan de fysica.** Beschrijf welk teken je verwacht en waarom, vóór je naar de uitkomst kijkt. Bij een compressor op vast toerental spelen twee tegengestelde effecten: een hogere buitentemperatuur verhoogt de verdampingsdruk en daarmee de dichtheid van het aangezogen gas en het massadebiet, wat het vermogen opdrijft, terwijl de lagere drukverhouding de specifieke arbeid juist verlaagt. Welk effect wint, hangt af van de compressor en is niet uit de theorie af te leiden. Spreekt de gemeten uitkomst de verwachting tegen, meld dat dan expliciet in plaats van het weg te laten.

### B.4 Beslisregel

| Uitkomst | Actie |
|---|---|
| Alle criteria uit B.2 gehaald | Neem de gefitte waarde en het gefitte teken over in `dhw_power`. Leg schatting, interval en meetperiode vast in de CHANGELOG. |
| Criteria niet gehaald | Zet `k_out` op 0,0 met een commentaarregel die vermeldt welk criterium niet werd gehaald en welke meetperiode nodig is. Plan een herhaling na het stookseizoen. |
| Significant, maar tegengesteld aan de fysische verwachting | Zet `k_out` op 0,0 en meld het conflict. Neem geen coëfficiënt over die je niet kunt verklaren. |

Een waarde die niet aan de criteria voldoet, hoort niet in een bestand dat zich voordoet als kalibratie. Dat raakt invariant 3 uit `AGENTS.md`: geen verzonnen waarden in productie.

### B.5 Het kalibratiescript

Leg de analyse vast in `scripts/calibrate_dhw.py`, zodat ze herhaalbaar is en niet als eenmalige exercitie verdwijnt. Vereisten:

- een optie om alleen te tonen zonder weg te schrijven;
- een kopie van het parameterbestand voordat er iets verandert;
- de filters uit B.3 stap 1 expliciet in de code, niet in een losse query;
- weigeren weg te schrijven wanneer een criterium uit B.2 niet is gehaald, met een duidelijke melding;
- schatting, standaardfout, interval en het aantal waarnemingen in de uitvoer.

### B.6 Op te leveren

1. De code volgens deel A en B.5.
2. Een kort verslag in de commit-body: meetperiode, aantal waarnemingen, spreiding in buitentemperatuur, beide coëfficiënten met interval, de fysische verwachting vooraf, en de genomen beslissing volgens B.4.
3. Bijgewerkte CHANGELOG met de conclusie, ook wanneer die luidt dat de coëfficiënt op nul blijft.
