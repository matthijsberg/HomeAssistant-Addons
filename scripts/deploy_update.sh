#!/usr/bin/env bash
# ==============================================================================
# Open HEMS Automated Deploy & Live HA Upgrade Script (With Backup Retention)
# ==============================================================================
set -e

BUMP_TYPE="${1:-patch}"
ADDON_DIR="/config/addons/open-hems"
ARCHIVE_DIR="/config/addons_archive/open-hems"
MAX_BACKUPS=5
SSH_KEY="/root/.ssh/id_ed25519"
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
    "ha apps rebuild local_open_hems && ha apps restart local_open_hems"; then
    echo "❌ Upgrade failed! Initiating automatic rollback to ${SNAPSHOT_DIR}..."
    bash "${ADDON_DIR}/scripts/rollback.sh" "${SNAPSHOT_DIR}"
    exit 1
fi

echo "=== ✅ Deployment Complete: Open HEMS ${NEW_VER} is live and healthy! ==="
echo "Retained snapshots in ${ARCHIVE_DIR}:"
ls -1td "${ARCHIVE_DIR}"/* | head -n "${MAX_BACKUPS}"
