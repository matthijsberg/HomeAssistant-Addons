#!/usr/bin/env bash
# ==============================================================================
# Open HEMS Automated Deploy & Live HA Upgrade Script
# ==============================================================================
set -e

BUMP_TYPE="${1:-patch}"
ADDON_DIR="/config/addons/open-hems"
SSH_KEY="/root/.ssh/id_ed25519"
SSH_HOST="172.30.32.1"
SSH_PORT="2222"

echo "=== 🚀 Open HEMS Deployment & Upgrade Pipeline ==="

# 1. Bump Version
echo "Step 1: Bumping version (${BUMP_TYPE})..."
NEW_VER=$(python3 "${ADDON_DIR}/scripts/version_manager.py" bump "${BUMP_TYPE}")

# 2. Run Pre-Commit Security & Secret Scanner
echo "Step 2: Scanning for secrets and credentials..."
python3 "${ADDON_DIR}/scripts/secret_scanner.py"

# 3. Commit and push to GitHub
echo "Step 3: Committing and pushing to GitHub..."
cd "${ADDON_DIR}"
git add .
git commit -m "release: bump version to ${NEW_VER}" || true
git push origin main || true

# 4. Sync files to host /addons/open-hems/ via SSH
echo "Step 4: Synchronizing files to host /addons/open-hems/..."
cp -r "${ADDON_DIR}"/* /share/addons/open-hems/
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
    "cp -r /share/addons/open-hems/* /addons/open-hems/ && ha store reload"

# 5. Trigger live update / rebuild in Home Assistant
echo "Step 5: Upgrading running app in Home Assistant..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
    "ha apps update local_open_hems || ha apps rebuild local_open_hems"

echo "=== ✅ Deployment Complete: Open HEMS ${NEW_VER} is live and running in Home Assistant! ==="
