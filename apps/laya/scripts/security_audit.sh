#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Running Security & Secret Audit in ${REPO_ROOT} ==="

FORBIDDEN_PATTERNS=(
  "ghp_[0-9a-zA-Z]{36}"
  "github_pat_[0-9a-zA-Z_]{82}"
  "eyJhbGciOi[0-9a-zA-Z_-]{20,}"
  "-----BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY"
  "sk-ant-[0-9a-zA-Z_-]{30,}"
  "AIza[0-9A-Za-z\\-_]{35}"
)

FAILURES=0

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  matches=$(grep -rE --exclude-dir=".git" --exclude-dir="__pycache__" --exclude-dir=".pytest_cache" --exclude="*.log" --exclude="security_audit.sh" -- "${pattern}" "${REPO_ROOT}" || true)
  if [ -n "${matches}" ]; then
    echo "❌ High Severity: Potential hardcoded secret found matching '${pattern}':"
    echo "${matches}"
    FAILURES=$((FAILURES + 1))
  fi
done

if [ ${FAILURES} -eq 0 ]; then
  echo "✓ Secret audit passed: zero credentials or hardcoded keys found!"
  exit 0
else
  echo "❌ Security audit failed with ${FAILURES} findings."
  exit 1
fi
