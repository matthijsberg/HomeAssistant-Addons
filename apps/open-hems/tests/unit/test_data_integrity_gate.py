import pytest
from pathlib import Path
import subprocess
import sys

def test_data_integrity_gate():
    """Verify that verify_data_integrity.py runs and passes all offline integrity checks."""
    script_path = Path(__file__).parents[2] / "scripts" / "verify_data_integrity.py"
    assert script_path.exists(), f"Integrity gate script not found at {script_path}"

    res = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    assert res.returncode == 0, f"Data integrity gate failed:\n{res.stdout}\n{res.stderr}"
