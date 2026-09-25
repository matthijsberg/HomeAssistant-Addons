"""
Open HEMS: Hardware Integrations & Device Registry
==================================================
Exports the canonical interfaces, capabilities, adapter specifications,
and device registry.
"""

from integrations.interfaces import (
    DeviceCapability,
    SourceContext,
    DeviceCommand,
    ActuationResult,
    EffectiveState,
    ITelemetryReader,
    IDeviceActuator,
    IInterlock,
    AdapterSpec,
    DeviceBinding,
)

from integrations.registry import (
    GLOBAL_REGISTRY,
    register_adapter,
    get_adapter,
    list_adapters,
    normalize_device_type,
    bind_one,
    bind_all,
    by_capability,
    by_capabilities,
    by_type,
    single_by_capability,
    single_by_capabilities,
    resolve_battery,
    CANONICAL_TYPE_ALIASES,
)

__all__ = [
    "DeviceCapability",
    "SourceContext",
    "DeviceCommand",
    "ActuationResult",
    "EffectiveState",
    "ITelemetryReader",
    "IDeviceActuator",
    "IInterlock",
    "AdapterSpec",
    "DeviceBinding",
    "GLOBAL_REGISTRY",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "normalize_device_type",
    "bind_one",
    "bind_all",
    "by_capability",
    "by_capabilities",
    "by_type",
    "single_by_capability",
    "single_by_capabilities",
    "resolve_battery",
    "CANONICAL_TYPE_ALIASES",
]
