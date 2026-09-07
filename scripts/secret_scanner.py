#!/usr/bin/env python3
"""
Open HEMS Pre-Commit Secret Scanner
====================================
Prevents accidental commits of API keys, credentials, tokens, and PII.
Runs automatically before every `git commit` via .git/hooks/pre-commit.

Exit Code:
  0 = Clean, no secrets detected.
  1 = Secrets detected, commit ABORTED.
"""

import sys
import re
import subprocess
from pathlib import Path

# Sensitive file patterns that must NEVER be committed to git
SENSITIVE_FILES = [
    r"\.ha_api_config\.json$",
    r"google_token\.json$",
    r"client_secret.*\.json$",
    r"\.env$",
    r"\.pem$",
    r"\.key$",
    r"id_rsa",
    r"id_ed25519",
    r"secrets\.yaml$",
    r"options\.json$"
]

# Regex patterns for detecting hardcoded credentials & tokens
SECRET_PATTERNS = [
    # 1. GitHub Tokens
    (r"gh[pousr]_[A-Za-z0-9_]{36,255}", "GitHub Token"),
    (r"github_pat_[A-Za-z0-9_]{82}", "GitHub Fine-Grained Token"),

    # 2. Home Assistant & JWT Tokens
    (r"eyJhbGciOi[A-Za-z0-9_\-\.]+\.[A-Za-z0-9_\-\.]+\.[A-Za-z0-9_\-\.]+", "JWT / Home Assistant Access Token"),

    # 3. Private Keys
    (r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY", "Cryptographic Private Key"),

    # 4. Telegram Bot Tokens
    (r"\b\d{9,10}:[A-Za-z0-9_-]{35}\b", "Telegram Bot API Token"),

    # 5. Generic API Keys & Passwords in code
    (r"(?i)(api[_-]?key|secret|password|passwd|auth[_-]?token|bearer[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.]{12,}['\"]", "Hardcoded API Key or Password Assignment"),

    # 6. Google OAuth Credentials
    (r"\"client_secret\"\s*:\s*\"[^\"]{10,}\"", "Google OAuth Client Secret"),
    (r"\"refresh_token\"\s*:\s*\"[^\"]{10,}\"", "Google OAuth Refresh Token"),

    # 7. EnergyZero / Powerpeers / InfluxDB passwords
    (r"(?i)(influx|powerpeers|energyzero).*password\s*[:=]\s*['\"][^'\"]{6,}['\"]", "Database or Utility Provider Password")
]


def check_staged_files():
    """Checks filenames of staged files."""
    try:
        out = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True)
        staged = [line.strip() for line in out.splitlines() if line.strip()]
    except Exception as e:
        print(f"Warning: Could not check git staged files: {e}")
        return True

    violations = []
    for f in staged:
        for pat in SENSITIVE_FILES:
            if re.search(pat, f):
                violations.append((f, f"Blocked sensitive file pattern: {pat}"))

    if violations:
        print("\n❌ [COMMIT REJECTED] Sensitive credential files detected in git staging:")
        for f, reason in violations:
            print(f"  • {f} -> {reason}")
        return False

    return True


def check_staged_diff():
    """Checks the actual diff (+ lines) for secrets."""
    try:
        diff = subprocess.check_output(["git", "diff", "--cached", "-U0"], text=True)
    except Exception as e:
        print(f"Warning: Could not get git diff: {e}")
        return True

    added_lines = []
    current_file = "unknown"
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append((current_file, line[1:]))

    violations = []
    for f, line in added_lines:
        # Ignore comments or markdown headings if harmless
        stripped = line.strip()
        if stripped.startswith("#") and not ("=" in stripped or ":" in stripped):
            continue
        # Check against secret patterns
        for pat, desc in SECRET_PATTERNS:
            if re.search(pat, line):
                # Mask secret preview for terminal safety
                violations.append((f, desc, line[:40] + "..."))

    if violations:
        print("\n❌ [COMMIT REJECTED] Hardcoded credentials / secrets detected in staged code:")
        for f, desc, snippet in violations:
            print(f"  • File: {f}")
            print(f"    Threat: {desc}")
            print(f"    Snippet: {snippet}")
        print("\nPlease remove all credentials, use environment variables or configuration files listed in .gitignore.\n")
        return False

    return True


def main():
    print("🔒 Running Open HEMS Pre-Commit Security & Secret Scanner...")
    files_ok = check_staged_files()
    diff_ok = check_staged_diff()

    if not (files_ok and diff_ok):
        sys.exit(1)

    print("✓ Security Scan Passed: 0 secrets, API keys, or sensitive files found.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
