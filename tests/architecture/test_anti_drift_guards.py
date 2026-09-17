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
from models.canonical import StandardizedState, get_state_metadata, Quality


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
    """Verify that every StandardizedState is fully mapped via get_state_metadata()."""
    meta_all = get_state_metadata()
    for state in StandardizedState:
        assert state in meta_all
        meta = get_state_metadata(state)
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


def test_mode_color_hex_guardrail():
    """
    Ensure models/*.py has ZERO hardcoded hex color codes (all colors live in config/mode_catalog.json).
    Ensure web/js/app.js does NOT contain standardized state mode hex codes outside OpenHEMSModeCatalog.
    """
    repo_root = Path(__file__).parent.parent.parent
    hex_pattern = re.compile(r"#[0-9A-Fa-f]{6}")

    # 1. Models layer must have ZERO hex literals
    models_dir = repo_root / "models"
    for py_file in models_dir.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        matches = hex_pattern.findall(text)
        assert not matches, f"{py_file.name} contains hardcoded hex colors: {matches}. Colors belong in config/mode_catalog.json."

    # 2. web/js/app.js must not define mode hex codes outside OpenHEMSModeCatalog
    app_js = repo_root / "web" / "js" / "app.js"
    assert app_js.exists()
    js_text = app_js.read_text(encoding="utf-8")

    # Locate OpenHEMSModeCatalog
    catalog_match = re.search(r"const\s+OpenHEMSModeCatalog\s*=\s*\{[\s\S]*?\n\s*\};\s*//\s*Auto-fetch", js_text)
    assert catalog_match, "OpenHEMSModeCatalog definition not found in app.js"

    outside_catalog = js_text[:catalog_match.start()] + js_text[catalog_match.end():]

    # Mode background color assignments must come from OpenHEMSModeCatalog, not hardcoded strings
    forbidden_mode_hexes = [
        r"style\.backgroundColor\s*=\s*['\"]#(?:EF4444|F59E0B|10B981|A855F7|4ADE80|1E293B)['\"]",
    ]
    for pattern in forbidden_mode_hexes:
        matches = re.findall(pattern, outside_catalog)
        assert not matches, f"Hardcoded mode background colors found outside OpenHEMSModeCatalog: {matches}"


