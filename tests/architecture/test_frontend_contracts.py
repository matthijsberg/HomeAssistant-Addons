"""
Architectural Guardrail: Frontend Dataset Contract Invariance
============================================================
Enforces that chart datasets and tooltips use immutable semantic `id` contracts
rather than fragile human-readable label matching.
"""

from pathlib import Path
import re
import pytest

APP_JS_PATH = Path(__file__).resolve().parent.parent.parent / "web" / "js" / "app.js"


def test_zero_fragile_label_matching_in_frontend():
    """Asserts that no code in web/js/app.js checks dataset.label.includes or indexOf."""
    assert APP_JS_PATH.exists(), f"Frontend script missing at {APP_JS_PATH}"
    code = APP_JS_PATH.read_text(encoding="utf-8")

    # Match patterns like: ds.label.includes, dataset.label.indexOf, d.label.includes
    fragile_matches = re.findall(r'(\b\w+\.label\.(?:includes|indexOf)\b)', code)
    assert not fragile_matches, (
        f"Found {len(fragile_matches)} fragile label string-matching calls in web/js/app.js: {fragile_matches}. "
        "Use typed dataset.id contracts instead."
    )


def test_key_datasets_have_semantic_ids():
    """Verifies that critical datasets define immutable `id` attributes."""
    code = APP_JS_PATH.read_text(encoding="utf-8")
    
    required_ids = [
        "solar_forecast",
        "epex_import",
        "epex_export",
        "dhw_p50",
        "dhw_unh_p50",
        "dhw_demand",
        "outdoor_temp",
        "indoor_temp"
    ]
    for req_id in required_ids:
        assert f"id: '{req_id}'" in code or f'id: "{req_id}"' in code, (
            f"Required semantic dataset id '{req_id}' not found in web/js/app.js"
        )
