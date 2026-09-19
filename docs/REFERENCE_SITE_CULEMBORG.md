# Referentie-Siteprofiel: Woning Culemborg (Matthijs van den Berg)

**Document Type:** Site Configuration Reference Profile  
**Status:** Productie-omgeving  
**Datum:** September 2026  
**Eigenaar:** Matthijs van den Berg (matthijs@b3rg.nl)  

---

## 1. Locatie & Weerparameters
* **Plaats:** Culemborg, Nederland
* **Geografische Coördinaten:** 51.9537° N, 5.2320° E
* **Tijdzone:** Europe/Amsterdam (CET/CEST met zomertijdovergang)
* **Lokaal Weerstation:** Wittboy GW2000A (fysieke zonnestraling $W/m^2$, buitentemperatuur, relatieve vochtigheid, windsnelheid)
* **Satelliet Weerbron:** Open-Meteo REST API (D-0 t/m D+2 uurlijkse voorspelling)

---

## 2. Energiecontract & Tariefstructuur
* **Leverancier:** Powerpeers
* **Contractvorm:** Dynamisch uur/kwartiercontract
* **Startdatum:** 25 september 2026
* **Tariefopbouw (per kWh all-in):**
  * EPEX Spotprijs (uur/kwartierprijs)
  * Inkoopopslag leverancier: **€0,01210 per kWh**
  * Energiebelasting + ODE: **€0,11085 per kWh**
  * BTW: **21%** over de totale som
  * Vaste leveringskosten: **€6,25 per maand**

---

## 3. Zonnepanelen (PV) Installatie
* **Vermogen:** 5.760 Wp (5,76 kWp)
* **Oriëntatie:** Zuidwest (azimuth 225°)
* **Hellingshoek:** 34°
* **Omvormer:** 5,5 kW AC piekcapaciteit
* **Bruto Monitoring:** Inepro 103 Modbus kWh-meter (`openhems` database)
* **Systeemrendement:** 0,88

---

## 4. Warmtepomp & Hydraulica
* **Buitenunit:** Daikin Altherma 3 H HT (EPRA18DW17)
  * Type: Lucht-water monobloc, 18 kW thermisch, 3-fase 400V, R32 koudemiddel.
  * Modulatievloer compressor: ~950 W elektrisch minimum.
* **Binnenunit:** Daikin Hydrobox (ETBX16E9W7)
  * Geïntegreerde Back-up Heater (BUH): 9 kW elektrisch (3-fase).
* **Tapwatervat (DHW):** OEG 350 L geëmailleerd boilervat
  * Inhoud: **350 Liter**
  * Thermische capaciteit: **$0,407\text{ kWh/K}$** ($350\text{ kg} \times 4,184\text{ kJ/(kg}\cdot\text{K)} / 3600$)
  * Standby warmteverlies: ~0,055 kW (~1,3 kWh per 24 uur)
  * Normaal setpoint: 50,0°C (~3,0 kW elektrisch op compressor, COP ~2,0)
  * Zonnebuffer boost setpoint: 60,0°C (~3,0 kW elektrisch op compressor)
* **Vloerverwarming (CV):**
  * Dekvloer met hoge thermische inertie (2-massa vloermodel: betonmassa + binnenlucht).
  * Doeltemperatuur woonkamer: 20,0°C (Daikin Madoka thermostaat).
  * Hysteresis: 0,5°C onder `target_temp_low`.

---

## 5. Aansturing, Relais & Hardware Interfaces

### 5.1 Daikin Smart Grid Contacten (S10S / S11S)
Aansturing vindt plaats via twee potentiaalvrije binaire relais aangesloten op de Daikin Smart Grid interface:

| Relais S10S | Relais S11S | Daikin SG Modus | HEMS Gedrag |
| :---: | :---: | :---: | :--- |
| **AAN** | **UIT** | **Stand 1 (Blokkade)** | Compressor geforceerd vergrendeld (harde spitsblokkade). Warmtepomp staat stil. |
| **UIT** | **UIT** | **Stand 2 (Normaal)** | Standaard werking volgens thermostaat/weersafhankelijke stooklijn. |
| **AAN** | **AAN** | **Stand 3 (Aanbevolen)** | Doorverwarmen CV vloerbuffer (+2K bias op aanvoertemperatuur). |
| **UIT** | **AAN** | **Stand 4 (Geforceerd)** | Geforceerde run: DHW boiler opwarmen naar setpoint (50°C of 60°C boost). |

* **Veiligheidsregel Hydraulische Isolatie:** Tijdens actieve DHW runs in Stand 4 moet de CV-hoofdschakelaar (`switch.hc_mode_altherma_on`) UIT staan. Dit voorkomt dat de 9 kW BUH op de CV-groep aanslaat als de driewegklep naar het tapwatervat schakelt.
* **Geheugenintegriteit:** Relaisstanden worden uitsluitend in vluchtig RAM geschakeld; er vinden geen persistente EEPROM write-cycli plaats op het Daikin moederbord.

---

## 6. Home Assistant Entiteitskoppelingen (Provider & Actuator)

Wanneer Open HEMS draait binnen het netwerk van Matthijs, koppelt de Home Assistant adapter aan de volgende entiteiten:

### Telemetrie (Providers)
* `sensor.p1_power` / P1 DSMR telegrammen
* `sensor.hc_dhw_temperature_r5t_dhw_tank` (Tapwatertemperatuur vat)
* `climate.woonkamer_climate_daikin` (Woonkamerthermostaat setpoint & temperatuur)
* `sensor.wittboy_solar_radiation` (Lokale zonnestraling in $W/m^2$)
* `sensor.inepro_103_power` (Zonnepanelen vermogen)

### Actuators (Schakelaars & Services)
* `switch.sg_relais_s10s` (Smart Grid contact 1)
* `switch.sg_relais_s11s` (Smart Grid contact 2)
* `switch.hc_mode_altherma_on` (CV Master Switch ter vergrendeling van BUH)
* `climate.woonkamer_climate_daikin` (Aanpassen comfortdoelen)

---

## 7. Databases & Netwerkconfiguratie
* **InfluxDB 1.8 Instantie:** `http://a0d7b954-influxdb:8086`
  * Database `openhems`: Opslag van 1-seconde en 15-minuten telemetrie (P1, Daikin, Inepro) en planning audits (`hems_recommendations`).
  * Database `hassio`: Historische Home Assistant sensor telemetrie (read-only referentie).
* **Open HEMS Web UI & API:** Poort `8099` (binnen HA Ingress bereikbaar via `/api/hassio_ingress/...`).
* **Secrets Bestand:** `/config/open_hems_secrets.json` (`chmod 0600`).
