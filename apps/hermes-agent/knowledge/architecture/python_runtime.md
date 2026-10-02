---
id: architecture/python_runtime
title: "Hermes Install, Python Pin and Self-Managed Runtime"
type: "Architectural Decision"
description: "How the shared Hermes checkout and venv are installed, when they are rebuilt, and how the add-on coexists with Hermes' self-managed dependency store."
status: active
trust: unverified
tags: [install, venv, python, uv, bootstrap, dashboard]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: run-sh
    resource: "run.sh"
    title: "install_hermes_core, required_python_version, hermes_runtime_works"
  - id: hermes-bootstrap
    resource: "/config/.hermes/hermes-agent/hermes_bootstrap.py"
    title: "Hermes entry-point bootstrap (prepare_launch / activate_dependencies)"
---

# Hermes Install, Python Pin and Self-Managed Runtime

## Layout
- One shared clone: `/config/.hermes/hermes-agent` (`SRC_DIR`), one venv `SRC_DIR/venv`.
- Install marker `/config/.hermes_install` = `git_url|ref|HEAD|subpackages`.

## Rebuild rules (upstream v1.3.4)
1. Python version = checkout `.python-version` (`3.MINOR[.PATCH]`); absent ⇒ `3.11`.
   Invalid content is fatal before the venv is touched.
2. Health probe: venv Python matches the pin **and** `import hermes_cli.main,
   hermes_cli.config` succeeds under a throwaway `HOME`/`HERMES_HOME`.
3. Probe fails ⇒ move venv aside, `uv venv --python <pin>`, reinstall, re-probe, then
   write the marker. On failure the previous venv is restored and startup aborts.
4. Probe passes but marker differs ⇒ in-place `uv pip install -e` (extra user
   packages survive). An interpreter migration drops manually added packages.

## Self-managed Hermes (install-stamp `updateMechanism: "self"`)
Current Hermes manages its own dependency generations inside the checkout and
selects them in `hermes_bootstrap`, which every Hermes entry point imports first.
A process that imports Hermes modules **without** the bootstrap may be re-executed on
the managed interpreter with no dependencies. Observed on 2026-09-29: every dashboard
crashed with `ModuleNotFoundError: No module named 'ruamel'`.

**Invariant:** any add-on code that imports Hermes modules directly must import
`hermes_bootstrap` first (tolerating `ModuleNotFoundError` for older revisions).
The dashboard launch and probe in `run.sh` (`DASHBOARD_BOOT`) follow this. The
gateway launcher imports `hermes_bootstrap` **before** anything else (fork divergence,
2.5.0). Upstream's launcher masks `get_default_hermes_root()` as `/dev/null` while
importing `hermes_cli.main`; letting the bootstrap run inside that window resolved
the package-manager store under `/dev/null` and dropped lazily provisioned backends
(Telegram) on 2026-10-01.

**Probe invariant:** the venv health probe runs with `HERMES_DISABLE_LAZY_INSTALLS=1`
so it never performs a source-update completion into its throwaway root; a venv
interpreter then falls back to its own packages, so the probe cannot loop rebuilds.

## `hermes` backup wrapper
`venv/bin/hermes` is replaced by a bash wrapper (marker `hermes-addon-backup-wrapper`)
that defaults `hermes backup` to `/backup/hermes`. Any reinstall regenerates
`bin/hermes`, so `run.sh` re-wraps on every start when the marker is missing.
