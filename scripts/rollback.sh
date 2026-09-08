#!/usr/bin/env bash
# ==============================================================================
# Open HEMS Instant Rollback Engine
# ==============================================================================
# Restores a previous stable version snapshot and redeploys to Home Assistant.
# Usage:
#   ./scripts/rollback.sh           (Rolls back to most recent snapshot)
#   ./scripts/rollback.sh list      (Lists available snapshots)
#   ./scripts/rollback.sh <version> (Rolls back to specific snapshot path/tag)
# ==============================================================================
set -e

ARCHIVE_DIR="/config/addons_archive/open-hems"
ADDON_DIR="/config/addons/open-hems"
SSH_KEY="/config/.ssh/id_ed25519"
SSH_HOST="172.30.32.1"
SSH_PORT="2222"

mkdir -p "${ARCHIVE_DIR}"

if [ "$1" = "list" ]; then
    echo "=== 📦 Available Open HEMS Backup Snapshots ==="
    ls -1td "${ARCHIVE_DIR}"/* 2>/dev/null || echo "No backups found."
    exit 0
fi

TARGET_BACKUP="$1"
if [ -z "${TARGET_BACKUP}" ]; then
    # Pick the most recent snapshot
    TARGET_BACKUP=$(ls -1td "${ARCHIVE_DIR}"/* 2>/dev/null | head -n 1)
fi

if [ -z "${TARGET_BACKUP}" ] || [ ! -d "${TARGET_BACKUP}" ]; then
    echo "❌ Error: No valid backup snapshot found to roll back to."
    exit 1
fi

echo "=== 🔄 Rolling back Open HEMS to snapshot: ${TARGET_BACKUP} ==="

# 1. Restore files to local add-on directory
echo "Step 1: Restoring files from archive..."
rm -rf "${ADDON_DIR}"/*
cp -r "${TARGET_BACKUP}"/* "${ADDON_DIR}/"

# 2. Sync restored files to /share and host /addons/
echo "Step 2: Pushing restored files to Home Assistant host..."
cp -r "${ADDON_DIR}"/* /share/addons/open-hems/
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
    "cp -r /share/addons/open-hems/* /addons/open-hems/ && ha store reload"

# 3. Rebuild and restart container in Home Assistant
echo "Step 3: Rebuilding and restarting Home Assistant App..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "${SSH_KEY}" -p "${SSH_PORT}" "root@${SSH_HOST}" \
    "ha apps rebuild local_open_hems && ha apps restart local_open_hems"

RESTORED_VER=$(python3 "${ADDON_DIR}/scripts/version_manager.py" status | awk '{print $NF}')
echo "=== ✅ Rollback Complete: Open HEMS ${RESTORED_VER} is restored and running! ==="
