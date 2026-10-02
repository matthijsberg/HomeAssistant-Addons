---
id: dual-nginx-listeners
title: "Dual Nginx Listeners and Network Boundary Policy"
type: "Security Standard"
status: "active"
trust: "human_reviewed"
tags: [nginx, security, ingress, network-policy, isolation]
---

# Dual Nginx Listeners and Network Boundary Policy

## Context & Rationale
Home Assistant Ingress and external LAN API clients have conflicting network requirements:
- Ingress must accept connections exclusively from the Home Assistant Supervisor reverse proxy (`172.30.32.2`) and strip frame-blocking security headers.
- LAN/API clients connect on the standard port (e.g. 3000) using bare root URLs (`http://<host>:3000/api/...`) without Ingress path prefixes.

## Normative Contracts & Invariants

1. **Ingress Listener (Port 8099):**
   - Binds to `0.0.0.0:8099`.
   - Access control strictly enforced: `allow 172.30.32.2; deny all;`.
   - Unconditionally strips `X-Frame-Options` and overrides `Content-Security-Policy` `frame-ancestors` to allow embedding inside the Home Assistant iframe.
   - Prepends the Ingress token to upstream path headers.
2. **LAN / API Listener (Port 3000):**
   - Binds to `0.0.0.0:3000`.
   - Accessible only if the user explicitly configures `ports: 3000/tcp: <port>` in Home Assistant settings (otherwise remains unmapped on the host).
   - Serves API routes (`/api/public/*`, `/api/otlp/*`) directly to SDK callers.
   - Enforces full Langfuse authentication and project API key validation on all endpoints.
3. **Loopback Binding for Internal Services:**
   - PostgreSQL (`:5432`), Redis (`:6379`), ClickHouse (`:8123`, `:9000`), SeaweedFS (`:8333`), and Langfuse Web (`:3100`) MUST bind strictly to `127.0.0.1`.
   - They are physically unreachable from other add-ons on the `hassio` Docker network.
