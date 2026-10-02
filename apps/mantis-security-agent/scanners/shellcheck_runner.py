import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any


def run_shellcheck(target_dir: Path, timeout: int = 45) -> List[Dict[str, Any]]:
    """
    Run ShellCheck on all shell scripts found in target directory.
    """
    shellcheck_bin = shutil.which("shellcheck")
    if not shellcheck_bin:
        return []

    scripts = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if f.endswith(".sh") or f == "run" or f == "finish":
                scripts.append(str(Path(root) / f))

    if not scripts:
        return []

    cmd = [
        shellcheck_bin,
        "-f", "json",
    ] + scripts

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
        for c in data:
            results.append({
                "source": "shellcheck",
                "code": f"SC{c.get('code')}",
                "file": c.get("file"),
                "start_line": c.get("line"),
                "end_line": c.get("endLine", c.get("line")),
                "message": c.get("message"),
                "severity": c.get("level", "warning").upper(),
            })
        return results
    except Exception as e:
        return [{"source": "shellcheck", "error": str(e)}]
