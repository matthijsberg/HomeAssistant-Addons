#!/usr/bin/env bash
# ==============================================================================
# Publish Open HEMS Add-on to GitHub
# ==============================================================================
set -e

REPO_NAME="matthijsberg/ha-addon-open-hems"

echo "Checking GitHub repository ${REPO_NAME}..."
if ! gh repo view "${REPO_NAME}" &>/dev/null; then
    echo "Repository does not exist yet. Creating public repository ${REPO_NAME}..."
    gh repo create "${REPO_NAME}" --public --description "Open HEMS: Predictive Home Energy Management System for Home Assistant" --source=. --remote=origin
    echo "Repository created successfully!"
fi

echo "Pushing main branch to GitHub..."
git push -u origin main

echo "Done! Open HEMS is live on GitHub at: https://github.com/${REPO_NAME}"
