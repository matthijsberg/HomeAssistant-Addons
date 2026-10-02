#!/usr/bin/env python3
"""Standalone validator for Home Assistant add-on manifests."""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
BUILD_PATH = REPO_ROOT / "build.yaml"
TRANSLATIONS_PATH = REPO_ROOT / "translations" / "en.yaml"


def main():
    errors = 0
    print("Checking config.yaml...")
    if not CONFIG_PATH.is_file():
        print("❌ config.yaml missing")
        return 1

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    for field in ["name", "slug", "version", "arch", "startup", "boot", "options", "schema"]:
        if field not in cfg:
            print(f"❌ Missing field '{field}' in config.yaml")
            errors += 1

    print("Checking build.yaml...")
    if not BUILD_PATH.is_file():
        print("❌ build.yaml missing")
        return 1

    with open(BUILD_PATH, "r", encoding="utf-8") as f:
        bld = yaml.safe_load(f)

    if "build_from" not in bld or "amd64" not in bld["build_from"]:
        print("❌ build_from.amd64 missing in build.yaml")
        errors += 1

    print("Checking translations/en.yaml...")
    if not TRANSLATIONS_PATH.is_file():
        print("❌ translations/en.yaml missing")
        return 1

    with open(TRANSLATIONS_PATH, "r", encoding="utf-8") as f:
        trans = yaml.safe_load(f)

    opts = set(cfg.get("options", {}).keys())
    trans_opts = set(trans.get("configuration", {}).keys())
    missing = opts - trans_opts
    if missing:
        print(f"❌ Missing translations for options: {missing}")
        errors += 1

    if errors == 0:
        print("✓ All add-on manifests are valid!")
        return 0
    else:
        print(f"❌ Encountered {errors} errors.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
