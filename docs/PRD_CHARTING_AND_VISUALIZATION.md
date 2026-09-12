# Open HEMS — PRD: Visualisatie, Grafieken & Design Systeem (v1.0)

**Documentstatus:** Actief / Productiestandaard  
**Datum:** 12 september 2026  
**Auteurs:** Matthijs van den Berg & Hermes Agent  
**Referentie-implementatie:** `OpenHEMSChartEngine` & `OpenHEMSTokens` in `daemon.py`

---

## 1. Doel & Filosofie (Single Source of Truth)

Open HEMS hanteert een strikte scheiding tussen de wiskundige rekenkern (Laag 1 t/m 3) en de presentatielaag (Laag 5: 'Domme Views').

### Kernregels:
1. **Geen Frontend Wiskunde:** JavaScript berekent nooit energietotalen, dips, pieken of sturingsadviezen. De frontend visualiseert uitsluitend wat de `PlanStore` en `CanonicalDispatchPlan` aanleveren.
2. **Geen Data-Eilandjes:** Geen enkele grafiek mag zijn eigen endpoints bevragen met afwijkende tijdsvensters, resoluties of data-aanroepen.
3. **Uniforme Gebruikerservaring:** Alle grafieken delen hetzelfde kleurenpalet, dezelfde typografie, dezelfde tijdsaanduidingen en dezelfde interactieve filters.

---

## 2. Centrale State & Sticky Filters (`OpenHEMSChartEngine`)

De toolbar bovenaan de pagina (en tabbladen) bevat de globale besturing. Alle relevante grafieken luisteren verplicht en synchroon naar deze centrale state:

```javascript
// Centrale opslag in localStorage voor persistentie over browser-sessies heen
const OpenHEMSChartEngine = {
    getChartType(),     // 'bar' (Staven) | 'line' (Lijn)
    setChartType(type), // Werkt state bij, slaat op in localStorage en triggert cascade
    getResolution(),    // '15m' (Kwartieren) | '1h' (Uren)
    setResolution(res), // Werkt state bij, slaat op in localStorage en triggert cascade
    refreshAllCharts()  // Synchrone reload van alle 5 grafieken tegelijk
};
```

### Cascade Refresh
Bij het wijzigen van resolutie of diagramtype worden de volgende componenten gelijktijdig geüpdatet:
1. `loadChartData()` — Hoofd-verbruiksvoorspelling (24h Stacked Dispatch)
2. `loadElectricityPricesChart()` — Prijzen & Zonnevoorspelling (EPEX + Forecast.Solar)
3. `renderDhwTemperatureChart()` — SWW Boiler 350L Thermisch Traject
4. `renderHeatingForecastChart()` — CV Vloerverwarming Traject
5. `renderModelDecompositionChart()` — Zelflerend Decompositiemodel

---

## 3. Universele Design Tokens & Kleurenpalet (`OpenHEMSTokens`)

Alle visuele componenten gebruiken verplicht dezelfde CSS- en Chart.js kleurcodes. Het is verboden om inline afwijkende kleurcodes te definiëren.

| Entiteit / Vector | Token Naam | Hex Code | Beschrijving |
| :--- | :--- | :--- | :--- |
| **Zonnestroom (Opwek)** | `OpenHEMSTokens.colors.solar` | `#F59E0B` | Amber-500: Productie, opwek en prognose |
| **Zon Balk Vulling** | `OpenHEMSTokens.colors.solarBg` | `rgba(245, 158, 11, 0.70)` | Amber semi-transparant voor staven |
| **Zon Gebied Vulling** | `OpenHEMSTokens.colors.solarArea` | `rgba(245, 158, 11, 0.22)` | Zachte amber gloed onder lijn |
| **Ongedefinieerd (Huis)** | `OpenHEMSTokens.colors.unallocated` | `#3B82F6` | Blue-500: Rustend verbruik / 7x96 leefpatroon |
| **SWW Tapwater (Boiler)** | `OpenHEMSTokens.colors.dhw` | `#EC4899` | Pink-500: 350L boileropwarming & SG4 boost |
| **CV Verwarming (Woning)** | `OpenHEMSTokens.colors.heating` | `#6366F1` | Indigo-500: Warmtepomp vloerverwarming |
| **Thuisaccu Laden** | `OpenHEMSTokens.colors.batteryCharge` | `#10B981` | Emerald-500: Net- of zonne-energie naar accu |
| **Thuisaccu Ontladen** | `OpenHEMSTokens.colors.batteryDischarge` | `#14B8A6` | Teal-500: Accu levert aan woning/net |
| **Verwacht Netto** | `OpenHEMSTokens.colors.netto` | `#EF4444` | Red-500: Netafname (>0) of teruglevering (<0) |
| **EPEX Beursprijs** | `OpenHEMSTokens.colors.price` | `#06B6D4` | Cyan-500: All-in stroomtarief referentielijn |
| **EPEX Stepped Line** | `OpenHEMSTokens.colors.priceLine` | `#38BDF8` | Sky-400: Getrapte tarievenlijn over staven |
| **Zon Kostprijs Ref** | `OpenHEMSTokens.colors.solarCost` | `#EAB308` | Yellow-500: Gestippelde referentie (€0,060/kWh) |
| **Gridlijnen (Donker)** | `OpenHEMSTokens.colors.gridLine` | `rgba(30, 41, 59, 0.4)` | Subtiel leisteengrijs |
| **Nulas (Symmetrisch)** | `OpenHEMSTokens.colors.gridLineZero` | `rgba(255, 255, 255, 0.18)` | Duidelijk gemarkeerde 0-as |

---

## 4. Gedrag bij Diagramtype: Staven (`bar`) vs. Lijn (`line`)

Wanneer de gebruiker schakelt tussen **📊 Staven** en **📈 Lijn**, transformeren alle compatibele grafieken synchroon:

### A. Grafiek 1: Verbruiksvoorspelling (`chart-data`)
* **Staven:** Gestapelde staven (`stack: 'consumption'` boven de 0-as, `stack: 'generation'` onder de 0-as). Netto en Stroomprijs lopen als lijnen op de voorgrond.
* **Lijn:** Gelaagde oppervlaktecurven (`fill: true`, `tension: 0.25`) per verbruiks- en opwekcomponent.

### B. Grafiek 2: Prijzen & Zonnevoorspelling (`electricityPricesChart`)
* **Staven:**
  * `Verwachte Zonneproductie`: Rendert als **massieve amberkleurige staven** (`type: 'bar'`, `backgroundColor: OpenHEMSTokens.colors.solarBg`, `borderRadius: 4`) gekoppeld aan de rechter Y-as (`y1`).
  * `EPEX Stroomtarief`: Rendert als **getrapte lijn** (`type: 'line'`, `stepped: 'before'`) gekoppeld aan de linker Y-as (`y`).
* **Lijn:**
  * `Verwachte Zonneproductie`: Rendert als **vloeiende oppervlaktecurve** (`type: 'line'`, `fill: true`, `tension: 0.35`).
  * `EPEX Stroomtarief`: Blijft een getrapte lijn voor tariefprecisie.

---

## 5. Tijdstandaardisatie & Tijdas-Contract

Alle grafieken gebruiken een uniforme tijdnotatie voor de X-as:
1. **Actuele kwartier / start:** Begint altijd met `Nu (HH:MM)`.
2. **Middernacht-overgang:** Toont altijd de weekdagafkorting gevolgd door `00:00` (bijv. `Za 00:00` of `Zo 00:00`). Nooit een losse `00:00`.
3. **Kwartieren / Uren:** Tussenliggende labels zijn strikt `HH:MM`.
4. **Fysische Nachtbegrenzing:** Tussen **21:00 en 07:00 uur** is zonnestroom in Nederland fysisch uitgesloten (`solar_kw = 0.00`).

---

## 6. Checklist voor Nieuwe Grafieken

Wanneer een ontwikkelaar of AI-agent een nieuwe grafiek toevoegt aan Open HEMS, **moet** deze voldoen aan:
- [ ] Gebruikt `OpenHEMSTokens.colors` voor alle kleuren (geen hardcoded hex codes).
- [ ] Luistert naar `OpenHEMSChartEngine.getResolution()` voor data-aanroepen (`?resolution=15m` of `1h`).
- [ ] Luistert naar `OpenHEMSChartEngine.getChartType()` voor de weergave (Staven vs. Lijn).
- [ ] Heeft géén eigen lokale resolutie- of type-dropdowns die kunnen conflicteren met de centrale werkbalk.
- [ ] Registreert zijn renderfunctie in `OpenHEMSChartEngine.refreshAllCharts()`.
- [ ] Toont middernachtmarkeringen conform `Za 00:00` / `Zo 00:00`.
