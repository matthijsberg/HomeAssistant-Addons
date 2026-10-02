#!/usr/bin/env python3
"""
Home Assistant Add-on Manifest & Schema Validator
Validates config.yaml and build.yaml against Supervisor specification.
"""

import sys
from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
BUILD_PATH = REPO_ROOT / "build.yaml"

def validate_config():
    print(f"Validating {CONFIG_PATH}...")
    if not CONFIG_PATH.exists():
        print(f"❌ config.yaml not found at {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    required_keys = ["name", "version", "slug", "description", "arch", "startup", "boot", "ingress", "ingress_port"]
    for k in required_keys:
        if k not in cfg:
            print(f"❌ Missing required key in config.yaml: {k}")
            sys.exit(1)

    # Ingress check
    assert cfg["ingress"] is True, "ingress must be true"
    assert cfg["ingress_port"] == 8099, "ingress_port must be 8099 for dual nginx setup"

    # Ports check (dynamic port specification)
    assert "ports" in cfg, "ports map must be declared"
    assert "3000/tcp" in cfg["ports"], "3000/tcp must be declared in ports"
    assert cfg["ports"]["3000/tcp"] is None, "3000/tcp default must be null (Ingress-only/optional LAN)"

    # Timeout & Backup
    assert cfg.get("timeout") == 120, "timeout must be 120 for clean DB shutdown"
    assert cfg.get("backup") == "cold", "backup must be cold for DB snapshot consistency"

    # Schema & options parity
    options = cfg.get("options", {})
    schema = cfg.get("schema", {})
    for opt_k in options:
        assert opt_k in schema, f"Option {opt_k} not defined in schema"

    print("✓ config.yaml passed all validation checks!")

def validate_build():
    print(f"Validating {BUILD_PATH}...")
    if not BUILD_PATH.exists():
        print(f"❌ build.yaml not found at {BUILD_PATH}")
        sys.exit(1)

    with open(BUILD_PATH, "r", encoding="utf-8") as f:
        bld = yaml.safe_load(f)

    assert "build_from" in bld, "build_from required in build.yaml"
    assert "amd64" in bld["build_from"], "amd64 required in build.yaml"
    print("✓ build.yaml passed all validation checks!")

if __name__ == "__main__":
    validate_config()
    validate_build()
    print("✓ All Home Assistant Add-on manifest checks passed!")
