#!/usr/bin/with-contenv bashio
# ==============================================================================
# Open HEMS Add-on Entrypoint
# ==============================================================================

bashio::log.info "Starting Open HEMS..."

CONFIG_PATH=$(bashio::config 'site_config')
AUTO_DISPATCH=$(bashio::config 'auto_dispatch')
SYNC_INTERVAL=$(bashio::config 'sync_interval_minutes')
LOG_LEVEL=$(bashio::config 'log_level')

bashio::log.info "Site configuration: ${CONFIG_PATH}"
bashio::log.info "Auto dispatch enabled: ${AUTO_DISPATCH}"
bashio::log.info "Sync interval: ${SYNC_INTERVAL} minutes"

# Start internal Ingress web server & scheduler daemon
export PYTHONPATH="/opt/open-hems:${PYTHONPATH}"
exec python3 /opt/open-hems/daemon.py \
    --config "${CONFIG_PATH}" \
    --interval "${SYNC_INTERVAL}" \
    --port 8099
