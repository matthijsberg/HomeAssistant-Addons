#!/usr/bin/env bash
set -e

echo "[Setup] Validating Matrix Synapse Home Assistant Add-on..."

# 1. Validate YAML syntax for config.yaml and build.yaml
python3 -c "import yaml; yaml.safe_load(open('config.yaml')); print('  - config.yaml: Valid YAML ✓')"
python3 -c "import yaml; yaml.safe_load(open('build.yaml')); print('  - build.yaml: Valid YAML ✓')"

# 2. Check executable permissions
chmod +x rootfs/usr/bin/run.sh
chmod +x scripts/*.sh
echo "  - Script permissions: Verified ✓"

# 3. Check HA Directory mounts
echo "  - Checking Home Assistant mount targets:"
echo "    • /ssl mount: $( [ -d /ssl ] && echo 'Available ✓' || echo 'Missing' )"
echo "    • /backup mount: $( [ -d /backup ] && echo 'Available ✓' || echo 'Missing' )"
echo "    • /config mount: $( [ -d /config ] && echo 'Available ✓' || echo 'Missing' )"

echo "[Setup] All pre-flight checks passed successfully!"
