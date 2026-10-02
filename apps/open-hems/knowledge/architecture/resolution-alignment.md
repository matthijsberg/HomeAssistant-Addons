---
type: Invariant
title: Resolution Alignment & Downsampling Guardrail
description: Guarantees calendar alignment and prevents destructive downsampling when switching between 15-minute and 1-hour intervals.
tags: [architecture, ui, resolution, guardrail]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:pytest_historical_overlay_ranges", at: "2026-09-29T00:00:00Z" }
sources:
  - id: charts-engine
    resource: web/js/modules/charts_thermal.js
    title: Open HEMS Chart Engine
    author: human:matthijs
---

# Resolution Alignment & Downsampling Guardrail

## Temporal Parity Rule
Switching between **15-Minute** and **1-Hour** resolution in charts MUST keep peak price blocks, smart grid overlay windows (`SG1`, `SG3`, `SG4`), and thermal heating runs at the exact same physical clock hours.

## Downsampling Guardrail
- **No Blind Division:** Never divide an array index blindly by 4 (`idx // 4`) without first checking whether the source array is already hourly (`len <= 48` for a 48h range).
- **Integral Aggregation:** When rolling up 15m power ($kW$) to 1h energy ($kWh$), the mean power over the 4 quarters must equal the hourly energy sum:
  $$E_{1h} = \frac{1}{4} \sum_{q=0}^{3} P_{15m, q}$$
