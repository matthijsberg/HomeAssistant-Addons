"""
Unit Tests for Open HEMS Device Library & Profile Contracts
===========================================================
Verifies:
1. All JSON templates in config/device_library/ load without syntax errors.
2. Every declared capability belongs to canonical DeviceCapability enum in integrations.interfaces.
3. Physical specifications conform to non-negative numerical invariants.
4. Quirk blocks remain decoupled from generic capabilities.
"""

import json
from pathlib import Path
import pytest
from integrations.interfaces import DeviceCapability


LIBRARY_DIR = Path(__file__).parent.parent.parent / "config" / "device_library"
SCHEMA_PATH = Path(__file__).parent.parent.parent / "schema" / "device_profile.schema.json"


def test_schema_file_exists_and_is_valid_json():
    assert SCHEMA_PATH.exists(), f"Missing schema at {SCHEMA_PATH}"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema.get("title") == "OpenHEMSDeviceProfile"
    assert "properties" in schema


def test_all_device_profiles_conform_to_capabilities_enum():
    assert LIBRARY_DIR.exists(), f"Missing library dir at {LIBRARY_DIR}"
    profile_files = list(LIBRARY_DIR.glob("*.json"))
    assert len(profile_files) >= 4, f"Expected at least 4 profiles, found {len(profile_files)}"

    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    canonical_caps = {c.value for c in DeviceCapability}

    for p_file in profile_files:
        profile = json.loads(p_file.read_text(encoding="utf-8"))
        # Formal JSON Schema validation
        jsonschema.validate(instance=profile, schema=schema)

        assert "profile_id" in profile, f"{p_file.name} missing profile_id"
        assert "capabilities" in profile, f"{p_file.name} missing capabilities"
        assert isinstance(profile["capabilities"], list)

        # Invariant: Every capability declared in a template must be in DeviceCapability
        for cap in profile["capabilities"]:
            assert cap in canonical_caps, (
                f"Profile '{profile['profile_id']}' declares invalid capability '{cap}'! "
                f"Valid capabilities: {canonical_caps}"
            )

        # Invariant: physical_specs must contain valid numbers
        specs = profile.get("physical_specs", {})
        assert isinstance(specs, dict)
        for k, v in specs.items():
            if isinstance(v, (int, float)):
                assert v >= 0, f"Specification '{k}' in '{profile['profile_id']}' cannot be negative (got {v})"


def test_quirks_isolation_rule():
    """Guarantees that manufacturer-specific quirks are isolated in the 'quirks' block."""
    profile_files = list(LIBRARY_DIR.glob("*.json"))
    for p_file in profile_files:
        profile = json.loads(p_file.read_text(encoding="utf-8"))
        if "quirks" in profile:
            assert isinstance(profile["quirks"], dict)
            # Physical specs must not leak manufacturer brand names
            specs_str = json.dumps(profile.get("physical_specs", {})).lower()
            assert "daikin" not in specs_str, f"Brand name leaked into generic physical_specs of {p_file.name}"
            assert "deye" not in specs_str, f"Brand name leaked into generic physical_specs of {p_file.name}"
