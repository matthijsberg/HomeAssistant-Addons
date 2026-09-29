#!/usr/bin/env bash
set -e

# Source bashio if present (running in Home Assistant Add-on container)
if [ -f "/usr/lib/bashio/bashio.sh" ]; then
  # shellcheck source=/dev/null
  source "/usr/lib/bashio/bashio.sh"
  INGRESS_ENTRY="$(bashio::addon.ingress_entry)"
else
  # Fallback for standalone/local dev testing
  INGRESS_ENTRY="${INGRESS_ENTRY:-/api/hassio_ingress/local-dev}"
fi

MARKER_FILE="/run/langfuse_basepath_token"
LAST_TOKEN=""
if [ -f "${MARKER_FILE}" ]; then
  LAST_TOKEN="$(cat "${MARKER_FILE}")"
fi

PRISTINE_DIR="/opt/langfuse-web-pristine"
RUNTIME_DIR="/opt/langfuse-web"

echo "[init-basepath] Active Home Assistant Ingress prefix: ${INGRESS_ENTRY}"

if [ "${LAST_TOKEN}" != "${INGRESS_ENTRY}" ] || [ ! -d "${RUNTIME_DIR}" ]; then
  echo "[init-basepath] Ingress token changed or runtime missing. Copying pristine assets and substituting base path..."
  
  rm -rf "${RUNTIME_DIR}"
  mkdir -p "${RUNTIME_DIR}"
  
  if [ -d "${PRISTINE_DIR}" ]; then
    cp -r "${PRISTINE_DIR}/." "${RUNTIME_DIR}/"
    
    echo "[init-basepath] Replacing /__LF_BASEPATH_PLACEHOLDER__ with ${INGRESS_ENTRY} across text assets..."
    # Find all relevant web assets and replace the placeholder
    find "${RUNTIME_DIR}" -type f \( -name "*.js" -o -name "*.json" -o -name "*.html" -o -name "*.css" -o -name "*.rsc" -o -name "*.txt" \) \
      -exec sed -i "s|/__LF_BASEPATH_PLACEHOLDER__|${INGRESS_ENTRY}|g" {} +
    
    echo "${INGRESS_ENTRY}" > "${MARKER_FILE}"
    echo "[init-basepath] Base-path replacement complete."
  else
    echo "⚠️ Warning: ${PRISTINE_DIR} not found. Running in dev/mock container."
    echo "${INGRESS_ENTRY}" > "${MARKER_FILE}"
  fi
else
  echo "[init-basepath] Ingress token unchanged (${LAST_TOKEN}). Re-using existing runtime assets."
fi
