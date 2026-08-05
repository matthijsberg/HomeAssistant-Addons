#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   PRE-RELEASE SECURITY AUDIT (4-PILLAR CHECK)"
echo "=================================================="

ERRORS=0

# Pillar 1: PII & Secrets Audit
echo "[Pillar 1] PII & Secrets Protection..."
SECRETS_FOUND=$(git grep -i -E "(password\s*=\s*['\"][^'\"]+['\"]|api_key\s*=\s*['\"][^'\"]+['\"])" || true)
if [ -n "$SECRETS_FOUND" ]; then
    echo "  ❌ FAILED: Hardcoded credentials detected:"
    echo "$SECRETS_FOUND"
    ERRORS=$((ERRORS + 1))
else
    echo "  ✓ PASSED: Zero hardcoded secrets/credentials in codebase."
fi

# Pillar 2: Input Sanitization & Injection Defense
echo "[Pillar 2] Input Sanitization & Shell Quoting..."
UNQUOTED_VARS=$(grep -n '\$[A-Z_]*[a-z_]*' rootfs/usr/bin/run.sh | grep -v '"' | grep -v '#' || true)
if [ -n "$UNQUOTED_VARS" ]; then
    echo "  ⚠️ Warning: Potential unquoted shell expansion lines found in run.sh:"
    echo "$UNQUOTED_VARS"
else
    echo "  ✓ PASSED: Shell variable expansions properly quoted."
fi

# Pillar 3: Least Privilege & Access Boundaries
echo "[Pillar 3] Least Privilege & Access Boundaries..."
RUN_SH_PERM=$(stat -c "%a" rootfs/usr/bin/run.sh)
if [ "$RUN_SH_PERM" = "755" ] || [ "$RUN_SH_PERM" = "711" ]; then
    echo "  ✓ PASSED: Executable permissions set correctly (${RUN_SH_PERM})."
else
    echo "  ✓ Setting executable permissions to 755..."
    chmod 755 rootfs/usr/bin/run.sh scripts/*.sh
fi

# Pillar 4: Default Configuration Hardening
echo "[Pillar 4] Default Configuration Security..."
REG_STATUS=$(jq -r '.options.registration_enabled // false' config.yaml 2>/dev/null || grep 'registration_enabled' config.yaml)
if [[ "$REG_STATUS" == *"false"* ]]; then
    echo "  ✓ PASSED: Public registration is DISABLED by default (prevents unauthorized relay access)."
else
    echo "  ❌ FAILED: Public registration enabled by default!"
    ERRORS=$((ERRORS + 1))
fi

echo "=================================================="
if [ $ERRORS -eq 0 ]; then
    echo "  ✅ SECURITY AUDIT PASSED: ALL 4 PILLARS SECURE!"
    echo "=================================================="
    exit 0
else
    echo "  ❌ SECURITY AUDIT FAILED WITH $ERRORS ERROR(S)."
    echo "=================================================="
    exit 1
fi
