"""Regression tests for fork-specific behaviour (see knowledge/ for the contracts)."""

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ADDON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADDON))

import ha_mcp_config  # noqa: E402
import ha_platform_config  # noqa: E402
import ha_sensor_reporter  # noqa: E402


def _bash(script: str, home: Path) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


# ── config helpers ──────────────────────────────────────────────────────
CONFIG = textwrap.dedent("""\
    # my model choice
    model: gemini-flash   # keep this
    platforms:
      telegram:
        enabled: true
    mcp_servers:
      other: {command: foo}
    """)


def test_mcp_block_is_pinned_without_tls_bypass_and_private(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    ha_mcp_config.configure_ha_mcp(str(cfg), "http://supervisor/core", "TOKEN", "")
    text = cfg.read_text()
    assert "@orellbuehler/homeassistant-mcp@" in text
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in text
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert "other" in text


def test_config_helpers_are_idempotent(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    for _ in range(2):
        ha_mcp_config.configure_ha_mcp(str(cfg), "http://supervisor/core", "TOKEN", "")
        ha_platform_config.disable_secondary_platforms(str(cfg))
    first = cfg.read_bytes()
    ha_mcp_config.configure_ha_mcp(str(cfg), "http://supervisor/core", "TOKEN", "")
    ha_platform_config.disable_secondary_platforms(str(cfg))
    assert cfg.read_bytes() == first


def test_comments_survive_when_ruamel_is_available(tmp_path):
    pytest.importorskip("ruamel.yaml")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    ha_mcp_config.configure_ha_mcp(str(cfg), "http://supervisor/core", "TOKEN", "")
    ha_platform_config.disable_secondary_platforms(str(cfg))
    assert "# my model choice" in cfg.read_text()
    assert "# keep this" in cfg.read_text()


def test_unparseable_config_is_never_overwritten(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("bad: [\n")
    ha_mcp_config.configure_ha_mcp(str(cfg), "http://supervisor/core", "TOKEN", "")
    assert cfg.read_text() == "bad: [\n"


# ── port slots ──────────────────────────────────────────────────────────
def _slots(home: Path, names: str) -> str:
    script = f"""
        source {ADDON}/profile-init.sh
        PROFILE_NAMES=({names})
        assign_port_slots || exit $?
        for i in "${{!PROFILE_NAMES[@]}}"; do printf '%s=%s ' "${{PROFILE_NAMES[$i]}}" "${{PROFILE_PORT_SLOTS[i]}}"; done
    """
    result = _bash(script, home)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_port_slots_follow_names_not_order(tmp_path):
    assert _slots(tmp_path, "alice bob carol") == "alice=0 bob=1 carol=2"
    assert _slots(tmp_path, "carol alice bob") == "carol=2 alice=0 bob=1"
    assert _slots(tmp_path, "alice carol") == "alice=0 carol=2"
    assert _slots(tmp_path, "alice carol Guest") == "alice=0 carol=2 Guest=3"
    assert _slots(tmp_path, "alice bob carol Guest") == "alice=0 bob=1 carol=2 Guest=3"


# ── backups ─────────────────────────────────────────────────────────────
@pytest.mark.skipif(
    shutil.which("tar") is None
    or "GNU" not in subprocess.run(["tar", "--version"], capture_output=True, text=True).stdout,
    reason="add-on image uses GNU tar",
)
def test_backup_snapshots_wal_databases(tmp_path):
    home = tmp_path / "home"
    (home / "sub").mkdir(parents=True)
    (home / "notes.md").write_text("hi")
    (home / "fake.db").write_text("not sqlite")
    writer = sqlite3.connect(home / "state.db")
    writer.execute("pragma journal_mode=wal")
    writer.execute("pragma wal_autocheckpoint=0")
    writer.execute("create table t(x)")
    writer.executemany("insert into t values (?)", [(i,) for i in range(500)])
    writer.commit()  # rows live only in the WAL while the writer stays open

    script = f"""
        source {ADDON}/backup-setup.sh
        BACKUP_ROOT={tmp_path}/backup
        PROFILE_HOMES=({home}); PROFILE_NAMES=(tester)
        create_profile_backup 0
    """
    result = _bash(script, tmp_path)
    writer.close()
    assert result.returncode == 0, result.stderr + result.stdout

    archives = list((tmp_path / "backup" / "tester").glob("hermes-backup-tester-*.tar.gz"))
    assert len(archives) == 1
    assert not list((tmp_path / "backup" / "tester").glob(".work.*"))
    out = tmp_path / "out"
    out.mkdir()
    members = subprocess.run(
        ["tar", "-tzf", str(archives[0])], check=True, capture_output=True, text=True
    ).stdout.split()
    assert "./state.db" in members
    assert not any(m.endswith(("-wal", "-shm")) for m in members)
    subprocess.run(["tar", "-xzf", str(archives[0]), "-C", str(out)], check=True)
    restored = sqlite3.connect(out / "state.db")
    assert restored.execute("select count(*) from t").fetchone()[0] == 500
    assert restored.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert (out / "fake.db").read_text() == "not sqlite"


# ── sensors ─────────────────────────────────────────────────────────────
def test_sensor_detects_gateway_by_working_directory(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "/x/gateway-launcher.py", "gateway", "run"],
        cwd=tmp_path,
    )
    try:
        import time
        time.sleep(0.5)
        assert ha_sensor_reporter.gateway_running(str(tmp_path))
        assert not ha_sensor_reporter.gateway_running(str(tmp_path / "elsewhere"))
    finally:
        proc.kill()
        proc.wait()


# ── version source ──────────────────────────────────────────────────────
def test_version_comes_from_env_then_manifest(tmp_path):
    head = (ADDON / "run.sh").read_text().splitlines()
    start = next(i for i, l in enumerate(head) if l.startswith("# Single source of truth"))
    end = next(i for i, l in enumerate(head) if l.startswith('ADDON_VERSION="${ADDON_VERSION:-unknown}"'))
    snippet = "\n".join(["#!/bin/bash", "set -euo pipefail", *head[start:end + 1], 'echo "$ADDON_VERSION"'])
    script = tmp_path / "run.sh"
    script.write_text(snippet)
    shutil.copy(ADDON / "config.yaml", tmp_path / "config.yaml")
    env = {"PATH": "/usr/bin:/bin"}
    manifest = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert manifest.stdout.strip() == __import__("yaml").safe_load(open(ADDON / "config.yaml"))["version"]
    override = subprocess.run(["bash", str(script)], env={**env, "ADDON_VERSION": "9.9.9"}, capture_output=True, text=True)
    assert override.stdout.strip() == "9.9.9"
    (tmp_path / "config.yaml").unlink()
    missing = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert missing.returncode == 0 and missing.stdout.strip() == "unknown"


# ── MQTT discovery ──────────────────────────────────────────────────────
def test_mqtt_discovery_entities_are_stable_and_grouped():
    msgs = dict(ha_sensor_reporter.discovery_messages(["alice", "bob"], "1.2.3", api_enabled=True))
    status = msgs["homeassistant/binary_sensor/hermes_agent_alice/status/config"]
    assert status["unique_id"] == "hermes_agent_alice_status"
    assert status["default_entity_id"] == "binary_sensor.hermes_agent_alice"
    assert status["device_class"] == "connectivity"
    assert status["availability"] == [{"topic": "hermes_agent/status"}]
    assert status["device"]["via_device"] == "hermes_agent_app"
    assert "homeassistant/binary_sensor/hermes_agent_bob/api/config" in msgs
    version = msgs["homeassistant/sensor/hermes_agent/version/config"]
    assert version["device"]["sw_version"] == "1.2.3"
    without_api = dict(ha_sensor_reporter.discovery_messages(["alice"], "1.2.3", api_enabled=False))
    assert not any(t.endswith("/api/config") for t in without_api)


# ── messaging allowlist default ─────────────────────────────────────────
def test_profiles_without_allowlist_are_closed_by_default(tmp_path):
    (tmp_path / "p0").mkdir()
    (tmp_path / "p1").mkdir()
    (tmp_path / "p0" / ".env").write_text("TELEGRAM_ALLOWED_USERS=12345\n")
    (tmp_path / "p1" / ".env").write_text("# TELEGRAM_ALLOWED_USERS=\n")
    (tmp_path / "opts.json").write_text('{"env_vars":[],"profile_env_vars":[]}')
    script = f"""
        source {ADDON}/profile-init.sh
        OPTIONS_FILE={tmp_path}/opts.json; ENABLE_API=false; ACCESS_PASSWORD=""
        PROFILE_DIRS=(p0 p1); PROFILE_NAMES=(p0 p1); API_PORTS=(8642 8643)
        PROFILE_HOMES=({tmp_path}/p0 {tmp_path}/p1)
        for _ in 1 2; do apply_env_vars_for_profile 0; apply_env_vars_for_profile 1; done
    """
    result = _bash(script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "GATEWAY_ALLOWED_USERS" not in (tmp_path / "p0" / ".env").read_text()
    assert (tmp_path / "p1" / ".env").read_text().count("GATEWAY_ALLOWED_USERS=homeassistant") == 1


def test_rest_fallback_hands_over_to_mqtt_when_broker_appears(monkeypatch):
    monkeypatch.setattr(ha_sensor_reporter, "MQTT_RECHECK_SECONDS", 0)
    monkeypatch.setattr(ha_sensor_reporter, "POLL_SECONDS", 0)
    monkeypatch.setattr(ha_sensor_reporter, "post_ha_state", lambda *a, **k: True)
    monkeypatch.setattr(ha_sensor_reporter, "gateway_running", lambda home: True)
    broker = {"host": "core-mosquitto", "port": 1883}
    monkeypatch.setattr(ha_sensor_reporter, "mqtt_ready", lambda: broker)
    result = ha_sensor_reporter.run_rest_loop(
        "http://supervisor/core", "token", ["alice"], [8642], ["/x"], False, "1.0")
    assert result == broker
