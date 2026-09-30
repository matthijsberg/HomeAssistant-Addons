---
type: Invariant
title: Forecast Page Three-Tier Segmentation
description: Architectural layout invariant grouping forecast dashboard visualizers into Finance, Energy, and Energy Storage sections.
tags: [architecture, ui, layout, forecast, invariant]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:browser_exec_qa", at: "2026-09-29T00:00:00Z" }
sources:
  - id: web-index-html
    resource: web/index.html
    title: Open HEMS Master WebUI Dashboard
    author: human:matthijs
---

# Forecast Page Three-Tier Segmentation Invariant

The Forecast & Prediction tab (`view-prediction`) is strictly segmented into three functional domains:

1. **Financiën & Markttarieven (Finance):**
   - Cost forecast chart (`costForecastChart`)
   - Dynamic EPEX electricity and solar export pricing (`electricityPricesChart`)
   - Price metrics (Min, Max, Peak Solar, Max Export)

2. **Energie & Vermogensbalans (Energy):**
   - Optimizer and solar surplus advice banners
   - Full 24h/48h power waterfall (`hemsChartAnalytics`): baseload, PV, DHW, CV, Battery, net grid line
   - 6-Box Energy metric summary

3. **Energie-opslag & Thermische Buffers (Storage):**
   - Storage Valuation Top Cards (DHW boilervat & Thuisbatterij buying value vs. live EPEX value)
   - DHW Operating Envelope Timeline and Trajectory
   - CV Space Heating Trajectory and Floor Buffer
   - Thuisaccu Operating Envelope Timeline and State of Charge Trajectory
