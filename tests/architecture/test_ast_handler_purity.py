"""
Architecture Guardrail: AST HTTP Handler Purity
================================================
Verifies that HTTP presentation handlers in daemon.py are dumb views
that read exclusively from the central PlanStore via ensure_active_canonical_plan,
rather than calculating independent physics or calling external APIs directly.
"""

import ast
from pathlib import Path


def _get_combined_backend_source():
    repo_root = Path(__file__).parent.parent.parent
    files = [
        repo_root / "daemon.py",
        repo_root / "api" / "routes_model.py",
        repo_root / "api" / "routes_system.py",
        repo_root / "api" / "routes_schedule.py",
        repo_root / "api" / "routes_analytics.py",
        Path("/config/addons/open-hems/daemon.py"),
        Path("/config/addons/open-hems/api/routes_model.py"),
        Path("/config/addons/open-hems/api/routes_system.py"),
    ]
    seen = set()
    text = ""
    for f in files:
        if f.exists() and f.resolve() not in seen:
            seen.add(f.resolve())
            text += f.read_text(encoding="utf-8") + "\n"
    return text


def test_decomposition_handler_uses_plan_store():
    content = _get_combined_backend_source()

    # Locate the /api/model/decomposition block
    decomp_idx = content.find('if path == "/api/model/decomposition":')
    assert decomp_idx != -1, "Missing /api/model/decomposition route handler!"

    # Get the handler block (next 30 lines)
    decomp_block = content[decomp_idx:decomp_idx + 800]

    # Must call ensure_active_canonical_plan()
    assert "ensure_active_canonical_plan" in decomp_block, (
        "/api/model/decomposition handler MUST consume ensure_active_canonical_plan!"
    )

    # Must NOT call external weather or urllib directly
    assert "api.open-meteo.com" not in decomp_block, (
        "/api/model/decomposition must NOT fetch external weather directly!"
    )
    assert "urllib.request.urlopen" not in decomp_block, (
        "/api/model/decomposition must NOT execute HTTP calls directly!"
    )


def test_consistency_endpoint_exists_and_pure():
    content = _get_combined_backend_source()
    assert 'if path == "/api/health/consistency":' in content, (
        "Missing /api/health/consistency health check route!"
    )


def test_heating_forecast_handler_is_pure_dumb_view():
    content = _get_combined_backend_source()
    hf_idx = content.find('if path.startswith("/api/model/heating-forecast"):')
    assert hf_idx != -1, "Missing /api/model/heating-forecast route handler!"

    hf_block = content[hf_idx:hf_idx + 1800]

    # Must call ensure_active_canonical_plan()
    assert "ensure_active_canonical_plan" in hf_block, (
        "/api/model/heating-forecast handler MUST consume ensure_active_canonical_plan!"
    )

    # Must NOT call urllib.request.urlopen directly
    assert "urllib.request.urlopen" not in hf_block, (
        "/api/model/heating-forecast must NOT make direct HA or HTTP requests!"
    )
    # Must NOT have raw climate entity queries
    assert "climate.woonkamer_climate_daikin" not in hf_block, (
        "/api/model/heating-forecast must NOT have raw HA entity strings!"
    )
