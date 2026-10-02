#!/usr/bin/env bash
set -e

echo "=== Pre-Release Security Audit (ha-addon-matrix) ==="
STATUS=0

echo "[Pillar 1: PII & Secrets Protection]"
# Check for hardcoded credentials/tokens/passwords
if grep -riE "(password\s*=\s*['\"][^'\"]+['\"]|api_key\s*=\s*['\"][^'\"]+['\"]|secret_key|private_key)" --exclude-dir=".git" --exclude-dir="tests" --exclude="security_audit.sh" .; then
    echo "❌ WARNING: Possible hardcoded credentials or secret keys detected!"
    STATUS=1
else
    echo "✓ No hardcoded credentials found in source files."
fi

echo ""
echo "[Pillar 2: Input Sanitization & Option Parsing]"
# Check run.sh for safe jq usage
if grep -q "jq -r" run.sh; then
    echo "✓ Safe jq extraction used for option parsing in run.sh."
else
    echo "❌ WARNING: Verify option parsing in run.sh."
    STATUS=1
fi

echo ""
echo "[Pillar 3: Permissions & Access Controls]"
# Check script execution permissions
if [ -x "run.sh" ] && [ -x "scripts/setup.sh" ]; then
    echo "✓ Execution flags correct on entrypoint and scripts."
else
    echo "❌ WARNING: Some scripts lack executable (+x) permissions!"
    STATUS=1
fi

echo ""
echo "[Pillar 4: Code Quality & YAML Linting]"
python3 -c "
import yaml
for path in ['config.yaml', 'build.yaml']:
    with open(path) as f:
        yaml.safe_load(f)
print('✓ All YAML configuration files pass syntax validation.')
" || STATUS=1

echo ""
if [ $STATUS -eq 0 ]; then
    echo "✅ SECURITY AUDIT PASSED! All 4 pillars verified."
    exit 0
else
    echo "❌ SECURITY AUDIT FAILED! Address warnings above before release."
    exit 1
fi
