# Update Log

## 2026-10-01
* **Creation**: Established OKF v0.2 bundle with 7 concepts (architecture, security, operations, integrations).
* **Upstream sync**: Ported upstream v1.3.1–v1.3.4 into add-on 2.4.0; recorded in [`upstream-lineage`](architecture/upstream_lineage.md).
* **Incident**: Dashboards crashed on every start since 2026-09-29 (`ModuleNotFoundError: ruamel`) after Hermes became self-managed; invariant added to [`python-runtime`](architecture/python_runtime.md).
* **Incident**: `hermes update` stopped all three gateways as unmanaged (2026-10-01 21:20); resolved by the `--external-supervisor` handback in [`gateway-supervision`](architecture/gateway_supervision.md).
* **Fix**: Without `homeassistant_token`, `HASS_URL` is forced to `http://supervisor/core` ([`home-assistant`](integrations/home_assistant.md)).
* **Correction**: 2.3.2 notes claimed `chmod 600` for `.htpasswd` and `.hermes_profile`; neither was implemented. `.hermes_profile` is now 0600; `.htpasswd` stays 0644 by design ([`access-boundaries`](security/access_boundaries.md)).
* **Hardening (2.5.0)**: MCP server pinned and TLS bypasses removed; profile `config.yaml` now `0600` and comment-preserving ([`home-assistant`](integrations/home_assistant.md), [`access-boundaries`](security/access_boundaries.md)).
* **Hardening (2.5.0)**: `docker-default`-based AppArmor profile introduced in complain mode with an explicit promotion rule ([`access-boundaries`](security/access_boundaries.md)).
* **Fix (2.5.0)**: Persistent per-name port slots ([`profile-topology`](architecture/profile_topology.md)); SQLite-consistent periodic backups ([`backups`](operations/backups.md)); sensors work without the API server; version derived from the build.
* **Incident (2.4.0 live)**: Gateways started without Telegram after the Python 3.14 venv migration because the launcher's `/dev/null` root mask broke Hermes' dependency selection; fixed in 2.5.0 ([`python-runtime`](architecture/python_runtime.md)). Dashboards and the venv migration were confirmed working on the live install.
* **Verification (2.5.0 live)**: Telegram and WhatsApp connected, all profile routes 200, auth enforced, sensors online, secrets 0600, MCP via Supervisor proxy. AppArmor complain-mode observation started 2026-10-01 20:11 UTC; zero events so far. The old profile had denied Chromium crashpad ptrace (2026-09-30).
