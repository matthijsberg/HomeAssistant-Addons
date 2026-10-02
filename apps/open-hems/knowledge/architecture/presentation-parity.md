---
type: Invariant
title: Presentation Parity Invariant
description: Strict 1:1 synchronization rule between UI overview summary cards and their modal detail breakdown views.
tags: [architecture, frontend, contract, invariant]
status: stable
generated: { by: "human:matthijs", at: "2026-09-29T00:00:00Z" }
verified:
  - { by: "process:browser_exec_qa", at: "2026-09-29T00:00:00Z" }
sources:
  - id: web-app-js
    resource: web/js/app.js
    title: Open HEMS Master Frontend Controller
    author: human:matthijs
---

# Presentation Parity Invariant

## Core Rule
Whenever an overview card displays aggregate financial or physical numbers (`costs`, `solar`, `savings`, `heatpump`), clicking that card to reveal its detail popup modal (`openKpiDetailModal`) MUST display the exact same numbers, timeframe context, units, and signs.

## Disallowed Anti-Patterns
1. **Asynchronous Context Drift:** Overview cards updating to dynamic timeframes (`24h`, `48h`, `7d`) while the modal datastore remains pinned to `today` (midnight-to-now).
2. **Negative Zero Artifacts:** Displaying `-0.0 kWh` or `-€0.00` when numbers are within floating-point rounding proximity of zero. All values $|v| < 0.05$ must render as `0.0`.
3. **Hardcoded Subtitles:** Presenting subtitles like `Kosten Vandaag` inside a modal when the active filter is `24 Uur` or `48 Uur`.
