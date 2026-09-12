#!/usr/bin/env bash
# ==============================================================================
# Open HEMS Automated Deploy & Live HA Upgrade Script (With Backup Retention)
# ==============================================================================
set -e

BUMP_TYPE="${1:-patch}"
ADDON_DIR="/config/addons/open-hems"
ARCHIVE_DIR="/config/addons_archive/open-hems"
MAX_BACKUPS=5
SSH_KEY="/config/.ssh/id_ed25519"
SSH_HOST="172.30.32.1"
SSH_PORT="2222"

echo "=== 🚀 Open HEMS Deployment & Upgrade Pipeline ==="

# 0. Create Snapshot of Current Working Version (Multi-Version Backup Retention)
CURRENT_VER=$(python3 "${ADDON_DIR}/scripts/version_manager.py" status 2>/dev/null | awk '{print $NF}' || echo "prev")
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SNAPSHOT_DIR="${ARCHIVE_DIR}/v${CURRENT_VER}_${TIMESTAMP}"

echo "Step 0: Creating snapshot backup of current version (v${CURRENT_VER})..."
mkdir -p "${SNAPSHOT_DIR}"
cp -r "${ADDON_DIR}"/* "${SNAPSHOT_DIR}/"

# Keep last N backups, remove older ones (FIFO rotation)
echo "Rotating backups (retaining last ${MAX_BACKUPS} versions)..."
cd "${ARCHIVE_DIR}"
ls -1td v* 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs -r rm -rf

echo "✓ Snapshot saved at: ${SNAPSHOT_DIR}"

# 1. Bump Version
echo "Step 1: Bumping version (${BUMP_TYPE})..."

# Pre-flight: Mandatory Bytecode Compilation & Syntax Check
echo "Pre-flight: Verifying Python bytecode and syntax..."
python3 -m py_compile "${ADDON_DIR}/daemon.py"
echo "✓ Python bytecode compilation passed!"

# Pre-flight: Mandatory Data & API Integrity Verification Gate
echo "Pre-flight: Running Automated Data & API Integrity Verification Gate..."
python3 "${ADDON_DIR}/scripts/verify_data_integrity.py"
echo "✓ All data integrity invariants passed!"

# Pre-flight: Mandatory Full Regression Test Suite
echo "Pre-flight: Running regression tests..."
PYTHONPATH="${ADDON_DIR}" pytest "${ADDON_DIR}/tests/"
echo "✓ All pytest regression tests passed!"

NEW_VER=$(python3 "${ADDON_DIR}/scripts/version_manager.py" bump "${BUMP_TYPE}")

# 2. Run Pre-Commit Security & Secret Scanner
echo "Step 2: Scanning for secrets and credentials..."
python3 "${ADDON_DIR}/scripts/secret_scanner.py"

# 3. Commit, Tag, and Push to GitHub
echo "Step 3: Committing, tagging, and pushing to GitHub..."
cd "${ADDON_DIR}"
git add .
git commit -m "release: bump version to ${NEW_VER}" || true
git tag -a "v${NEW_VER}" -m "Release v${NEW_VER}" 2>/dev/null || true
git push origin main --tags || true

# 4. Sync files to host /addons/open-hems/ via SSH
echo "Step 4: Synchronizing files to host /addons/open-hems/..."
cp -r "${ADDON_DIR}"/* /share/addons/open-hems/
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
    "cp -r /share/addons/open-hems/* /addons/open-hems/ && ha store reload"

# 5. Trigger live update / rebuild in Home Assistant with Health Check
echo "Step 5: Upgrading running app in Home Assistant..."
if ! ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
    "ha apps update local_open_hems || (ha apps rebuild local_open_hems && ha apps restart local_open_hems)"; then
    echo "❌ Upgrade/rebuild failed on host! Diagnostic information follows:"
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
        "ha apps logs local_open_hems | tail -n 40" || true
    echo "⚠️ Code preserved on disk for developer / AI agent triage (no automatic rollback)."
    exit 1
fi

# 6. Post-Deploy Live Data & API Smoke Test
echo "Step 6: Running Post-Deploy Live Data & API Smoke Test..."
sleep 4
if ! python3 "${ADDON_DIR}/scripts/verify_data_integrity.py" --live --url "http://172.30.33.10:8099"; then
    echo "❌ Post-deploy data integrity smoke test failed!"
    echo "⚠️ Diagnostic details reported above. Code preserved for developer / AI agent forward-fixing (no rollback)."
    exit 1
fi
echo "✓ Post-deploy live data integrity verified!"

echo "=== ✅ Deployment Complete: Open HEMS ${NEW_VER} is live and healthy! ==="
echo "Retained snapshots in ${ARCHIVE_DIR}:"
ls -1td "${ARCHIVE_DIR}"/* | head -n "${MAX_BACKUPS}"
