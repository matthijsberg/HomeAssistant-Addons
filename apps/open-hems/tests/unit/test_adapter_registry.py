"""
Unit Tests: Adapter Registry & Device Resolution (REG1)
=======================================================
Verifies registration, device binding, capability-based resolution,
type normalization, and error handling for ambiguous capabilities.
"""

import pytest
from integrations.interfaces import AdapterSpec, DeviceCapability
from integrations.registry import (
    bind_all,
    bind_one,
    by_capability,
    by_type,
    single_by_capability,
    normalize_device_type,
    GLOBAL_REGISTRY,
    get_adapter,
)


@pytest.fixture
def sample_site_config():
    return {
        "devices": [
            {
                "id": "heatpump_daikin",
                "type": "heat_pump",
                "adapter": "daikin_altherma",
                "installed": True,
                "enabled": True,
            },
            {
                "id": "solar_inepro",
                "type": "pv",
                "adapter": "generic_ha_sensor",
                "installed": True,
                "enabled": True,
            },
            {
                "id": "battery_deye",
                "type": "home_battery",
                "adapter": "home_battery",
                "installed": True,
                "enabled": False,  # Disabled
            },
            {
                "id": "grid_meter_p1",
                "type": "grid_meter",
                "installed": True,
                "enabled": True,
            }
        ]
    }


def test_type_normalization():
    assert normalize_device_type("battery") == "home_battery"
    assert normalize_device_type("battery_storage") == "home_battery"
    assert normalize_device_type("pv") == "solar_inverter"
    assert normalize_device_type("solar") == "solar_inverter"
    assert normalize_device_type("dhw") == "dhw_boiler"
    assert normalize_device_type("heatpump") == "heat_pump"


def test_built_in_adapters_registered():
    assert get_adapter("daikin_altherma") is not None
    assert get_adapter("generic_ha_sensor") is not None
    assert get_adapter("home_battery") is not None


def test_bind_all(sample_site_config):
    bindings = bind_all(sample_site_config)
    assert len(bindings) == 4

    hp = [b for b in bindings if b.device_id == "heatpump_daikin"][0]
    assert hp.is_active is True
    assert hp.spec is not None
    assert hp.spec.slug == "daikin_altherma"
    assert DeviceCapability.IS_THERMAL in hp.spec.capabilities

    bat = [b for b in bindings if b.device_id == "battery_deye"][0]
    assert bat.is_active is False
    assert bat.device_type == "home_battery"


def test_by_capability(sample_site_config):
    thermal = by_capability(sample_site_config, DeviceCapability.IS_THERMAL)
    assert len(thermal) == 1
    assert thermal[0].device_id == "heatpump_daikin"

    # Battery has CAN_STORE, but is enabled=False, so active_only should exclude it
    active_storage = by_capability(sample_site_config, DeviceCapability.CAN_STORE, active_only=True)
    # Heatpump also has CAN_STORE (thermal storage in floor buffer)
    assert len(active_storage) == 1
    assert active_storage[0].device_id == "heatpump_daikin"

    all_storage = by_capability(sample_site_config, DeviceCapability.CAN_STORE, active_only=False)
    assert len(all_storage) == 2


def test_single_by_capability(sample_site_config):
    hp = single_by_capability(sample_site_config, DeviceCapability.IS_THERMAL)
    assert hp is not None
    assert hp.device_id == "heatpump_daikin"

    # Deadline capability has no matching device
    assert single_by_capability(sample_site_config, DeviceCapability.HAS_DEADLINE) is None


def test_single_by_capability_raises_on_ambiguity():
    ambiguous_cfg = {
        "devices": [
            {"id": "hp1", "type": "heat_pump", "adapter": "daikin_altherma", "enabled": True},
            {"id": "hp2", "type": "heat_pump", "adapter": "daikin_altherma", "enabled": True},
        ]
    }
    with pytest.raises(ValueError, match="Ambiguous device resolution"):
        single_by_capability(ambiguous_cfg, DeviceCapability.IS_THERMAL)
