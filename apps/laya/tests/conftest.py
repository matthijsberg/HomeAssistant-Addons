"""Test fixtures and path configuration for Laya Router."""

import os
import sys
from pathlib import Path

# Add app code directory to sys.path
APP_DIR = Path(__file__).parent.parent / "rootfs" / "usr" / "src" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Ensure mock mode during test runs
os.environ["LAYA_MOCK_MODE"] = "1"
os.environ["LAYA_API_KEY"] = "test-secret-token"
