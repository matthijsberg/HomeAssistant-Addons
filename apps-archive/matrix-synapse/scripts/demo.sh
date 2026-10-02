#!/usr/bin/env bash
set -e

echo "[Demo] Running Matrix Synapse Add-on Smoke Test..."

# Test YAML schema & config loading
python3 -c "
import yaml
with open('config.yaml') as f:
    cfg = yaml.safe_load(f)
assert cfg['name'] == 'Matrix Synapse + Sliding Sync'
assert 'ssl:rw' in cfg['map']
assert 'backup:rw' in cfg['map']
assert 'config:rw' in cfg['map']
print('[Demo] Add-on Manifest & Directory Mappings verified: OK')
"

echo "[Demo] Running Setup Validation Script..."
bash scripts/setup.sh

echo "[Demo] Testing Backup Script to /backup mount..."
bash scripts/backup.sh

echo "[Demo] Smoke Test Completed Successfully! ✓"
