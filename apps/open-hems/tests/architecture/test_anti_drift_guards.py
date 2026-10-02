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

    assert not violations, "Entity isolation violations found:\n" + "\n".join(violations)


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


def test_dockerfile_and_requirements_dependency_parity():
    """
    Guardrail: Guarantee that all third-party imports in non-test runtime modules
    are strictly present in Dockerfile's 'apk add' package list and requirements.txt.
    Prevents silent packaging drift or missing binary dependencies at deployment.
    """
    import ast
    import sys

    repo_root = Path(__file__).parent.parent.parent
    dockerfile = repo_root / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile must exist"
    df_text = dockerfile.read_text(encoding="utf-8")

    # Extract apk add packages
    apk_lines = []
    in_apk = False
    for line in df_text.splitlines():
        if "apk add" in line:
            in_apk = True
            apk_lines.append(line)
        elif in_apk:
            apk_lines.append(line)
            if not line.strip().endswith("\\"):
                in_apk = False

    apk_block = " ".join(apk_lines)
    apk_pkgs = set(re.findall(r"\b([a-zA-Z0-9_-]+)\b", apk_block)) - {"RUN", "apk", "add", "no", "cache"}

    req_file = repo_root / "requirements.txt"
    assert req_file.exists(), "requirements.txt must exist"
    req_pkgs = {line.split(">=")[0].split("==")[0].strip().lower() for line in req_file.read_text().splitlines() if line.strip() and not line.startswith("#")}

    pkg_map = {
        "numpy": "py3-numpy",
        "yaml": "py3-yaml",
        "requests": "py3-requests",
        "aiohttp": "py3-aiohttp",
    }

    stdlib = sys.stdlib_module_names if hasattr(sys, "stdlib_module_names") else set()
    local_roots = {p.name for p in repo_root.iterdir() if p.is_dir()} | {p.stem for p in repo_root.glob("*.py")}

    runtime_dirs = ["api", "models", "layer1_data_collection", "layer2_calibration", "layer3_scheduling", "integrations", "site_adapters"]
    runtime_files = [repo_root / "daemon.py"]
    for d in runtime_dirs:
        dir_path = repo_root / d
        if dir_path.exists():
            runtime_files.extend(dir_path.rglob("*.py"))

    third_party = set()
    for py_file in runtime_files:
        if any(part in py_file.parts for part in ["tests", ".git", "__pycache__"]):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    pkg = a.name.split(".")[0]
                    if pkg not in stdlib and pkg not in local_roots:
                        third_party.add(pkg)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                pkg = node.module.split(".")[0]
                if pkg not in stdlib and pkg not in local_roots:
                    third_party.add(pkg)

    for pkg in third_party:
        expected_apk = pkg_map.get(pkg, f"py3-{pkg}")
        assert expected_apk in apk_pkgs, f"Third-party import '{pkg}' is missing from Dockerfile apk add ({expected_apk})"
        assert pkg.lower() in req_pkgs or (pkg.lower() == "yaml" and "pyyaml" in req_pkgs), f"Third-party import '{pkg}' is missing from requirements.txt"



