import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any


def run_hadolint(target_dir: Path, timeout: int = 30) -> List[Dict[str, Any]]:
    """
    Run Hadolint on Dockerfiles found in target directory.
    """
    hadolint_bin = shutil.which("hadolint") or "/usr/local/bin/hadolint"
    if not shutil.which(hadolint_bin) and not Path(hadolint_bin).exists():
        return []

    dockerfiles = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if f == "Dockerfile" or f.startswith("Dockerfile."):
                dockerfiles.append(str(Path(root) / f))

    if not dockerfiles:
        return []

    results = []
    for df in dockerfiles:
        try:
            cmd = [str(hadolint_bin), "-f", "json", df]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if res.stdout.strip():
                data = json.loads(res.stdout)
                for issue in data:
                    results.append({
                        "source": "hadolint",
                        "code": issue.get("code"),
                        "file": issue.get("file"),
                        "start_line": issue.get("line"),
                        "end_line": issue.get("line"),
                        "message": issue.get("message"),
                        "severity": issue.get("level", "warning").upper(),
                    })
        except Exception as e:
            results.append({"source": "hadolint", "file": df, "error": str(e)})

    return results
