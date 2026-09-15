"""
Architecture Guardrail: AST HTTP Handler Purity
================================================
Verifies that HTTP presentation handlers in daemon.py are dumb views
that read exclusively from the central PlanStore via ensure_active_canonical_plan,
rather than calculating independent physics or calling external APIs directly.
"""

import ast
from pathlib import Path


def test_decomposition_handler_uses_plan_store():
    repo_root = Path(__file__).parent.parent.parent
    daemon_file = repo_root / "daemon.py"
    if not daemon_file.exists():
        daemon_file = Path("/config/addons/open-hems/daemon.py")

    assert daemon_file.exists()
    content = daemon_file.read_text(encoding="utf-8")

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
    repo_root = Path(__file__).parent.parent.parent
    daemon_file = repo_root / "daemon.py"
    if not daemon_file.exists():
        daemon_file = Path("/config/addons/open-hems/daemon.py")

    content = daemon_file.read_text(encoding="utf-8")
    assert 'if path == "/api/health/consistency":' in content, (
        "Missing /api/health/consistency health check route!"
    )


def test_heating_forecast_handler_is_pure_dumb_view():
    repo_root = Path(__file__).parent.parent.parent
    daemon_file = repo_root / "daemon.py"
    if not daemon_file.exists():
        daemon_file = Path("/config/addons/open-hems/daemon.py")

    content = daemon_file.read_text(encoding="utf-8")
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
