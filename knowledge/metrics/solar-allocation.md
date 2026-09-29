---
type: Metric
title: Solar Production & Allocation
description: Accounting of rooftop solar generation partitioned into direct self-consumption and exported grid feed-in.
tags: [solar, generation, self-consumption, kpi]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:pytest_kpi_presenter", at: "2026-09-29T00:00:00Z" }
sources:
  - id: inepro-103-meter
    resource: integrations/inepro/modbus.py
    title: Inepro Pro1-Modbus Zonne-omvormer Meter
    author: human:matthijs
---

# Solar Production & Allocation (Zonnepanelen Opbrengst)

## Definition
Measures gross rooftop solar generation and attributes value according to whether the electricity was consumed locally behind the meter or exported to the grid.

## Sub-components
1. **Direct Self-Consumption (Direct Eigen Verbruik):**
   Solar energy immediately consumed by home baseload, heat pump, or battery charging:
   $$E_{\text{self}} = \max\left(0, E_{\text{solar}} - E_{\text{export}}\right)$$
   Valued at the avoided retail grid purchase price:
   $$\text{Value}_{\text{self}} = E_{\text{self}} \cdot p_{\text{buy}}$$

2. **Grid Export (Teruglevering aan het Net):**
   Surplus solar power pushed into the distribution grid:
   $$\text{Value}_{\text{export}} = E_{\text{export}} \cdot p_{\text{sell}}$$

3. **Total Solar Value (Totale Zonne-opbrengst):**
   $$\text{Value}_{\text{total}} = \text{Value}_{\text{self}} + \text{Value}_{\text{export}}$$

## Polarity Convention
In power charts, generation is rendered with negative polarity (or dedicated downward stacked bars) to distinguish it from load consumption. In financial summaries, avoided cost and revenues are positive values contributing to household savings.
