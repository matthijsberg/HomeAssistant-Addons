# Changelog

All notable changes to the Hermes Agent Home Assistant add-on are documented here.

The format follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match the add-on `version` in `config.yaml`.

## [2.6.0] - 2026-10-01

### Added

- **Assist Conversation Agent:** New Home Assistant integration **Hermes Agent** (`ha_integration/custom_components/hermes_agent`) that makes Hermes selectable as the conversation agent of an Assist pipeline. Each request is routed to the profile of the Home Assistant user who made it; voice satellites use the primary profile. Follow-up questions continue the same Hermes session, and answers are kept short and speech-friendly.
- **MQTT Discovery Sensors:** Status sensors are announced through MQTT discovery as a **Hermes Agent** device with one sub-device per profile. They have stable entities (`binary_sensor.hermes_agent_<profile>`, `…_gateway`, `…_api`, `sensor.hermes_agent_app_version`) that survive Home Assistant restarts and become unavailable when the App stops. Without MQTT the previous REST sensors are used.
- **Supervisor Watchdog:** `watchdog` now points at the App's ingress `/health`, so Supervisor restarts the App if it stops responding (enable *Watchdog* on the App's Info page).

### Security

- **Closed-by-Default Messaging:** Profiles without any messaging allowlist get `GATEWAY_ALLOWED_USERS=homeassistant`. Home Assistant events keep working; a messaging platform enabled later rejects unknown senders until its own allowlist is set. Existing allowlists are never changed.

### Fixed

- **Documentation:** The Assist section described a "Server URL" field that the built-in OpenAI Conversation integration does not have. It now documents the bundled integration.

## [2.5.0] - 2026-10-01

### Security

- **Pinned MCP Server:** The Home Assistant MCP server is pinned to `@orellbuehler/homeassistant-mcp@0.8.0` instead of downloading whatever version is latest on every start.
- **No TLS Bypasses:** Removed `NODE_TLS_REJECT_UNAUTHORIZED=0` from the MCP block and `curl -k` from HA user sync. Use the default internal Supervisor URL, or a custom `https://` URL with a valid certificate.
- **Profile Config Permissions:** A profile's `config.yaml` holds the HA token for the MCP server, so it is now `chmod 600`.
- **AppArmor Profile:** The old profile placed no real restrictions on the container. It is replaced by one based on Docker's `docker-default` (Docker capability set, signal/ptrace limited to this App, `/proc` and `/sys` write denials, no `mount`), which also blocks writes to the App's own scripts. It ships in **complain mode**, so anything not yet covered is logged rather than blocked; it switches to enforce mode once the audit log stays clean.

### Fixed

- **Telegram and Other Lazy Backends (2.4.0 regression):** The upstream gateway launcher masked Hermes' home as `/dev/null` while importing Hermes. On self-managed Hermes that made dependency selection fail (`source-update completion failed: … /dev/null/installs/…`), so gateways ran without lazily provisioned backends (`Platform 'telegram' … adapter creation failed`). The launcher now runs `hermes_bootstrap` with the real home first.
- **Venv Probe Side Effects:** The startup health probe no longer runs a full Hermes source-update into a temporary directory on every venv (re)build (`HERMES_DISABLE_LAZY_INSTALLS=1`).
- **Config Comments Preserved:** Profile `config.yaml` updates now only touch the managed MCP and platform keys, keep your comments and formatting (via `ruamel.yaml`), write atomically, skip the write when nothing changed, and never overwrite a file that fails to parse.
- **Stable Ports per Profile:** Ports are tied to the profile name (`/config/.hermes_port_slots`) instead of list position, so reordering, removing or HA-syncing users no longer shifts ports. Existing installs keep their current ports.
- **Consistent Database Backups:** Periodic backups snapshot SQLite databases with the online backup API, so archives taken while agents are active hold a consistent copy including recent WAL writes. They no longer contain raw `-wal`/`-shm` files.
- **Sensors Without API Server:** With `enable_api` off, profile sensors report whether the gateway process is running instead of always showing `offline`. States are re-sent every 5 minutes, so they come back after a Home Assistant restart.
- **Single Version Source:** The App version now comes from the build (`BUILD_VERSION`, falling back to the manifest) instead of six hardcoded copies. A missing manifest no longer aborts startup.
- **Repository Hygiene:** Added a `.gitignore` covering caches, local venvs, secrets, archives and editor files.

### Recommended Action

- Clear `hass_url` and `homeassistant_token` in the App configuration. The App then uses the internal Supervisor connection (`http://supervisor/core` with the automatically rotated `SUPERVISOR_TOKEN`), and no long-lived token is stored in profile files.

### Verified

- `create_profile_backup` against a live WAL-mode writer with GNU tar 1.35: all 1000 rows that existed only in the WAL were present, `integrity_check` ok, non-SQLite `.db` archived raw, work directory removed.
- Port slots: fresh, reorder, remove, add, re-add and pool-exhaustion scenarios; a fresh install reproduces 8642/8643/8644.
- Config helpers with ruamel.yaml and with PyYAML: comments kept (ruamel), byte-identical on re-run, mode `0600`, unparseable file untouched.
- `apparmor.txt` compiles with AppArmor parser 4.1.7; gateway detection for sensors verified against a live process; upstream v1.3.4 suite unchanged (same 8 known fork-specific failures as 2.4.0); `scripts/setup.sh` quality gate passes (11 tests).
- Live Home Assistant install (2026-10-01, three profiles, Hermes `6afef023`, Python 3.14.7):
  - No dependency-selection error; Telegram connected (polling) and the WhatsApp bridge connected.
  - `/v1/health`, `/dashboard/`, `/dashboard/api/status`, `/hermes/` and dashboard assets returned 200 for all three profiles over HTTPS.
  - Unauthenticated `/hermes/` and `/v1/models` returned 401; Bearer `/v1/models` returned 200.
  - All sensors are `online` with `gateway_running` and `api_healthy` true and version `2.5.0` (taken from the build).
  - Profile `config.yaml` and `.hermes_profile` are `0600`; the MCP block is pinned, uses `http://supervisor/core` with the Supervisor token and has no TLS bypass; the port slots reproduce 8642/8643/8644.
  - AppArmor: no events since the new profile loaded. The only earlier denial (2026-09-30, old profile) blocked Chromium's crash handler (`chrome_crashpad` ptrace), which the new profile allows.

## [2.4.0] - 2026-10-01

Syncs upstream [hermes-ha-addon](https://github.com/WolframRavenwolf/hermes-ha-addon) v1.3.1–v1.3.4 into the fork (base was upstream v1.3.0). Architecture and change records live in the OKF bundle under `knowledge/`.

### Added

- **Per-Profile Gateway Supervision (upstream 1.3.1/1.3.2):** Each gateway runs under its own slot supervisor that owns the full process tree. Restarts happen only after every descendant has exited. Output goes to both the add-on log and `<profile>/logs/gateway.log`, and `hermes update` hands restarts back through `--external-supervisor` instead of stopping the gateways.
- **Authoritative API Settings (upstream 1.3.1):** API host, port, enablement and key, plus profile pinning and multiplexing, are re-applied after every Hermes env/config load and enforced on the final `GatewayConfig`.
- **Dashboard Supervision:** Crashed dashboards are restarted (at most once per 5 minutes), with a fresh session token and an nginx reload.
- **OKF v0.2 Knowledge Bundle:** `knowledge/` documents upstream lineage, gateway supervision, Python runtime, profile topology, security boundaries, backups and the HA integration. Validate it with `scripts/validate_okf.sh` or `tests/test_okf.py`.

### Fixed

- **Python Version Pin (upstream 1.3.4):** The Hermes checkout's `.python-version` is now honoured; checkouts without one default to 3.11. Broken or incompatible venvs are rebuilt even when the install marker matches, and the previous venv is restored if the rebuild fails. Interpreter migrations drop manually added venv packages.
- **Named-Profile Startup (upstream 1.3.3):** `gateway.standalone: true` is set on named profiles (including HA-synced users) before any gateway starts. This is feature-detected, so older Hermes revisions are unaffected.
- **Dashboard Crash on Self-Managed Hermes:** Dashboards now import `hermes_bootstrap` first. Previously they failed with `ModuleNotFoundError: No module named 'ruamel'` once Hermes switched to its own dependency store.
- **`hermes backup` Wrapper:** The wrapper is re-applied after any reinstall regenerates `bin/hermes`. Previously it was silently lost.
- **Secret File Mode:** `/config/.hermes_profile` (contains `HASS_TOKEN`/`GITHUB_TOKEN`) is now `chmod 600`, as the 2.3.2 notes already claimed. `.htpasswd` intentionally stays world-readable because it only contains an apr1 hash that nginx workers must read; the 2.3.2 claim was incorrect.
- **Zero-Config HA Access:** Without a `homeassistant_token`, the App now always talks to `http://supervisor/core`, because `SUPERVISOR_TOKEN` is rejected by Core's direct URL. The misleading `hass_url` default (`http://homeassistant.local:8123`) was removed for new installs. Installs with their own token are unaffected.
- **Fail-Fast Credential Validation:** Startup aborts if `api-server.sh` is missing instead of silently skipping credential validation.

### Changed

- **Option Help Text:** `access_password`, `enable_api` and `env_vars` descriptions now state the credential rules enforced since 2.2.0 (upstream 1.3.1).

### Verified

- Upstream v1.3.4 regression suite run against this fork (Python 3.14.7, offline): all Python-runtime, gateway supervisor/launcher/logger, API-credential, backup-policy and multi-profile tests pass. The remaining failures are fork-specific metadata or test-harness artifacts, plus one environment-specific test that also fails on pure upstream in the same container. Details are in `knowledge/architecture/upstream_lineage.md`.
- `bash -n` on all shell scripts, `py_compile` on all Python helpers, YAML parse of `config.yaml`, `build.yaml` and `translations/en.yaml`, and OKF validation (7 concepts, 0 errors) passed.
- Not yet verified on a live Home Assistant install. The first start migrates the venv from Python 3.11 to the checkout's pinned 3.14, which needs network access and takes several minutes.

## [2.3.2] - 2026-09-07

### Security

- **Direct Port Web Terminal Authentication:** Removed `auth_basic off` bypass on `/hermes/` and `/terminal/` routes so direct HTTP/HTTPS ports strictly require HTTP Basic Auth with `access_password`. Added a 403 Forbidden guard on direct ports if `access_password` is empty to prevent unauthenticated root terminal access.
- **Secure Default Port Mapping:** Unmapped `8443/tcp` by default in `config.yaml` (`ports: 8443/tcp: null`) so new installations do not expose ports on the LAN by default.
- **Nginx Security Headers:** Configured `server_tokens off`, `X-Content-Type-Options: nosniff`, `X-XSS-Protection: 1; mode=block`, `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN` (direct ports), and `Strict-Transport-Security` (HTTPS).
- **TLS Ciphers & Session Hardening:** Configured modern ECDHE cipher suites, `ssl_prefer_server_ciphers on`, and SSL session cache in Nginx direct ports configuration.
- **Strict Secret Permissions:** Enforced `chmod 600` on generated `.htpasswd`, `.hermes_profile`, and profile `.env` credential files.

## [2.3.1] - 2026-09-07

### Fixed

- **AppArmor Compatibility:** Revert to default supervisor AppArmor mode for reliable container execution across HAOS releases.

## [2.3.0] - 2026-09-07

### Added

- **Native Home Assistant Core API Access:** Enabled `homeassistant_api: true` in `config.yaml` to provide automatic `SUPERVISOR_TOKEN` injection for tokenless Core REST & WebSocket API communication.
- **Go Toolchain Upgrade:** Upgraded compiler toolchain to Go `1.27.1` in `Dockerfile`.
- **Home Assistant Terminology Standardization:** Conformed user-facing prose across documentation and manifests to the official Home Assistant "App" standard.

## [2.2.0] - 2026-09-07

### Added

- **Automated Periodic Profile Backups:** Background daemon in `backup-setup.sh` / `run.sh` periodically archives active profile data into `/backup/hermes/<profile>/` with configurable interval (`periodic_backup_interval_hours`, default 24h) and retention pruning (`periodic_backup_keep_count`, default 7).
- **Home Assistant Backup Size Optimization:** Incorporate upstream `backup_exclude` rules to exclude rebuildable virtualenvs, project `node_modules`, LSP packages, `.cache`, and `.npm` from HA backups.
- **API Credential Validation (`api-server.sh`):** Validate access passwords and environment variables to ensure safe single-line ASCII credentials and prevent `.env` syntax errors.
- **Gateway Process Identification:** Generate `$VENV_DIR/bin/hermes-gateway` symlink so the Hermes core dashboard accurately detects running gateway instances.

### Fixed

- **Multi-Profile Environment & Credential Isolation:** Use authoritative `set_owned_env_var` and `remove_env_var` in `profile-init.sh` to remove leaked `HERMES_HOME`, `HERMES_S6_SUPERVISED_CHILD`, and set `GATEWAY_MULTIPLEX_PROFILES=false`.
- **Git Clone HTTP/2 Workaround:** Configured `git config --system http.version HTTP/1.1` in `Dockerfile` to circumvent Debian Bookworm `libcurl-gnutls` HTTP/2 401 handshake failures against GitHub.

## [2.1.0] - 2026-07-27

### Added

- **Home Assistant Assist & Voice Pipeline Integration:** Full support for routing spoken voice queries from Home Assistant Assist / ESP32 satellites directly to Hermes.
- **Voice Assist Skill Guidelines:** Added TTS optimization guidelines to `skills/homeassistant/SKILL.md` for clean spoken audio synthesis without markdown tables.
- **Repository Release & Versioning Policy:** Standardized SemVer versioning rules and recorded release requirements in `SCOPE.md`.

## [2.0.0] - 2026-07-26

### Added

- **Auto HA User Sync & Smart Ingress Routing:** Discover active Home Assistant human users, auto-create dedicated Hermes profiles, `USER.md`, and `SOUL.md`, and auto-route logged-in HA sidebar users to their personal assistant.
- **Home Assistant MCP Server Integration:** Auto-inject `@orellbuehler/homeassistant-mcp` tools into each profile's `config.yaml` using `HASS_URL` and `HASS_TOKEN`.
- **Pre-Packaged HA Skill:** Ship a native `skills/homeassistant/SKILL.md` template for entity discovery, device control, and safety rules.
- **Home Assistant Sensor Reporter:** Periodically publish system and per-profile operational health (`sensor.hermes_agent`, `sensor.hermes_agent_<profile>`) to Home Assistant Core's REST API.
- **Configurable Log Levels:** Add `log_level` option to UI configuration (`trace`, `debug`, `info`, `notice`, `warning`, `error`, `fatal`) with timestamping and version tagging.

### Fixed

- **Multi-Profile Messaging Isolation:** Automatically disable shared external polling channels (Telegram, WhatsApp) on secondary profiles to prevent 409 long-poll conflicts and port 3000 collisions.
- **Socket Freeing on Restart:** Added `ss -tulpn` socket inspection and pre-bind port cleanup in `run.sh` to prevent `Errno 98: address already in use` during container restarts.
- **Primary Profile Promotion:** Automatically promote primary HA user to Profile 0 and migrate past memories and sessions from `.hermes`.

## [1.3.0] - 2026-07-19

### Added

- Add an opt-in Hermes Desktop remote backend on container port 9119 using the official `hermes serve` contract and the existing access password.
- Keep the Desktop port unmapped and the feature disabled by default; Home Assistant's Network settings choose the external host port.

### Security

- Require `access_password` whenever the Desktop backend is enabled.
- Warn that authenticated Desktop access provides full agent control, carries the accepted opt-in risk, and belongs only on a trusted LAN/VPN/Tailscale path rather than the public internet.

### Verified

- `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest discover -s tests -v` - 66 tests OK, 1 skipped.
- Shell syntax, Python AST, YAML parsing, public-repository hygiene, and `git diff --check` passed.
- An isolated Home Assistant test repository built and installed the add-on without changing the stopped production installation.
- The live remote backend enforced authentication, exposed the `default` and `worker` profiles, minted single-use WebSocket tickets, and rejected ticket reuse.
- The codesign-verified Hermes Desktop app connected through its real cookie-authenticated remote path and reached the expected provider onboarding for the credential-free test profile.
- Independent code reviews completed with no blocking findings.

## [1.2.1] - 2026-06-18

### Fixed

- Keep Dashboard Chat WebSockets open behind Home Assistant Ingress by enabling `ingress_stream` and rewriting proxied dashboard API `Origin` headers to the dashboard backend host.
- Fix direct-port Dashboard Chat WebSocket authentication when browsers send Basic auth plus the SPA `?token=` query parameter.

### Verified

- `git diff --check` - OK.
- `bash -n hermes_agent/nginx-render.sh hermes_agent/run.sh` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py` - OK.
- `uv run --with pytest python -m pytest -q tests/test_dashboard_ingress_patches.py` - 21 passed.
- `uv run --with pytest python -m pytest -q` - 53 passed, 1 skipped.
- Live Home Assistant local DEV add-on lifecycle smoke - `/dashboard/api/pty` streamed binary TUI frames, `/dashboard/api/ws` emitted `gateway.ready`, and `/dashboard/api/events` stayed open through both HA Ingress and direct HTTPS access.

## [1.2.0] - 2026-06-15

### Added

- Add `profiles_base` so bare multi-profile names can default to upstream-style `.hermes/profiles/<name>` storage.

### Changed

- Use one shared Hermes Agent clone and virtualenv for all profiles instead of installing Hermes separately for every profile.
- Preserve existing flat profile directories such as `/config/amy` during upgrades when the new `profiles_base` target does not exist yet.

### Fixed

- Fix the shared-install dashboard patch helper call so startup uses the shared `SRC_DIR` instead of removed per-profile variables.
- Correct Home Assistant option text and storage documentation for the shared install and `profiles_base` behavior.
- Fix the README OpenAI-compatible API authentication example formatting.

### Verified

- `git diff --check` - OK.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` - 53 tests OK, 1 skipped.
- `PYTHONDONTWRITEBYTECODE=1 /amy/hermes-agent/.venv/bin/python -m pytest -q` - 52 passed, 1 skipped.
- `bash -n hermes_agent/run.sh hermes_agent/profile-init.sh hermes_agent/nginx-render.sh` - OK.
- YAML parse checks for `repository.yaml`, `hermes_agent/build.yaml`, `hermes_agent/config.yaml`, and `hermes_agent/translations/en.yaml` - OK.
- Live Home Assistant local DEV add-on smoke - two profiles started, `nginx -t` passed, one shared Hermes clone/venv was reused, restart reused the shared install marker, primary and secondary Dashboard/Terminal/API routes returned HTTP 200, HA Ingress Dashboard WebSockets returned `HTTP/1.1 101 Switching Protocols` for `/api/pty`, `/api/ws`, and `/api/events` on both root and `/profile/amy`, and legacy flat profile preservation passed.

## [1.1.2] - 2026-06-15

### Added

- Add this Home Assistant add-on changelog so users can see what changed before updating.

### Changed

- Clarify persistent storage documentation: container `/config` maps to the add-on's private `addon_config` storage, not the normal Home Assistant Core `/config` folder.
- Document where to find the same storage from HAOS/Samba via the `addon_configs` share.
- Correct the install-marker name shown in the storage tree for multi-profile aware versions.

### Fixed

- Fix the dashboard Chat tab behind Home Assistant Ingress by preserving WebSocket upgrades through the nginx `/dashboard/api/` proxy.
- Forward `Upgrade` and mapped `Connection` headers for dashboard API routes while keeping normal REST calls on the same path sane.
- Add regression coverage for primary and multi-profile dashboard API routes across Ingress, direct HTTP, and direct HTTPS render modes.

### Verified

- `git diff --check` - OK.
- `python3 -m pytest -q tests/test_dashboard_ingress_patches.py` - 18 passed.
- `python3 -m pytest -q` - 43 passed, 1 skipped.
- `bash -n hermes_agent/nginx-render.sh hermes_agent/run.sh` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py` - OK.
- Live Home Assistant local DEV add-on smoke - add-on started, `nginx -t` passed, dashboard returned HTTP 200 through Ingress, and `/dashboard/api/pty`, `/dashboard/api/ws`, and `/dashboard/api/events` returned `HTTP/1.1 101 Switching Protocols`.

## [1.1.1] - 2026-06-04

### Fixed

- Fix dashboard startup regression by executing the installed dashboard patch helper directly instead of relying on a non-guaranteed `python` alias.
- Use the helper's `#!/usr/bin/env python3` shebang, matching the Python executable installed in the add-on image.
- Add a regression assertion so startup does not reintroduce the bare `python` dependency.

### Verified

- `python3 -m unittest discover -s tests` - 43 tests OK, 1 skipped.
- `python3 -m py_compile hermes_agent/dashboard-patches.py tests/test_dashboard_ingress_patches.py tests/test_multi_profile.py` - OK.
- `bash -n hermes_agent/run.sh hermes_agent/profile-init.sh hermes_agent/nginx-render.sh` - OK.

## [1.1.0] - 2026-06-01

### Added

- Add multi-profile mode via `profiles`; the first profile keeps the existing root URLs and additional profiles are exposed under `/profile/<name>/...`.
- Add Home Assistant-compatible `profile_env_vars` for per-profile `.env` overrides.
- Render nginx routing per profile for dashboard, terminal, and API access.
- Add profile initialization support and per-profile tmux/session naming.
- Add regression tests for multi-profile behavior.

### Changed

- Keep Hermes Gateway in the foreground under Home Assistant s6 so Supervisor tracks the add-on lifecycle correctly.
- Update README and translations for multi-profile support.

### Notes

- Includes the v1.0.8 gateway foreground fix that was tagged but not published as a GitHub Release.
- Thanks to @imcvampire for the multi-profile work.

### Verified

- `python3 -m unittest discover -s tests -q` - 43 tests OK, 1 skipped.
- `git diff --check` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py tests/test_dashboard_ingress_patches.py tests/test_multi_profile.py` - OK.
- `bash -n hermes_agent/run.sh hermes_agent/nginx-render.sh hermes_agent/profile-init.sh` - OK.

## [1.0.8] - 2026-05-28

### Fixed

- Keep Hermes Gateway in the foreground under Home Assistant s6 supervision.
- Ensure Supervisor can correctly track the add-on lifecycle instead of losing sight of a backgrounded gateway process.
- Add a regression test for the foreground gateway behavior.

### Notes

- Tagged as v1.0.8 and later included in the v1.1.0 GitHub Release notes.

## [1.0.7] - 2026-05-17

### Fixed

- Fix dashboard asset loading behind Home Assistant Ingress for modern Hermes dashboard builds.
- Add an add-on-controlled `import.meta.url` fallback for dashboard base-path detection so API, plugin, router, and asset paths keep working when Home Assistant Ingress uses a long random prefix.
- Force Vite to emit relative dashboard asset URLs with `base: "./"` for modern and legacy dashboard sources.
- Rebuild stale dashboard bundles when `index.html` still contains absolute `/assets/...` references.
- Keep legacy dashboard compatibility patches and partial-patch repair behavior.
- Avoid polling `/v1/health` when the API server is disabled, preventing misleading nginx `connect() failed` log noise.

### Changed

- Update the landing page so `/v1/health` is shown as optional API server health, not Gateway health.

### Notes

- Thanks to @imcvampire for the logs and browser Network screenshot that exposed the remaining asset-path failure.

### Verified

- `python3 -m pytest -q` - 13 passed.
- `git diff --check` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py` - OK.
- `bash -n hermes_agent/run.sh` - OK.
- Independent code review passed with no blocking findings.

## [1.0.6] - 2026-05-16

### Fixed

- Fix Home Assistant add-on startup failure introduced in v1.0.5 when direct HTTP/HTTPS ports are enabled.
- Move `map_hash_bucket_size 128;` into the main nginx template before the first `map` block.
- Remove the late duplicate directive from the direct-ports include.
- Add a regression test that locks the directive ordering.

### Verified

- Verified on a real Home Assistant add-on installation with a fresh `/config` runtime.
- Upgrade/start path reached `nginx reloaded` and `All services started`.
- Ingress landing page, dashboard, dashboard deep links, dashboard assets, dashboard API, and `/v1/health` returned 200.

### Notes

- Thanks to @imcvampire for reporting the post-v1.0.5 dashboard startup problem.

## [1.0.5] - 2026-05-16

### Fixed

- Fix Home Assistant add-on startup failures caused by fragile dashboard source patching after upstream Hermes dashboard changes.
- Move dashboard source rewriting into a tested helper script instead of multi-expression `sed` calls.
- Skip source patches for modern Hermes dashboards that already support proxy prefixes via `X-Forwarded-Prefix`.
- Repair partially patched v1.0.4 dashboard sources from failed previous starts.
- Keep legacy fallback patches for older root-path-only Hermes dashboard sources.
- Forward the dashboard prefix from Home Assistant Ingress or custom reverse proxies to Hermes.
- Add regression tests for modern upstream, legacy fallback, and the `sed` delimiter failure class.

### Notes

- Thanks to @imcvampire for pinpointing the `sed` delimiter crash in PR #4.

## [1.0.4] - 2026-04-24

### Fixed

- Pass a loopback `Host` header to the upstream dashboard when proxying through nginx.
- Improve dashboard reverse-proxy compatibility after the v1.0.3 dashboard hardening work.

### Notes

- Tagged as v1.0.4 but not published as a separate GitHub Release.

## [1.0.3] - 2026-04-20

### Fixed

- Make the dashboard work fully behind Home Assistant Ingress, direct `/dashboard/` mounts, and custom reverse proxies.
- Replace the previous `BASE = "."` workaround with runtime base-path computation from `import.meta.url`.
- Patch `api.ts`, `usePlugins.ts`, and `vite.config.ts` idempotently in-container so API calls, plugin assets, router paths, and built HTML assets stay correctly prefixed.
- Fix the landing-page Gateway indicator by switching the probe to the public `/v1/health` endpoint instead of authenticated `/v1/models`.
- Protect direct-port `/dashboard/api/*` routes with the dashboard session token while keeping `/dashboard/api/status` public for health/status use.

### Added

- Add a README Security Model section covering Home Assistant Ingress, direct-port dashboard auth, and `/v1/*` API Bearer auth.

## [1.0.2] - 2026-04-20

### Fixed

- Restore dashboard compatibility with Hermes Agent v0.10.0 after upstream added a global auth middleware requiring an ephemeral Bearer token on `/api/` endpoints.
- Read the dashboard token from the HTML on startup and inject it server-side through nginx so Basic Auth and Home Assistant Ingress do not strip or consume it.

## [1.0.1] - 2026-04-20

### Added

- Add Web Dashboard support as a new tab alongside Hermes and Terminal through the Home Assistant sidebar.
- Add direct HTTP/HTTPS dashboard exposure through the `enable_dashboard` toggle.
- Expose dashboard pages for status, sessions, analytics, logs, cron, skills, config, and API keys.

### Fixed

- Fix Supervisor loop behavior by tracking the gateway PID correctly and always restarting the gateway, preventing container death on manual gateway restart.

## [1.0.0] - 2026-03-27

### Added

- Initial stable Home Assistant add-on packaging for Hermes Agent.
- Persistent AI agent with SQLite FTS5 long-term memory, self-improving skills, and multi-platform messaging.
- OpenAI-compatible API under `/v1/` for frontends such as Open WebUI and SillyTavern.
- Persistent web terminals backed by tmux through the Home Assistant sidebar.
- HTTP and HTTPS direct LAN access with auto-generated TLS certificates and optional Basic Auth.
- Full persistence for source code, venv, Homebrew, npm, Go, and agent data across add-on updates.
- Editable install so the agent can read and modify its own source code.
- Plugin architecture for custom tools, commands, and hooks.
- Container toolchain with Go 1.26, Node.js 22, Python 3.11, Chromium, GitHub CLI, Homebrew, uv, and common development tools.
- Support for `amd64` and `aarch64`.
