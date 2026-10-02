# Changelog

All notable changes to the `Langfuse` Home Assistant Add-on will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.0.0-1] - 2026-09-29

### Added
- Initial scaffolding and architecture for Langfuse v4 Home Assistant Add-on.
- Full multi-service architecture specification: PostgreSQL 16, ClickHouse 26.4, Redis 7.2, SeaweedFS (S3), Langfuse Web & Worker.
- Dynamic optional port mapping (`ports: 3000/tcp: null`) allowing Ingress-only default or user-defined LAN exposure.
- Open Knowledge Format (OKF v0.2) architectural knowledge bundle in `knowledge/`.
- Automated pre-commit linting, manifest verification (`scripts/test_config.py`), and secret scanning (`scripts/security_audit.sh`).
- Cold backup integration (`backup: cold`) and 120s graceful shutdown timeout (`timeout: 120`).
- Automated 30-day data retention engine specification.
