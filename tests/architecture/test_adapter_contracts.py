"""
Architecture Guardrail: Adapter Registry Contracts & Isolation (REG0)
=====================================================================
Enforces:
1. `integrations.interfaces` contains zero Home Assistant entity strings.
2. `integrations.interfaces` does not import from higher layers (layer3, layer4, daemon).
3. ADR-002, ADR-003, and ADR-005 exist and reference the implementation plan.
"""

from pathlib import Path
import inspect
import integrations.interfaces as ifaces

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_interfaces_have_no_entity_strings():
    source_path = Path(inspect.getfile(ifaces))
    content = source_path.read_text(encoding="utf-8")
    for forbidden in ["sensor.", "climate.", "switch.", "input_boolean.", "binary_sensor."]:
        assert forbidden not in content, f"Forbidden entity prefix '{forbidden}' leaked into integrations.interfaces!"


def test_interfaces_import_nothing_from_higher_layers():
    source_path = Path(inspect.getfile(ifaces))
    content = source_path.read_text(encoding="utf-8")
    for forbidden in ["layer3_scheduling", "layer4_control", "layer5_analytics", "daemon", "integrations.daikin"]:
        assert forbidden not in content, f"Forbidden upward import '{forbidden}' found in integrations.interfaces!"


def test_adrs_reference_implementation_plan():
    adr2 = (REPO_ROOT / "docs" / "adr" / "ADR-002-device-agnostic-dispatch-contract.md").read_text(encoding="utf-8")
    adr3 = (REPO_ROOT / "docs" / "adr" / "ADR-003-ha-as-adapter-plugin.md").read_text(encoding="utf-8")
    adr5 = (REPO_ROOT / "docs" / "adr" / "ADR-005-adapter-registry.md").read_text(encoding="utf-8")

    assert "docs/plans/PLAN-adapter-register.md" in adr2
    assert "docs/plans/PLAN-adapter-register.md" in adr3
    assert "docs/plans/PLAN-adapter-register.md" in adr5
