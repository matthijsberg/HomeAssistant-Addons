#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Running Security & Secret Audit in ${REPO_ROOT} ==="

FORBIDDEN_PATTERNS=(
  "ghp_[0-9a-zA-Z]{36}"
  "github_pat_[0-9a-zA-Z_]{82}"
  "eyJhbGciOi[0-9a-zA-Z_-]{20,}"
  "-----BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY"
  "sk-lf-[0-9a-zA-Z_-]{30,}"
  "pk-lf-[0-9a-zA-Z_-]{30,}"
)

FAILURES=0

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  # Grep across repo excluding tests, scripts, and PRDs
  matches=$(grep -rE --exclude-dir=".git" --exclude="*.log" --exclude="security_audit.sh" -- "${pattern}" "${REPO_ROOT}" || true)
  if [ -n "${matches}" ]; then
    echo "❌ High Severity: Potential hardcoded secret found matching '${pattern}':"
    echo "${matches}"
    FAILURES=$((FAILURES + 1))
  fi
done

if [ ${FAILURES} -eq 0 ]; then
  echo "✓ Secret audit passed: zero credentials or hardcoded keys found!"
else
  echo "❌ Security audit failed with ${FAILURES} findings."
  exit 1
fi
