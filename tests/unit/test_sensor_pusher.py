"""
Unit Tests for HomeAssistantSensorPusher
========================================
"""

import pytest
from unittest.mock import MagicMock, patch

from integrations.homeassistant.sensor_pusher import HomeAssistantSensorPusher
from models.canonical import CanonicalDispatchPlan, DispatchPlanSlot, DHWPlanSummary


def test_sensor_pusher_initialization():
    pusher = HomeAssistantSensorPusher("https://172.30.32.1:8123", "fake_token_123")
    assert pusher.ha_url == "https://172.30.32.1:8123"
    assert pusher.ha_token == "fake_token_123"


def test_sensor_pusher_payload_generation():
    """Verify that push_plan_sensors builds correct entity states and calls push_state."""
    pusher = HomeAssistantSensorPusher("https://172.30.32.1:8123", "fake_token")
    
    # Mock push_state so we inspect calls without real network traffic
    pusher.push_state = MagicMock(return_value=True)

    dummy_slots = [
        DispatchPlanSlot(
            slot_idx=i,
            time_label=f"12:{i*15:02d}",
            dt_iso=f"2026-09-17T12:{i*15:02d}:00+02:00",
            price_eur=0.20,
            solar_kw=2.0,
            unallocated_kw=0.5,
            heating_kw=0.0,
            dhw_kw=1.0 if i == 0 else 0.0,
            net_import_kw=-0.5,
            mode_code="normal",
            mode_label="Normaal",
            color_hex="#10B981",
            tailwind_class="text-emerald-400",
            description="Test slot"
        )
        for i in range(4)
    ]

    dummy_plan = CanonicalDispatchPlan(
        generated_at="2026-09-17T12:00:00+02:00",
        horizon_hours=1.0,
        resolution_mins=15,
        is_fresh=True,
        freshness_age_seconds=0.0,
        dynamic_peaks=[],
        slots=dummy_slots,
        dhw_summary=DHWPlanSummary(
            planned_mode="normal",
            planned_mode_label="Normaal",
            color_hex="#10B981",
            tailwind_class="text-emerald-400",
            target_temp_c=50.0,
            run_start="04:45",
            run_end="05:45",
            run_duration_min=60,
            power_kw=1.8,
            total_stroom_kwh=1.8,
            spits_lockout_hours=2.5,
            dynamic_peaks=[],
            unheated_trajectory=[],
            counterfactual_reason="",
            arbitrage_saving_eur=0.50
        )
    )

    res = pusher.push_plan_sensors(dummy_plan)
    assert len(res) == 7
    assert all(res.values())
    assert pusher.push_state.call_count == 7

    # Verify entity IDs pushed
    called_entities = [call[0][0] for call in pusher.push_state.call_args_list]
    assert "sensor.openhems_energy_cost_24h_forecast" in called_entities
    assert "sensor.openhems_energy_consumption_24h_forecast" in called_entities
    assert "sensor.openhems_solar_production_24h_forecast" in called_entities
    assert "sensor.openhems_hems_savings_24h_forecast" in called_entities
    assert "sensor.openhems_heatpump_dispatch_24h_forecast" in called_entities
    assert "sensor.openhems_dhw_target_temperature" in called_entities
    assert "sensor.openhems_dispatch_status" in called_entities
