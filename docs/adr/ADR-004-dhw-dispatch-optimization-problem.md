# ADR-004: DHW-dispatch als Formeel Optimalisatieprobleem (Exact Dynamic Programming)

* **Status:** Accepted
* **Date:** 2026-09-18
* **Author:** Matthijs van den Berg / Hermes Agent
* **Deciders:** Open HEMS Architecture Working Group

---

## Context & Problem Statement

De oorspronkelijke warm tapwater (DHW) planning steunde op heuristische keuzes (`dhw_daytime_arbiter.py`):
1. Een vaste keuze tussen vooraf bedachte paden (Pad A1 naar 50°C, Pad A2 naar 60°C, Pad B1 standby, Pad B2 bufferen);
2. Een afstandsanker (`hours_to_anchor`) dat een arbitraire doeltemperatuur forceerde;
3. Een stapsgewijze benadering van de COP (2,85 tot 52°C, 2,15 daarboven) waardoor runs soms suboptimaal werden afgebroken of gefragmenteerd in meerdere runs.

Hierdoor ontstonden situaties waarin twee runs van 4°C werden gepland waar één run naar bijvoorbeeld 54°C of 58°C fysisch en financieel aanzienlijk voordeliger was. De eindtemperatuur van een run moet niet vooraf worden gekozen, maar moet wiskundig volgen uit de minimale kosten over de planningshorizon.

---

## Decision: Formele DP-Optimizer

We vervangen alle heuristische paden, ankers en handmatige regels door een pure, canonieke dynamisch-programmeringssolver (`layer3_scheduling/dhw_optimizer.py`).

### 1. Model & Notatie
- **Horizon:** $k = 0..N-1$ kwartieren ($N = 192$ voor 48 uur, $N = 96$ voor 24 uur), stapgrootte $\Delta t = 0,25$ uur.
- **Toestand:**
  - $T_k$: tanktemperatuur (°C) aan het begin van slot $k$. $T_0$ gemeten.
  - $r_k$: compressor runtoestand $r \in \{\text{OFF\_FREE}, \text{OFF\_DWELL}(d), \text{ON\_MANDATORY}(l), \text{ON\_FREE}\}$ voor $d \in \{1..D_{\min}-1\}$ en $l \in \{1..L_{\min}-1\}$.
- **Beslissing:** $u_k \in \{0, 1\}$.
- **Parameters:**
  - $C = \text{spec.thermal\_capacity\_kwh\_per\_k}$ ($0,407$ kWh/K voor 350L vat).
  - $\text{UA} = \text{dhw\_model.get\_tank\_ua()}$ (2,5 W/K), $T_{\text{amb}} = 18,0^\circ\text{C}$.
  - $Q_{\text{tap}, k}$: verwachte P50 aftap (kWh_th) uit geleerde 7x96 matrix.
  - $Q_{\text{tap95}, k}$: P95 aftapscenario voor dynamische comfortbewaking.
  - $p_{\text{eff}, k}$: effectieve stroomprijs (€/kWh) in slot $k$ (zonne-overschot gewaardeerd tegen vermeden teruglevering, rest tegen all-in import).
  - $P_{\text{el}}(T) = \text{spec.get\_electric\_power\_kw}(T)$ (1,8 kW bij $T \le 52^\circ\text{C}$, 2,4 kW bij $T > 52^\circ\text{C}$).
  - $T_{\text{comf}} = 40,0^\circ\text{C}$, $T_{\max} = 60,0^\circ\text{C}$.

### 2. Systeemdynamica (Canonieke Balans)
$$T_{k+1} = T_k + \frac{u_k \cdot P_{\text{el}}(T_k) \cdot \text{COP}(T_k, T_{\text{out}, k}) \cdot \Delta t - \text{UA} \cdot (T_k - T_{\text{amb}}) \cdot \frac{\Delta t}{1000} - Q_{\text{tap}, k}}{C}$$
met $\text{COP}(T_k, T_{\text{out}, k}) = \text{clamp}(\text{COP}_{50} - k_T \cdot (T_k - 50) + k_{\text{out}} \cdot (T_{\text{out}} - 10), \text{COP}_{\min}, \text{COP}_{\max})$. Boven $T_{\max}$ wordt geen warmte meer toegevoegd.

### 3. Doelfunctie (Minimaliseren)
$$J = \sum_{k=0}^{N-1} u_k \cdot P_{\text{el}}(T_k) \cdot \Delta t \cdot p_{\text{eff}, k} + c_{\text{start}} \cdot \sum_{k=0}^{N-1} \max(0, u_k - u_{k-1}) - C \cdot (T_N - T_{\text{comf}}) \cdot \frac{\hat{p}}{\widehat{\text{COP}}}$$
- $c_{\text{start}} = €0,05$ startkosten per compressorstart.
- $\hat{p}$: gemiddelde van de goedkoopste 20% van $p_{\text{eff}}$ over de horizon.
- $\widehat{\text{COP}} = \text{COP}(50, \bar{T}_{\text{out}})$.

### 4. Randvoorwaarden
1. **Comfort:** $T_k \ge T_{\text{comf}} + m_k$ voor alle $k$, met $m_k = \frac{\sum_{j=k}^{k+H} (Q_{\text{tap95}, j} - Q_{\text{tap}, j})}{C}$ ($H = 32$ slots, 8 uur).
2. **Begrenzing:** $T_k \le T_{\max}$.
3. **Hardware-invariants:** Minimale runlengte $L_{\min} = 3$ slots (45 min) en minimale rusttijd $D_{\min} = 4$ slots (60 min).
4. **Harde vergrendelingen:** $u_k = 0$ in slots met harde prijspiekvergrendeling (`is_hard_lockout`).

### 5. Algoritme
- Achterwaartse dynamische programmering over $T$ op een raster van $0,25^\circ\text{C}$ (81 punten) en discrete toestand $r$ (7 toestanden).
- Continue lineaire interpolatie van $V_{k+1}(T', r')$.
- Voorwaartse continue pas vanaf $(T_0, r_0)$.
- Looptijd op host: **~30 ms voor 24 uur, ~63 ms voor 48 uur** (ver onder 1.000 ms doel).

---

## Consequences & Simplifications

- **Verwijderd:** Heuristische paden (Pad A1, Pad A2, Pad B1, Pad B2), ankerberekeningen (`compute_optimal_horizon_target_temp`), en handmatige nachtvensterzoekers in `central_planner.py`.
- **Eenduidige Balans:** De volledige HEMS-pijplijn gebruikt uitsluitend de canonieke `dhw_step` uit `models/physics.py`.
- **Traceerbare Uitleg:** De verklarende tekst en bullets worden direct gegenereerd door 3 formele tegenfeiten ($J_{\text{none}}$, $J_{\text{cap50}}$, $J_{\text{delay}}$) en bevatten exact één zin per geplande run.
- **Kostenverlaging:** In seizoensreplays verlaagt de optimizer de stookkosten met 30% tot 46% door gefragmenteerde runs te consolideren en goedkope zonne-uren volledig te benutten tot aan $T_{\max}$.
