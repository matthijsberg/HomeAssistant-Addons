---
id: ingress-basepath
title: "Ingress Runtime Base-Path Substitution"
type: "Architectural Decision"
status: "active"
trust: "human_reviewed"
tags: [ingress, nextjs, base-path, nginx, web-ui]
---

# Ingress Runtime Base-Path Substitution

## Context & Rationale
Home Assistant Ingress routes traffic through a dynamic sub-path generated per session/add-on instance (`/api/hassio_ingress/<token>/`). Next.js compiles asset URLs (`/_next/...`) and router base paths statically at build time via `NEXT_PUBLIC_BASE_PATH`. 

Pre-built Docker images cannot adapt to runtime Ingress tokens without rewriting, and runtime nginx regex rewriting of unprefixed Next.js bundles leads to broken chunk imports and hydration failures.

## Normative Contracts & Invariants

1. **Build-Time Placeholder Contract:**
   - The custom Langfuse Web image MUST be compiled with:
     `NEXT_PUBLIC_BASE_PATH=/__LF_BASEPATH_PLACEHOLDER__`
2. **Runtime Substitution (`init-basepath`):**
   - At container launch, `init_basepath.sh` queries Supervisor via `bashio::addon.ingress_entry` to obtain the actual Ingress prefix.
   - If the active token differs from the stored marker, pristine web assets are copied to a runtime staging directory and all occurrences of `/__LF_BASEPATH_PLACEHOLDER__` are replaced with the concrete Ingress token across `.js`, `.json`, `.html`, `.css`, and `.rsc` files.
   - The runtime directory lives strictly in ephemeral container storage, never polluting `/data` or backup archives.
3. **Deep-Link Persistence:**
   - Navigating directly or hard-refreshing on deep links (e.g. `.../project/xyz/traces/123`) must resolve correctly through the Ingress proxy without triggering 404 Not Found.
