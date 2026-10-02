#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "${REPO_ROOT}/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.venv/bin/activate"
fi

echo "================================================================="
echo "   SCAFFOLDING & QUALITY VERIFICATION: ha-addon-laya            "
echo "================================================================="

echo -e "\n1. [Manifest Validation] Checking config.yaml, build.yaml, and translations..."
"${REPO_ROOT}/.venv/bin/python3" "${REPO_ROOT}/scripts/test_config.py"

echo -e "\n2. [OKF v0.2 Validation] Checking Open Knowledge Format bundle..."
bash "${REPO_ROOT}/scripts/validate_okf.sh"

echo -e "\n3. [Security Audit] Running pre-commit secret & credential scanner..."
bash "${REPO_ROOT}/scripts/security_audit.sh"

echo -e "\n4. [Shell Syntax] Validating bash scripts with 'bash -n'..."
for sh_file in "${REPO_ROOT}/scripts"/*.sh "${REPO_ROOT}/rootfs"/*.sh; do
  if [ -f "$sh_file" ]; then
    bash -n "$sh_file"
  fi
done
echo "✓ All bash scripts passed syntax checks!"

echo -e "\n5. [Code Linting] Checking Python code style with ruff..."
if [ -f "${REPO_ROOT}/.venv/bin/ruff" ]; then
  "${REPO_ROOT}/.venv/bin/ruff" check "${REPO_ROOT}"
  echo "✓ Ruff linting checks passed!"
elif command -v ruff &>/dev/null; then
  ruff check "${REPO_ROOT}"
  echo "✓ Ruff linting checks passed!"
else
  echo "ℹ Ruff not found in PATH; skipping ruff lint check."
fi

echo -e "\n6. [Pytest Suite] Running automated unit & regression tests..."
cd "${REPO_ROOT}"
"${REPO_ROOT}/.venv/bin/pytest" tests/ -v

echo -e "\n================================================================="
echo "✓ ALL QUALITY GATES PASSED! Scaffolding is verified."
echo "================================================================="
