#!/usr/bin/env bash
set -e

echo "=== Home Assistant Matrix Add-on Setup ==="

# Check directory layout
if [ ! -f "config.yaml" ] || [ ! -f "Dockerfile" ]; then
    echo "❌ Error: Run this script from the project root (/config/addons/matrix)"
    exit 1
fi

echo "✓ Project directory structure valid."

# Check permissions
chmod +x run.sh 2>/dev/null || true
if [ -d "scripts" ]; then
    chmod +x scripts/*.sh 2>/dev/null || true
fi

echo "✓ Executable permissions configured."
echo "=== Setup complete! Next step: run bash scripts/demo.sh ==="
