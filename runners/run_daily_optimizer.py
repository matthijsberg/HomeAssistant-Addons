#!/usr/bin/env python3
"""
Runner: Daily HEMS Optimization & Scheduling
============================================
Fires 4x daily (06:30, 11:30, 14:30, 18:30) via Cronjob 043a4b36fff1.
Workflow:
  1. Refreshes Layer 1 Data Collector (EPEX spot prices & Open-Meteo weather).
  2. Solves Layer 3 Power Slotting schedule (Baseload -> DHW -> CV -> Battery -> Export).
  3. Writes time-series to InfluxDB ('hermes' database).
  4. Audits & syncs day/week totals to Google Sheets ('HEMS_Planning_En_Prijzen').
  5. Updates Home Assistant entities & 24-hour interactive Markdown dashboard card.
"""

import os
import sys

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, "/config/lib")

from layer3_scheduling.scheduler import main as run_scheduler

if __name__ == "__main__":
    run_scheduler()
