import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any


def run_gitleaks(target_dir: Path, timeout: int = 60) -> List[Dict[str, Any]]:
    """
    Run Gitleaks detector on target directory.
    Returns detected secrets with redacted values.
    """
    gitleaks_bin = shutil.which("gitleaks") or "/usr/local/bin/gitleaks"
    if not shutil.which(gitleaks_bin) and not Path(gitleaks_bin).exists():
        return []

    cmd = [
        str(gitleaks_bin),
        "dir",
        str(target_dir),
        "--no-git",
        "--report-format", "json",
        "--report-path", "/dev/stdout",
        "--exit-code", "0",
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if not res.stdout.strip():
            return []
        data = json.loads(res.stdout)
        results = []
        for s in data:
            results.append({
                "source": "gitleaks",
                "rule_id": s.get("RuleID"),
                "file": s.get("File"),
                "start_line": s.get("StartLine"),
                "end_line": s.get("EndLine"),
                "description": s.get("Description"),
                "secret_redacted": "[REDACTED_SECRET]",
                "severity": "CRITICAL",
                "cwe": "CWE-798",
            })
        return results
    except Exception as e:
        return [{"source": "gitleaks", "error": str(e)}]
