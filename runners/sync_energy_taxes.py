#!/usr/bin/env python3
"""
Runner: Periodic Energy Tax Sync & Alerting
===========================================
Detects official Dutch electricity tax rate from EnergyZero stream (all_in_with_vat - base_with_vat),
updates Home Assistant input_number.dynamic_energy_tax_electricity, and delivers Telegram notifications
if a statutory tax rate adjustment occurs.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, "/config/lib")

from layer1_data_collection.collector import EnergyDataCollector
from heatpump_core import HeatPumpCore
from datetime import datetime

def main():
    core = HeatPumpCore()
    collector = EnergyDataCollector()
    today = datetime.now().date()
    
    print("--- CHECKING STATUTORY ENERGY TAX RATES ---")
    prices = collector.fetch_market_prices(today, force_refresh=True)
    detected_tax = prices.get("tax_delta")
    
    if detected_tax is not None:
        print(f"Detected Official Tax Rate: €{detected_tax:.5f}/kWh")
        current_ha_tax = core.get_dynamic_tariffs()["tax_electricity"]
        print(f"Current Home Assistant Tax:  €{current_ha_tax:.5f}/kWh")
        
        if abs(detected_tax - current_ha_tax) > 0.0005:
            print(f"Tax rate adjustment detected! Updating HA to €{detected_tax:.5f}/kWh...")
            core.publish_ha_state("input_number.dynamic_energy_tax_electricity", detected_tax)
            # Deliver notification
            msg = f"⚡ De officiële energiebelasting op elektriciteit is gewijzigd van €{current_ha_tax:.4f} naar €{detected_tax:.4f}/kWh incl. btw."
            core.send_telegram_notification(msg)
        else:
            print("Tax rate matches Home Assistant. No changes needed.")
    else:
        print("Warning: Could not detect tax delta from market stream.")

if __name__ == "__main__":
    main()
