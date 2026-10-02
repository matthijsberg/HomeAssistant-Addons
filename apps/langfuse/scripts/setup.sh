#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "================================================================="
echo "   SCAFFOLDING & QUALITY VERIFICATION: ha-addon-langfuse        "
echo "================================================================="

echo -e "\n1. [Manifest Validation] Checking config.yaml and build.yaml..."
python3 "${REPO_ROOT}/scripts/test_config.py"

echo -e "\n2. [OKF v0.2 Validation] Checking Open Knowledge Format bundle..."
"${REPO_ROOT}/scripts/validate_okf.sh"

echo -e "\n3. [Security Audit] Running pre-commit secret & credential scanner..."
"${REPO_ROOT}/scripts/security_audit.sh"

echo -e "\n4. [Shell Syntax] Validating bash scripts with 'bash -n'..."
for sh_file in "${REPO_ROOT}/scripts"/*.sh; do
  bash -n "$sh_file"
done
echo "✓ All bash scripts passed syntax checks!"

echo -e "\n5. [Pytest Suite] Running automated unit & regression tests..."
cd "${REPO_ROOT}" && pytest tests/ -v

echo -e "\n================================================================="
echo "✓ ALL QUALITY GATES PASSED! Scaffolding is verified."
echo "================================================================="
