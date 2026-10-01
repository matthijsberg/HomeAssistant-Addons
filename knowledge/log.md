# Update Log

## 2026-10-01
* **Creation**: Established OKF v0.2 bundle with 7 concepts (architecture, security, operations, integrations).
* **Upstream sync**: Ported upstream v1.3.1–v1.3.4 into add-on 2.4.0; recorded in [`upstream-lineage`](architecture/upstream_lineage.md).
* **Incident**: Dashboards crashed on every start since 2026-09-29 (`ModuleNotFoundError: ruamel`) after Hermes became self-managed; invariant added to [`python-runtime`](architecture/python_runtime.md).
* **Incident**: `hermes update` stopped all three gateways as unmanaged (2026-10-01 21:20); resolved by the `--external-supervisor` handback in [`gateway-supervision`](architecture/gateway_supervision.md).
* **Fix**: Without `homeassistant_token`, `HASS_URL` is forced to `http://supervisor/core` ([`home-assistant`](integrations/home_assistant.md)).
* **Correction**: 2.3.2 notes claimed `chmod 600` for `.htpasswd` and `.hermes_profile`; neither was implemented. `.hermes_profile` is now 0600; `.htpasswd` stays 0644 by design ([`access-boundaries`](security/access_boundaries.md)).
