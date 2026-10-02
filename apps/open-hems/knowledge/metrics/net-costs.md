---
type: Metric
title: Net Energy Cost
description: Realized or predicted net energy cost in EUR based on grid import expenditure minus export revenue.
tags: [finance, tariffs, grid, kpi]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:pytest_kpi_presenter", at: "2026-09-29T00:00:00Z" }
sources:
  - id: powerpeers-contract
    resource: https://www.powerpeers.nl
    title: Dynamic Energy Contract Terms
    author: org:powerpeers
---

# Net Energy Cost (Netto Kosten)

## Definition
Net Energy Cost represents the total financial balance of electricity consumed from and delivered to the electrical grid over a given time window (e.g. 15 minutes, 24 hours, or 48 hours).

## Polarity Convention
- **Grid Import ($P > 0$):** Electricity drawn from the grid is positive. Incurs dynamic all-in import tariff ($p_{\text{buy}}$).
- **Grid Export ($P < 0$):** Excess generation delivered to the grid is negative. Yields net feed-in compensation ($p_{\text{sell}}$).

## Formula
$$\text{NetCost}_{\text{EUR}} = \sum_{t} \left( E_{\text{import}, t} \cdot p_{\text{buy}, t} - E_{\text{export}, t} \cdot p_{\text{sell}, t} \right)$$

Where:
- $E_{\text{import}, t} = \max(0, P_{\text{grid}, t}) \cdot \Delta t$
- $E_{\text{export}, t} = \max(0, -P_{\text{grid}, t}) \cdot \Delta t$

## UI Invariant
Every view presenting Net Energy Cost must display the net monetary value alongside the gross physical energy flows:
`€X.XX (Y.Y kWh afname · Z.Z kWh retour)`
