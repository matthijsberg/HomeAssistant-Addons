---
type: Metric
title: Storage Energy Valuation
description: Buying cost versus actual market replacement value for energy stored in DHW boilervat and home battery against live EPEX prices.
tags: [storage, valuation, dhw, battery, arbitrage, kpi]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:pytest_storage_valuation", at: "2026-09-29T00:00:00Z" }
sources:
  - id: epex-day-ahead
    resource: layer1_data_collection/collector.py
    title: EPEX Spot Hourly & 15m Tariffs
    author: org:energyzero
  - id: dhw-model
    resource: layer3_scheduling/storage_valuation.py
    title: Storage Valuation Engine
    author: human:matthijs
---

# Storage Energy Valuation (Opslagwaarde & Arbitrage)

## Overview
Computes the financial value of electrical and thermal energy stored in household storage media (DHW boiler and electrochemical home battery).

## 1. Domestic Hot Water (Boilervat 350L SWW)
- **Minimum Usable Threshold:**
  $$T_{\text{min}} = \text{setpoint} - 10.0\text{ }^\circ\text{C} = 40.0\text{ }^\circ\text{C}$$
- **Usable Temperature Lift:**
  $$\Delta T = \max(0.0, T_{\text{tank}} - 40.0\text{ }^\circ\text{C})$$
- **Thermal Energy Stored:**
  $$E_{\text{th}} = \frac{350 \cdot 1.163 \cdot \Delta T}{1000}\text{ kWh}_{\text{th}}$$
- **Electrical Equivalent:**
  $$E_{\text{el}} = \frac{E_{\text{th}}}{\text{COP}_{\text{dhw}}} \quad (\text{COP} \approx 3.1)$$
- **Valuation:**
  $$\text{Value}_{\text{buy}} = E_{\text{el}} \cdot p_{\text{charge, dhw}}$$
  $$\text{Value}_{\text{actual}} = E_{\text{el}} \cdot p_{\text{now}}$$
  $$\Delta \text{Value} = \text{Value}_{\text{actual}} - \text{Value}_{\text{buy}}$$

## 2. Thuisbatterij (15 kWh LFP)
- **Minimum Usable Threshold:** Minimum SoC $\text{SoC}_{\text{min}} = 10\%$ ($1.5\text{ kWh}$).
- **Usable Energy Stored:**
  $$E_{\text{usable}} = 15.0 \cdot \max(0.0, \text{SoC} - 0.10)\text{ kWh}$$
- **Valuation (Accounting for 90% Discharge Efficiency):**
  $$\text{Value}_{\text{buy}} = E_{\text{usable}} \cdot p_{\text{charge, bat}}$$
  $$\text{Value}_{\text{actual}} = E_{\text{usable}} \cdot 0.90 \cdot p_{\text{now}}$$
  $$\Delta \text{Value} = \text{Value}_{\text{actual}} - \text{Value}_{\text{buy}}$$
