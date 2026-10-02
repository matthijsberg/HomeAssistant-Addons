import os
import json
import pytest
import tempfile
from pathlib import Path

from harness.snapshot import is_path_denied, validate_target_path, compute_snapshot_id
from harness.egress import redact_secrets, create_egress_manifest, compute_manifest_hash
from harness.budget import check_and_update_budget, BudgetExceededError
from harness.jobs import init_db, create_job, get_job, update_job_status, list_jobs
from surface.extractor import extract_addon_config_surface, build_deployment_manifest


def test_deny_globs():
    assert is_path_denied("secrets.yaml", "/config/secrets.yaml") is True
    assert is_path_denied(".env", "/config/.env") is True
    assert is_path_denied("data/options.json", "/data/options.json") is True
    assert is_path_denied("main.py", "/addons/open-hems/main.py") is False


def test_secret_redaction():
    dummy_gh = "ghp_" + "1" * 36
    dummy_jwt = "eyJhbGciOi" + "J" * 20 + "." + "e30" + "." + "t-ID"
    text = f"const apiKey = '{dummy_gh}';\nconst token = '{dummy_jwt}';\n"
    clean, count = redact_secrets(text)
    assert count >= 2
    assert "ghp_" not in clean
    assert "[REDACTED_" in clean


def test_egress_manifest():
    manifest = create_egress_manifest(
        targets=["/addons/open-hems"],
        files_count=15,
        bytes_count=120000,
        redactions=2,
        models={"triage": {"model": "gemini-2.5-flash"}, "deep": {"model": "gemini-2.5-pro"}},
    )
    assert manifest["files_count"] == 15
    assert manifest["manifest_hash"] is not None
    assert manifest["estimated_cost_eur"] > 0


def test_budget_enforcement():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_p = Path(tmp.name)
        init_db(db_p)
        limits = {"max_tokens_per_job": 1000, "max_cost_eur_per_job": 1.0, "max_cost_eur_per_day": 5.0}

        # Should pass
        ok, _ = check_and_update_budget(db_p, "job_test", 200, 300, 0.2, limits)
        assert ok is True

        # Should fail on tokens
        with pytest.raises(BudgetExceededError):
            check_and_update_budget(db_p, "job_test", 800, 600, 0.2, limits)


def test_surface_extractor():
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        cfg = p / "config.yaml"
        cfg.write_text("name: Test\nhost_network: true\nmap: ['config:rw']\n")
        code = p / "app.py"
        code.write_text("@app.post('/api/control')\ndef control():\n    os.system('ls')\n")

        manifest = build_deployment_manifest(p)
        assert manifest["addon_config"]["host_network"] is True
        assert len(manifest["code_surface"]["routes"]) == 1
        assert len(manifest["cross_artifact_risks"]) >= 1
