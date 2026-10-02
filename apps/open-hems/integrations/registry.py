"""
Open HEMS: Hardware Adapter Registry & Device Resolution (REG1)
================================================================
Implements ADR-005. Provides explicit, type-safe registration of hardware
adapters, capability-based device resolution, and canonical device typing.

INVARIANT: No dynamic directory scanning, no magic imports, zero entity names.
"""

from typing import Dict, Any, List, Optional, Iterable
from integrations.interfaces import AdapterSpec, DeviceBinding, DeviceCapability
from models.canonical import DeviceType

# Canonical Type Vocabulary normalization map
CANONICAL_TYPE_ALIASES: Dict[str, str] = {
    "battery": DeviceType.HOME_BATTERY.value,
    "battery_storage": DeviceType.HOME_BATTERY.value,
    "home_battery": DeviceType.HOME_BATTERY.value,
    "solar": DeviceType.SOLAR_INVERTER.value,
    "solar_inverter": DeviceType.SOLAR_INVERTER.value,
    "rooftop_solar": DeviceType.SOLAR_INVERTER.value,
    "pv": DeviceType.SOLAR_INVERTER.value,
    "grid": DeviceType.GRID_METER.value,
    "grid_meter": DeviceType.GRID_METER.value,
    "main_grid_meter": DeviceType.GRID_METER.value,
    "heat_pump": DeviceType.HEAT_PUMP.value,
    "heatpump": DeviceType.HEAT_PUMP.value,
    "dhw": DeviceType.DHW_BOILER.value,
    "dhw_tank": DeviceType.DHW_BOILER.value,
    "dhw_boiler": DeviceType.DHW_BOILER.value,
    "water_heater": DeviceType.DHW_BOILER.value,
}


class AdapterRegistry:
    """Explicit registry of hardware adapters."""

    def __init__(self):
        self._adapters: Dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> None:
        """Registers an immutable adapter specification."""
        self._adapters[spec.slug] = spec

    def get(self, slug: str) -> Optional[AdapterSpec]:
        """Retrieves an adapter by slug."""
        return self._adapters.get(slug)

    def list_all(self) -> List[AdapterSpec]:
        """Lists all registered adapters."""
        return list(self._adapters.values())

    def find_adapter_for_device(self, dev_dict: Dict[str, Any]) -> Optional[AdapterSpec]:
        """
        Finds the matching AdapterSpec for a device dictionary.
        Priority:
          1. Explicit device['adapter']
          2. Matching supported_types against canonical device type
        """
        explicit_slug = dev_dict.get("adapter")
        if explicit_slug and explicit_slug in self._adapters:
            return self._adapters[explicit_slug]

        raw_type = dev_dict.get("type", "")
        norm_type = CANONICAL_TYPE_ALIASES.get(raw_type, raw_type)
        for spec in self._adapters.values():
            if norm_type in spec.supported_types or raw_type in spec.supported_types:
                return spec
        return None


# Global registry instance
GLOBAL_REGISTRY = AdapterRegistry()


def register_adapter(spec: AdapterSpec) -> None:
    GLOBAL_REGISTRY.register(spec)


def get_adapter(slug: str) -> Optional[AdapterSpec]:
    return GLOBAL_REGISTRY.get(slug)


def list_adapters() -> List[AdapterSpec]:
    return GLOBAL_REGISTRY.list_all()


def normalize_device_type(raw_type: str) -> str:
    """Normalizes any historical type alias to the canonical DeviceType value."""
    return CANONICAL_TYPE_ALIASES.get(raw_type, raw_type)


def bind_one(device_dict: Dict[str, Any]) -> DeviceBinding:
    """Binds a single configured device dict to an AdapterSpec."""
    dev_id = device_dict.get("id", "unknown")
    raw_type = device_dict.get("type", "unknown")
    norm_type = normalize_device_type(raw_type)
    spec = GLOBAL_REGISTRY.find_adapter_for_device(device_dict)

    # Device is active only if installed and enabled (both default to True if omitted)
    installed = device_dict.get("installed", True)
    enabled = device_dict.get("enabled", True)
    is_active = bool(installed and enabled)

    return DeviceBinding(
        device=device_dict,
        spec=spec,
        device_id=dev_id,
        device_type=norm_type,
        is_active=is_active
    )


def bind_all(cfg: Dict[str, Any]) -> List[DeviceBinding]:
    """Binds all devices declared in site_config / secrets_store."""
    devices = cfg.get("devices", [])
    if isinstance(devices, dict):
        devices = list(devices.values())
    return [bind_one(d) for d in devices if isinstance(d, dict)]


def by_capabilities(
    cfg: Dict[str, Any],
    caps: Iterable[DeviceCapability],
    *,
    active_only: bool = True
) -> List[DeviceBinding]:
    """
    Finds devices matching ALL given capabilities (conjunction).
    CRITICAL: Validates against the AdapterSpec capabilities (ground truth), not unvalidated YAML strings.

    A single capability is rarely a unique discriminator. A thermal buffer and an
    electrochemical battery both store energy; only the battery can also export.
    """
    required = frozenset(caps)
    if not required:
        return []
    res = []
    for b in bind_all(cfg):
        if active_only and not b.is_active:
            continue
        if b.spec and required.issubset(b.spec.capabilities):
            res.append(b)
    return res


def by_capability(cfg: Dict[str, Any], cap: DeviceCapability, *, active_only: bool = True) -> List[DeviceBinding]:
    """Finds devices matching a single functional capability."""
    return by_capabilities(cfg, [cap], active_only=active_only)


def by_type(cfg: Dict[str, Any], device_type: str, *, active_only: bool = True) -> List[DeviceBinding]:
    """Finds devices matching a canonical device type or alias."""
    norm_type = normalize_device_type(device_type)
    bindings = bind_all(cfg)
    res = []
    for b in bindings:
        if active_only and not b.is_active:
            continue
        if b.device_type == norm_type or b.device.get("type") == device_type:
            res.append(b)
    return res


def single_by_capabilities(cfg: Dict[str, Any], caps: Iterable[DeviceCapability]) -> Optional[DeviceBinding]:
    """
    Returns exactly one active device matching ALL capabilities, or None if not present.
    Raises ValueError if multiple active devices qualify.
    """
    required = frozenset(caps)
    matches = by_capabilities(cfg, required, active_only=True)
    if not matches:
        return None
    if len(matches) > 1:
        matched_ids = [m.device_id for m in matches]
        names = sorted(c.value for c in required)
        raise ValueError(f"Ambiguous device resolution: multiple active devices claim {names}: {matched_ids}")
    return matches[0]


def resolve_battery(cfg: Dict[str, Any]) -> Optional[DeviceBinding]:
    """
    Resolves the single electrochemical home battery, or None when none is configured.

    Discriminator is CAN_STORE and CAN_EXPORT together: a thermal buffer stores but
    cannot export, solar can export but cannot store. Only a battery does both.
    """
    return single_by_capabilities(cfg, {DeviceCapability.CAN_STORE, DeviceCapability.CAN_EXPORT})


def single_by_capability(cfg: Dict[str, Any], cap: DeviceCapability) -> Optional[DeviceBinding]:
    """
    Returns exactly one active device matching capability, or None if not present.
    Raises ValueError if multiple active devices claim the same exclusive capability.
    """
    matches = by_capability(cfg, cap, active_only=True)
    if not matches:
        return None
    if len(matches) > 1:
        matched_ids = [m.device_id for m in matches]
        raise ValueError(f"Ambiguous device resolution: multiple active devices claim {cap.value}: {matched_ids}")
    return matches[0]


# ---------------------------------------------------------------------------
# Initial Built-in Adapter Registrations
# ---------------------------------------------------------------------------

ADAPTER_DAIKIN_ALTHERMA = AdapterSpec(
    slug="daikin_altherma",
    supported_types=frozenset({"heat_pump", "thermal_buffer"}),
    capabilities=frozenset({
        DeviceCapability.IS_THERMAL,
        DeviceCapability.CAN_MODULATE,
        DeviceCapability.CAN_STORE,
        DeviceCapability.CAN_DELAY
    })
)

ADAPTER_GENERIC_HA_SENSOR = AdapterSpec(
    slug="generic_ha_sensor",
    supported_types=frozenset({"main_grid_meter", "rooftop_solar", "dhw_tank", "sensor"}),
    capabilities=frozenset({
        DeviceCapability.CAN_EXPORT
    })
)

ADAPTER_HOME_BATTERY = AdapterSpec(
    slug="home_battery",
    supported_types=frozenset({"home_battery", "battery", "battery_storage"}),
    capabilities=frozenset({
        DeviceCapability.CAN_STORE,
        DeviceCapability.CAN_EXPORT,
        DeviceCapability.CAN_MODULATE
    })
)

# Register default drivers
register_adapter(ADAPTER_DAIKIN_ALTHERMA)
register_adapter(ADAPTER_GENERIC_HA_SENSOR)
register_adapter(ADAPTER_HOME_BATTERY)
