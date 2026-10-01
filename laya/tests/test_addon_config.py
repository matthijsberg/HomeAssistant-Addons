"""Unit tests validating Home Assistant add-on manifests and configurations."""

from pathlib import Path

import yaml

ADDON_ROOT = Path(__file__).parent.parent
CONFIG_PATH = ADDON_ROOT / "config.yaml"
BUILD_PATH = ADDON_ROOT / "build.yaml"
TRANSLATIONS_PATH = ADDON_ROOT / "translations" / "en.yaml"
DOCKERFILE_PATH = ADDON_ROOT / "Dockerfile"


def test_config_yaml_manifest():
    assert CONFIG_PATH.exists(), "config.yaml must exist"
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert cfg["name"] == "Laya Router"
    assert cfg["slug"] == "laya"
    assert cfg["version"] == "0.5.0"
    assert cfg["arch"] == ["amd64"]
    assert cfg["startup"] == "services"
    assert cfg["boot"] == "auto"

    # Ingress checks
    assert cfg.get("ingress") is True
    assert cfg.get("ingress_port") == 8000
    assert cfg.get("ingress_panel") is True
    assert "panel_icon" in cfg

    # Hardware devices check: /dev/dri passthrough for Intel iGPU
    assert "devices" in cfg
    assert "/dev/dri:/dev/dri" in cfg["devices"]

    # Ports check: null by default for optional LAN mapping
    assert "ports" in cfg
    assert "8000/tcp" in cfg["ports"]
    assert cfg["ports"]["8000/tcp"] is None

    # Required options
    opts = cfg.get("options", {})
    assert opts.get("device") in ("cpu", "xpu")
    assert opts.get("threads") == 6
    assert "english" in opts.get("checkpoints", "")
    assert opts.get("router_default") == "multilingual"
    assert "api_key" in opts
    assert opts.get("log_level") == "info"
    assert opts.get("provider") in ("gemini", "litellm", "openrouter", "custom")
    assert "families" in opts
    families_list = opts.get("families", [])
    assert any(f.get("name") == "quick" and f.get("model") == "gemini-3.5-flash-lite" for f in families_list)
    assert any(f.get("name") == "deep" and f.get("thinking_budget") is None for f in families_list)

    # Schema check
    schema = cfg.get("schema", {})
    assert "device" in schema
    assert "threads" in schema
    assert "checkpoints" in schema
    assert "router_default" in schema
    assert "api_key" in schema
    assert "provider" in schema
    assert "families" in schema
    assert "log_level" in schema


def test_build_yaml_manifest():
    assert BUILD_PATH.exists(), "build.yaml must exist"
    with open(BUILD_PATH, "r", encoding="utf-8") as f:
        bld = yaml.safe_load(f)

    assert "build_from" in bld
    assert bld["build_from"]["amd64"] in ("ubuntu:24.04", "python:3.12-slim")
    assert "args" in bld
    assert "LAYA_VERSION" in bld["args"]


def test_translations_coverage():
    assert TRANSLATIONS_PATH.exists(), "translations/en.yaml must exist"
    with open(TRANSLATIONS_PATH, "r", encoding="utf-8") as f:
        trans = yaml.safe_load(f)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    config_keys = set(cfg.get("options", {}).keys())
    trans_keys = set(trans.get("configuration", {}).keys())

    missing = config_keys - trans_keys
    assert not missing, f"Missing English translations for options: {missing}"


def test_dockerfile_exists():
    assert DOCKERFILE_PATH.exists()
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert any(base in content for base in ("ubuntu:24.04", "python:3.12-slim"))
    assert "libze1" in content
    assert "whl/xpu" in content
    assert "laya[serve]" in content
    assert "EXPOSE 8000" in content
