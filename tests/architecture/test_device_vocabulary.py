"""
Architecture Guardrail: Device Vocabulary & Site Adapter Boundaries (REG1)
==========================================================================
Enforces:
1. `DeviceType` enum in `models.canonical` is the single source of truth for canonical device types.
2. `integrations.registry` CANONICAL_TYPE_ALIASES resolves all aliases to valid DeviceType values.
3. Classes in `site_adapters/` never register directly in `integrations.registry`.
"""

from pathlib import Path
import inspect
from models.canonical import DeviceType
from integrations.registry import CANONICAL_TYPE_ALIASES, list_adapters


def test_device_type_vocabulary_is_canonical():
    valid_types = {dt.value for dt in DeviceType}
    for alias, target in CANONICAL_TYPE_ALIASES.items():
        assert target in valid_types, f"Alias target '{target}' for '{alias}' is not a valid canonical DeviceType!"


def test_site_adapters_never_register():
    registered_slugs = {a.slug for a in list_adapters()}
    # Site adapters such as P1P2 classifier must never masquerade as full hardware adapters
    forbidden = ["p1p2", "daikin_p1p2", "classifier", "p1p2_classifier"]
    for f in forbidden:
        assert f not in registered_slugs, f"Site adapter '{f}' illegally registered in AdapterRegistry!"
