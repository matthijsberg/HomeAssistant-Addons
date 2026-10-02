#!/bin/bash
# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────
# TLS certificate setup (sourced by run.sh and by tests).
#
# Consumes from the caller's environment:
#   CERTS_DIR          existing self-signed CA/server cert directory
#   USE_HA_SSL_CERT     "true"/"false"
#   HA_SSL_DIR          directory to look for the HA-managed cert in (/ssl in prod)
#   HA_SSL_CERTFILE     filename within HA_SSL_DIR, e.g. fullchain.pem
#   HA_SSL_KEYFILE      filename within HA_SSL_DIR, e.g. privkey.pem
#
# Leaves $CERTS_DIR/server.crt + server.key in place either way. The
# self-signed CA (ca.crt/ca.key) is untouched/unconditional — harmless to
# keep even when using an HA-managed cert.
# ─────────────────────────────────────────────────────────────────────

if ! declare -f log >/dev/null 2>&1; then
  ADDON_VERSION="${ADDON_VERSION:-unknown}"
  log() {
    local now
    now="$(date +'%Y-%m-%d %H:%M:%S')"
    echo "[$now] [v${ADDON_VERSION}] $*"
  }
fi

configure_tls_certs() {
    if [ "$USE_HA_SSL_CERT" = "true" ]; then
        local ha_cert="$HA_SSL_DIR/$HA_SSL_CERTFILE"
        local ha_key="$HA_SSL_DIR/$HA_SSL_KEYFILE"
        if [ -f "$ha_cert" ] && [ -f "$ha_key" ]; then
            log "[run] Using HA-managed TLS certificate from $ha_cert"
            cp "$ha_cert" "$CERTS_DIR/server.crt"
            cp "$ha_key" "$CERTS_DIR/server.key"
            chmod 600 "$CERTS_DIR/server.key"
            return 0
        else
            log "[run] WARNING: use_ha_ssl_cert is true but $ha_cert / $ha_key not found — falling back to self-signed"
            USE_HA_SSL_CERT="false"
        fi
    fi

    if [ ! -f "$CERTS_DIR/server.crt" ]; then
        log "[run] Generating self-signed TLS certificates..."
        openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
            -keyout "$CERTS_DIR/ca.key" -out "$CERTS_DIR/ca.crt" \
            -days 3650 -subj "/CN=Hermes Agent CA" 2>/dev/null
        openssl req -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
            -keyout "$CERTS_DIR/server.key" -out /tmp/server.csr \
            -subj "/CN=hermes-agent" 2>/dev/null
        LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
        openssl x509 -req -in /tmp/server.csr \
            -CA "$CERTS_DIR/ca.crt" -CAkey "$CERTS_DIR/ca.key" \
            -CAcreateserial -out "$CERTS_DIR/server.crt" \
            -days 3650 -extfile <(printf "subjectAltName=DNS:hermes-agent,DNS:localhost,IP:127.0.0.1,IP:%s" "$LAN_IP") 2>/dev/null
        rm -f /tmp/server.csr "$CERTS_DIR/ca.srl"
        chmod 600 "$CERTS_DIR/server.key" "$CERTS_DIR/ca.key"
        log "[run] TLS certificates generated (CA + server)"
        log "[run] Install $CERTS_DIR/ca.crt on clients to avoid browser warnings"
    else
        log "[run] TLS certificates: using existing"
    fi
}
