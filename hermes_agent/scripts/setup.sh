#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "================================================================="
echo "   SCAFFOLDING & QUALITY VERIFICATION: hermes_agent              "
echo "================================================================="

echo -e "\n1. [Manifest Validation] config.yaml, build.yaml, translations..."
python3 - <<'PY'
import yaml

config = yaml.safe_load(open("config.yaml"))
for key in ("name", "version", "slug", "arch", "options", "schema"):
    assert key in config, f"config.yaml missing {key}"
missing = set(config["options"]) - set(config["schema"])
assert not missing, f"options without schema: {sorted(missing)}"
yaml.safe_load(open("build.yaml"))
translations = yaml.safe_load(open("translations/en.yaml"))["configuration"]
untranslated = set(config["schema"]) - set(translations)
assert not untranslated, f"schema keys without translation: {sorted(untranslated)}"
print(f"✓ Manifest valid (version {config['version']})")
PY

echo -e "\n2. [OKF v0.2 Validation] knowledge bundle..."
"${REPO_ROOT}/scripts/validate_okf.sh"

echo -e "\n3. [Security Audit] committed secrets..."
if grep -rInE \
    'ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9_-]{32,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.' \
    --exclude-dir=.git --exclude-dir=scripts . ; then
  echo "❌ Possible secret committed (see matches above)"
  exit 1
fi
echo "✓ No secrets found"

echo -e "\n4. [Syntax] bash -n and Python AST..."
for sh_file in ./*.sh scripts/*.sh; do
  bash -n "$sh_file"
done
python3 -B -c "import ast, glob; [ast.parse(open(f).read(), f) for f in glob.glob('*.py') + glob.glob('tests/*.py')]"
echo "✓ All scripts parse"

echo -e "\n5. [Pytest Suite] ..."
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest tests/ -q -p no:cacheprovider

echo -e "\n================================================================="
echo "✓ ALL QUALITY GATES PASSED!"
echo "================================================================="
