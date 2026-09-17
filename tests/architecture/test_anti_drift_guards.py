"""
Automated CI Architectural Guardrail Tests
==========================================
Enforces the 4 invariant principles of Open HEMS:
1. Entity Isolation: Core modules must not contain hardcoded HA entity IDs.
2. Immutability & Vector Uniformity: Canonical dispatch plan slots are contract-bound.
3. State Taxonomy Invariance: Every state has a single authoritative representation.
"""

import pytest
import re
from pathlib import Path
from models.canonical import StandardizedState, STATE_METADATA, Quality


def test_core_entity_isolation():
    """
    Ensure core modules do NOT contain hardcoded HA entity names.
    Hardware entities belong strictly in site_adapters/ or site_config.json.
    """
    repo_root = Path(__file__).parent.parent.parent
    core_dirs = [
        repo_root / "layer1_data_collection" / "sanitizer.py",
        repo_root / "layer2_calibration" / "learned_forecaster.py",
        repo_root / "layer3_scheduling" / "central_planner.py",
        repo_root / "layer3_scheduling" / "plan_store.py",
        repo_root / "models" / "canonical.py"
    ]

    forbidden_patterns = [
        r"sensor\.hc_dhw_temperature",
        r"switch\.hc_mode_altherma_on",
        r"climate\.woonkamer_climate",
        r"switch\.sg_relais"
    ]

    violations = []
    for file_path in core_dirs:
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if re.search(pattern, text):
                violations.append(f"{file_path.name} violates entity isolation: matched '{pattern}'")

    assert not violations, f"Entity isolation violations found:\n" + "\n".join(violations)


def test_standardized_taxonomy_completeness():
    """Verify that every StandardizedState is fully mapped in STATE_METADATA."""
    for state in StandardizedState:
        assert state in STATE_METADATA
        meta = STATE_METADATA[state]
        assert "code" in meta
        assert "label" in meta
        assert "color_hex" in meta
        assert meta["color_hex"].startswith("#")
        assert len(meta["color_hex"]) == 7


def test_unidirectional_layer_architecture():
    """
    Enforce unidirectional architecture (Laag 1 -> 2 -> 3 -> 4/5):
    - layer1_data_collection/ and layer2_calibration/ must NEVER import
      from layer3_scheduling or layer4_control.
    """
    repo_root = Path(__file__).parent.parent.parent
    prohibited_imports = [
        r"from\s+layer3_scheduling",
        r"import\s+layer3_scheduling",
        r"from\s+layer4_control",
        r"import\s+layer4_control",
    ]

    scanned_dirs = [
        repo_root / "layer1_data_collection",
        repo_root / "layer2_calibration",
    ]

    violations = []
    for directory in scanned_dirs:
        if not directory.exists():
            continue
        for py_file in directory.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for pattern in prohibited_imports:
                for match in re.finditer(pattern, text):
                    line_no = text[:match.start()].count("\n") + 1
                    violations.append(
                        f"{py_file.relative_to(repo_root)}:{line_no} violates unidirectional architecture: matched '{pattern}'"
                    )

    assert not violations, "Unidirectional architecture violations found:\n" + "\n".join(violations)

