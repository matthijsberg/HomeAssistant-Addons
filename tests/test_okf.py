import tempfile
from pathlib import Path
import pytest

from harness.okf import (
    parse_okf_markdown,
    record_okf_concept,
    query_okf_concepts,
    export_okf_bundle,
    import_okf_bundle,
    query_security_guidance,
)


def test_parse_okf_markdown_human():
    doc = """---
type: Component Entity
title: Authentication Module
resource: src/auth/jwt.py
tags: [auth, jwt]
verified:
  - by: human:matthijs
    at: 2026-09-28T12:00:00Z
---
Validates JWT tokens before allowing API dispatch.
"""
    parsed = parse_okf_markdown(doc, default_concept_id="entities/auth.md")
    assert parsed is not None
    assert parsed["type"] == "Component Entity"
    assert parsed["title"] == "Authentication Module"
    assert parsed["resource"] == "src/auth/jwt.py"
    assert parsed["trust_tier"] == "human_reviewed"
    assert "jwt" in parsed["tags"]
    assert "Validates JWT tokens" in parsed["body_markdown"]


def test_parse_okf_markdown_machine():
    doc = """---
type: Threat Boundary
title: Home Assistant Ingress Boundary
resource: api/routes.py
verified:
  - by: machine:semgrep-runner
    at: 2026-09-28T12:00:00Z
---
All routes behind /api/ require valid Ingress session token.
"""
    parsed = parse_okf_markdown(doc)
    assert parsed["trust_tier"] == "machine_confirmed"


def test_okf_crud_and_bundle_exchange():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db, tempfile.TemporaryDirectory() as tmp_export, tempfile.NamedTemporaryFile(suffix=".db") as tmp_db2:
        db_path = Path(tmp_db.name)
        export_dir = Path(tmp_export)
        db_path2 = Path(tmp_db2.name)

        # 1. Record concept
        concept = {
            "concept_id": "inv_no_root",
            "type": "Security Invariant",
            "title": "Never Run Container as Root",
            "resource": "Dockerfile",
            "trust_tier": "human_reviewed",
            "tags": ["cwe-250", "docker"],
            "frontmatter": {"rule": "USER openhems"},
            "body_markdown": "Containers on HAOS must declare an unprivileged USER to prevent root escapes.",
        }
        record_okf_concept(db_path, concept)

        # 2. Query concept
        results = query_okf_concepts(db_path, resource="Dockerfile")
        assert len(results) == 1
        assert results[0]["title"] == "Never Run Container as Root"

        # 3. Export bundle
        exported_count = export_okf_bundle(db_path, export_dir)
        assert exported_count == 1
        assert (export_dir / "invariants").is_dir()

        # 4. Import bundle into new DB
        imported_count = import_okf_bundle(db_path2, export_dir)
        assert imported_count == 1

        # 5. Query guidance
        guidance = query_security_guidance(db_path2, "Dockerfile")
        assert guidance["okf_trust_tier"] == "HUMAN_REVIEWED"
        assert len(guidance["security_invariants"]) == 1
        assert "Mantis Security Advisor: Dockerfile" in guidance["guidance_markdown"]

        # 6. Official OKF v0.2 Specification Validator Script Verification
        import subprocess
        val_script = Path("/config/.hermes/profiles/matthijs/skills/software-development/okf-open-knowledge-format/scripts/validate.sh")
        if val_script.exists():
            res = subprocess.run([str(val_script), str(export_dir)], capture_output=True, text=True)
            print("OKF Validator output:\n", res.stdout)
            assert res.returncode == 0
            assert "Bundle is OKF v0.2 conformant" in res.stdout
            assert "error(s)" not in res.stdout
            assert "warning(s)" not in res.stdout
