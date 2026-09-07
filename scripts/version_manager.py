#!/usr/bin/env python3
"""
Open HEMS Version & Release Manager
====================================
Manages the 4-tier semantic versioning workflow:
  Tier 1: MAJOR (x.0.0)      -> Major architectural shifts
  Tier 2: MINOR (0.x.0)      -> Public releases to GitHub with release notes
  Tier 3: PATCH (0.0.x)      -> Internal bugfixes & stable feature patches
  Tier 4: DEV   (0.0.0-dev.N)-> Daily internal development iterations

Usage:
  python3 version_manager.py status
  python3 version_manager.py bump [major|minor|patch|dev]
  python3 version_manager.py release-notes [version]
"""

import sys
import re
import os
import argparse
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"
CHANGELOG_FILE = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def get_current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "0.2.0"


def parse_version(v_str: str):
    # Matches X.Y.Z or X.Y.Z-dev.N
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?$", v_str)
    if not m:
        raise ValueError(f"Invalid version string: {v_str}")
    major, minor, patch, dev = m.groups()
    return int(major), int(minor), int(patch), int(dev) if dev else 0


def format_version(major: int, minor: int, patch: int, dev: int = 0) -> str:
    base = f"{major}.{minor}.{patch}"
    if dev > 0:
        return f"{base}-dev.{dev}"
    return base


def bump(level: str) -> str:
    curr = get_current_version()
    major, minor, patch, dev = parse_version(curr)

    if level == "major":
        new_v = format_version(major + 1, 0, 0, 0)
    elif level == "minor":
        new_v = format_version(major, minor + 1, 0, 0)
    elif level == "patch":
        new_v = format_version(major, minor, patch + 1, 0)
    elif level == "dev":
        new_v = format_version(major, minor, patch, dev + 1)
    else:
        raise ValueError(f"Unknown bump level: {level}")

    # Write to VERSION
    VERSION_FILE.write_text(new_v + "\n")

    # Always update config.yaml version field
    if CONFIG_FILE.exists():
        content = CONFIG_FILE.read_text()
        content = re.sub(r'version:\s*"[^"]+"', f'version: "{new_v}"', content)
        CONFIG_FILE.write_text(content)

    # Also update daemon.py version string if present
    daemon_file = Path(__file__).resolve().parent.parent / "daemon.py"
    if daemon_file.exists():
        d_content = daemon_file.read_text()
        d_content = re.sub(r'Version:\s*[\d\.\-a-z]+', f'Version: {new_v}', d_content)
        d_content = re.sub(r'"version":\s*"[^"]+"', f'"version": "{new_v}"', d_content)
        d_content = re.sub(r'v\d+\.\d+\.\d+[\-a-z\d\.]*', f'v{new_v}', d_content)
        daemon_file.write_text(d_content)

    print(new_v)
    return new_v


def get_release_notes(version: str) -> str:
    if not CHANGELOG_FILE.exists():
        return "No CHANGELOG.md found."
    text = CHANGELOG_FILE.read_text()
    pattern = rf"## \[{re.escape(version)}\][^\n]*\n(.*?)(?=\n## \[|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return f"No release notes entry found for [{version}] in CHANGELOG.md"


def main():
    parser = argparse.ArgumentParser(description="Open HEMS Version Manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    b_p = sub.add_parser("bump")
    b_p.add_argument("level", choices=["major", "minor", "patch", "dev"])

    r_p = sub.add_parser("release-notes")
    r_p.add_argument("version", nargs="?", default=None)

    args = parser.parse_args()

    if args.command == "status":
        v = get_current_version()
        print(f"Current Open HEMS Version: {v}")
    elif args.command == "bump":
        bump(args.level)
    elif args.command == "release-notes":
        v = args.version or get_current_version().split("-")[0]
        notes = get_release_notes(v)
        print(f"--- Release Notes for [{v}] ---")
        print(notes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
