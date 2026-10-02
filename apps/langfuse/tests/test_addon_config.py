import os
from pathlib import Path
import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
BUILD_PATH = REPO_ROOT / "build.yaml"


def test_config_yaml_structure():
    assert CONFIG_PATH.exists()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert cfg["name"] == "Langfuse"
    assert cfg["slug"] == "langfuse"
    assert cfg["version"].startswith("4.")
    assert cfg["init"] is False
    assert "amd64" in cfg["arch"]
    assert cfg["ingress"] is True
    assert cfg["ingress_port"] == 8099
    assert cfg["timeout"] == 120
    assert cfg["backup"] == "cold"


def test_dynamic_port_mapping():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    ports = cfg.get("ports", {})
    assert "3000/tcp" in ports
    assert ports["3000/tcp"] is None, "3000/tcp must be null by default to allow optional LAN mapping"


def test_build_yaml_structure():
    assert BUILD_PATH.exists()
    with open(BUILD_PATH, "r", encoding="utf-8") as f:
        bld = yaml.safe_load(f)

    assert "build_from" in bld
    assert "amd64" in bld["build_from"]
    assert "LANGFUSE_VERSION" in bld.get("args", {})
    assert "CLICKHOUSE_VERSION" in bld.get("args", {})
    assert "SEAWEEDFS_VERSION" in bld.get("args", {})


def test_translations_en():
    trans_path = REPO_ROOT / "translations" / "en.yaml"
    assert trans_path.exists()
    with open(trans_path, "r", encoding="utf-8") as f:
        trans = yaml.safe_load(f)
    assert "configuration" in trans
    assert "admin_email" in trans["configuration"]
    assert "retention_days" in trans["configuration"]
