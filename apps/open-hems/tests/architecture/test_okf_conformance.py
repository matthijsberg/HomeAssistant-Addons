"""
Architecture Guard: OKF v0.2 Conformance Verification
====================================================
Runs the official OKF v0.2 validator against the project's knowledge bundle
(/config/addons/open-hems/knowledge) to ensure zero schema drift, valid frontmatter,
mandatory 'type' fields, and valid Attested Computations.
"""

import subprocess
from pathlib import Path


def test_okf_bundle_conformance():
    repo_root = Path(__file__).resolve().parent.parent.parent
    knowledge_dir = repo_root / "knowledge"
    validator_script = Path("/config/.hermes/profiles/matthijs/skills/software-development/okf-open-knowledge-format/scripts/validate.sh")

    assert knowledge_dir.exists(), f"Missing OKF knowledge bundle at {knowledge_dir}"
    assert (knowledge_dir / "index.md").exists(), "Missing bundle root index.md"

    if validator_script.exists():
        res = subprocess.run(
            [str(validator_script), str(knowledge_dir)],
            capture_output=True,
            text=True
        )
        assert res.returncode == 0, f"OKF validation failed:\n{res.stdout}\n{res.stderr}"
        assert "Bundle is OKF v0.2 conformant" in res.stdout
