# Open HEMS — Master AI Agent Scoping Protocol
**Version:** `0.8.0` (Streamlined 4-Layer Modular Monorepo)

When developing, testing, or modifying Open HEMS with AI agents, you **MUST** constrain the agent's work to one specific layer using the dedicated specifications below:

---

## 🎯 Layer Directory & Agent Spec Matrix

| Layer | Directory | Dedicated Spec | Core Responsibility |
| :--- | :--- | :--- | :--- |
| **Laag 1** | `layer1_data_collection/` | `AGENT_SPEC.md` | Data-opslag (InfluxDB), streaming bussen (MQTT), Device Source Adapters (HA entiteiten & MQTT topics) en apparaat-specifieke parameters (dwell-time, noodgrenzen, SG relais). |
| **Laag 2** | `layer2_calibration/` | `AGENT_SPEC.md` | Fysische kalibratie: OLS regressie van zonne-opbrengst $K(h)$, gebouwverlies $UA_{base}$, tapwatervat standby verlies, en sensor downtime uitsluitingsmaskers. |
| **Laag 3** | `layer3_scheduling/` | `AGENT_SPEC.md` | Economische optimalisatie & beleid: 3 Policy Archetypen, 24-uurs dispatch, **Multi-Device Peak Shaving (3x25A limiet)**, hydraulische BUH blokkade, en overschot-prioritering. |
| **Laag 4** | `layer5_analytics/` | `AGENT_SPEC.md` | Analyse & KPI rapportage: Reële financiële besparingen, zonne-zelfconsumptie %, seizoens-COP en forecast validatie (MAE). |
| **Contracten** | `models/canonical.py` | `ARCHITECTURE.md` | Canoniek datamodel (Vector, Flow, Measurement, Policy, Command). |

---

## 🔒 Golden Rules for AI Agents

1. **Strict No-Mock-Data Integrity:** NEVER generate or test against synthetic or mock data. All tests and reports must use real system data or deterministic math.
2. **Device vs. Policy Scoping:**
   - Apparaat-specifieke hardware parameters en noodgrenzen horen bij het **Device (Laag 1)**.
   - Systeembrede totalen (zoals peak shaving van netafname, zonne-overschot volgorde) horen bij **Policies (Laag 3)**.
3. **No Cross-Layer Leakage:** Agents in Laag 1 do not write optimization algorithms. Agents in Laag 3 do not write database drivers.
4. **Credential Isolation:** Never commit or expose passwords. Use `/config/open_hems_secrets.json` (`0600`).
