# Mantis Security Agent — Home Assistant Add-on

Autonomous, sandboxed code & configuration security auditing service based on Google Mantis (`google/mantis`), exposing a Model Context Protocol (MCP) server for Hermes Agent, multi-agent workflows, and developer CI.

---

## Features

- **Static-First & Zero Host Execution**: No target code or untrusted scripts run on the Home Assistant OS host.
- **Deterministic Pre-Pass**: Combines Semgrep CE, Gitleaks secret detection, ShellCheck, Hadolint, and a custom Home Assistant deployment-surface extractor.
- **Human Egress Approval (`confirm` mode)**: No code leaves the host without explicit operator approval via the Home Assistant Ingress web panel.
- **MCP Server**: Native JSON-RPC 2.0 interface supporting synchronous PR reviews (`mantis_review_diff`) and asynchronous repo campaigns (`mantis_start_audit`).
- **Read-Only Invariant**: All repository mounts are read-only; remediation patches are returned strictly as unified diffs verified with `git apply --check`.

---

## MCP Tools

| Tool | Purpose |
|---|---|
| `mantis_review_diff` | Fast synchronous security review of a git diff/patch before commit. |
| `mantis_start_audit` | Asynchronous deep security campaign across repos or HA configs. |
| `mantis_get_report` | Poll status, retrieve findings, and inspect advisory report. |
| `mantis_cancel_audit` | Cancel an active or waiting audit job. |
| `mantis_list_jobs` | List recent security review jobs with metrics and status. |
