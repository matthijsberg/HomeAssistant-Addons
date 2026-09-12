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
