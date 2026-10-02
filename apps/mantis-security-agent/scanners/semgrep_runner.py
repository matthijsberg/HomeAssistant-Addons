import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any


def run_semgrep(target_dir: Path, custom_rules_dir: Path = None, timeout: int = 120) -> List[Dict[str, Any]]:
    """
    Run Semgrep CE in JSON mode on target_dir.
    Returns normalized list of finding leads.
    """
    semgrep_bin = shutil.which("semgrep")
    if not semgrep_bin:
        # Fallback check virtualenv
        venv_bin = Path("/opt/venv/bin/semgrep")
        if venv_bin.exists():
            semgrep_bin = str(venv_bin)

    if not semgrep_bin:
        return []

    cmd = [
        semgrep_bin,
        "scan",
        "--json",
        "--quiet",
        "--no-git-ignore",
        "--disable-version-check",
    ]

    if custom_rules_dir and custom_rules_dir.exists():
        cmd.extend(["--config", str(custom_rules_dir)])
    else:
        cmd.extend(["--config", "auto"])

    cmd.append(str(target_dir))

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
        for r in data.get("results", []):
            results.append({
                "source": "semgrep",
                "check_id": r.get("check_id"),
                "file": r.get("path"),
                "start_line": r.get("start", {}).get("line"),
                "end_line": r.get("end", {}).get("line"),
                "message": r.get("extra", {}).get("message"),
                "severity": r.get("extra", {}).get("severity", "WARNING").upper(),
                "metadata": r.get("extra", {}).get("metadata", {}),
            })
        return results
    except Exception as e:
        return [{"source": "semgrep", "error": str(e)}]
