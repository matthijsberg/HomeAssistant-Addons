---
type: Metric
title: Heat Pump Electrical Consumption & SCOP
description: Monitored electricity input, estimated thermal heat output, and seasonal coefficient of performance (SCOP).
tags: [heatpump, daikin, thermal, scop, kpi]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:pytest_kpi_presenter", at: "2026-09-29T00:00:00Z" }
sources:
  - id: daikin-altherma-specs
    resource: integrations/daikin/altherma.py
    title: Daikin Altherma 3 H HT 18kW Technical Data
    author: org:daikin
---

# Heat Pump Electrical Consumption & SCOP

## Definition
Tracks the total electrical energy consumed by the heat pump compressor and circulation pumps, mapped against the delivered thermal heat to space heating (CV) and domestic hot water (DHW).

## Seasonal Coefficient of Performance (SCOP)
The seasonal baseline efficiency multiplier is calibrated to:
$$\text{SCOP}_{\text{nominal}} = 3.65$$

Thermal heat yield is estimated as:
$$E_{\text{thermal}} = E_{\text{el}} \cdot \text{SCOP}$$

## Operating Constraints
- **Hydraulic Interlock:** Domestic Hot Water (DHW SG4) runs exclusively; space heating (CV) is deactivated during DHW production.
- **Modulation Floor:** ~950 W minimum electrical modulation when active.
- **SG Modes:**
  - `SG1`: Spitsblok (Forced Off during peak tariffs)
  - `SG2`: Normal weather-dependent curve
  - `SG3`: Pre-heat floor buffer (+1.0 °C)
  - `SG4`: DHW 60 °C boost run
