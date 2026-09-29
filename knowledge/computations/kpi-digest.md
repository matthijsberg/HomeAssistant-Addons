---
type: Attested Computation
title: KPI Presenter Digest Computation
description: Sanctioned deterministic computation of summary KPI cards and detail breakdowns for Open HEMS dashboards.
tags: [computation, kpi, presenter, deterministic]
status: stable
runtime: python
parameters:
  - { name: timeframe, type: string, required: true }
  - { name: interval, type: string, required: true }
executor:
  resource: layer5_analytics/kpi_presenter.py
  receipt: [total_import_kwh, total_export_kwh, net_cost_eur, card_items]
attester:
  resource: tests/unit/test_kpi_presenter.py
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:pytest_kpi_presenter", at: "2026-09-29T00:00:00Z" }
sources:
  - id: kpi-presenter-code
    resource: layer5_analytics/kpi_presenter.py
    title: Clean Architecture Layer 5 Presenter
    author: human:matthijs
---

# Computation

The sanctioned computation executes inside `layer5_analytics/kpi_presenter.py` and produces immutable `KpiCardItem` and `KpiBreakdownItem` records:

```python
kpis = KpiPresenter.build_forecast_kpis(
    unallocated=unallocated_kw,
    boiler=boiler_kw,
    heating=heating_kw,
    solar=solar_kw,
    prices=prices_eur_per_kwh,
    step_h=0.25,
    plan=dispatch_plan,
    ...
)
```

## Parameter Constraints
- `timeframe`: Must match one of `['1h', '6h', 'today', '24h', '48h', '7d']`.
- `interval`: Must be either `'15m'` or `'1h'`.
- All emitted breakdown arrays must contain at least 2 distinct explanatory line items.
