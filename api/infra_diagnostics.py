"""
Open HEMS: Infrastructure Diagnostics & Telemetry Annotations
============================================================
Handles connection verification for InfluxDB and MQTT, plus operational annotations.
"""

import time
import json
import base64
import socket
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any

from api.secrets_store import CONFIG_FILE, load_json, load_secrets
from layer3_scheduling.decision_audit import DecisionAuditLogger


def write_hems_annotation(
    event_type: str,
    title: str,
    description: str,
    state_code: str,
    power_kw: float = 0.0,
    target_temp_c: float = 0.0,
    savings_eur: float = 0.0,
    severity: str = "info"
) -> None:
    """Writes a native semantic event annotation to openhems InfluxDB for Grafana dashboards."""
    try:
        cfg = load_json(CONFIG_FILE)
        sec = load_secrets()
        active_conn = cfg.get("influxdb_connections", [{}])[0]
        db_name = active_conn.get("database", "openhems")
        db_user = active_conn.get("username", "openhems")
        db_url = active_conn.get("url", "http://a0d7b954-influxdb:8086")
        db_pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

        now_ns = int(time.time() * 1e9)
        safe_title = title.replace('"', '\\"').replace('\n', ' ')
        safe_desc = description.replace('"', '\\"').replace('\n', ' ')

        line = (
            f'hems_annotations,event_type={event_type},severity={severity},state_code={state_code} '
            f'title="{safe_title}",description="{safe_desc}",power_kw={power_kw:.2f},'
            f'target_temp_c={target_temp_c:.1f},savings_eur={savings_eur:.2f} {now_ns}'
        )

        write_url = f"{db_url}/write?" + urllib.parse.urlencode({"u": db_user, "p": db_pwd, "db": db_name})
        req = urllib.request.Request(write_url, data=line.encode("utf-8"), method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception as e:
        print(f"Warning writing Grafana annotation: {e}")


def log_technical_error(
    domain: str,
    event_type: str,
    reason: str,
    explanation: str,
    inputs: Dict[str, Any],
    category: str = "ERROR"
) -> None:
    """Logs technical errors, failed actuations, and API issues to InfluxDB and the Audit Logger."""
    try:
        sev = "error" if category == "ERROR" else "warning"
        write_hems_annotation(
            event_type=event_type,
            title=reason,
            description=explanation,
            state_code=sev,
            power_kw=0.0,
            target_temp_c=0.0,
            savings_eur=0.0,
            severity=sev
        )
        DecisionAuditLogger.log_decision(
            domain=domain,
            decision_type=event_type,
            chosen_mode=sev,
            target_temp_c=None,
            inputs=inputs,
            reason=reason,
            explanation=explanation,
            savings_estimate_eur=0.0,
            category=category
        )
    except Exception as e_log:
        print(f"Warning logging technical error: {e_log}")


def test_influxdb_connection(url: str, database: str, username: str = "", password: str = "", retention: str = "autogen") -> Dict[str, Any]:
    """Tests connection, authentication, and database availability against InfluxDB."""
    t0 = time.time()
    try:
        clean_url = url.rstrip("/")
        # 1. Ping
        ping_req = urllib.request.Request(f"{clean_url}/ping")
        with urllib.request.urlopen(ping_req, timeout=3) as r:
            if r.status not in (200, 204):
                return {"status": "error", "message": f"Ping mislukt met HTTP code {r.status}"}

        # 2. Query Databases
        q_url = f"{clean_url}/query?" + urllib.parse.urlencode({"q": "SHOW DATABASES"})
        req_q = urllib.request.Request(q_url)
        auth = base64.b64encode(f"{username}:{password}".encode()).decode() if (username and password) else None
        if auth:
            req_q.add_header("Authorization", f"Basic {auth}")

        with urllib.request.urlopen(req_q, timeout=4) as r:
            res = json.loads(r.read().decode("utf-8"))
            dbs = [v[0] for v in res.get("results", [{}])[0].get("series", [{}])[0].get("values", [])]

        # 3. Check specific database series count
        series_count = 0
        target_db = database or "hermes"
        if target_db in dbs:
            q_meas = f"{clean_url}/query?" + urllib.parse.urlencode({"q": f"SHOW MEASUREMENTS ON {target_db}"})
            req_m = urllib.request.Request(q_meas)
            if auth:
                req_m.add_header("Authorization", f"Basic {auth}")
            with urllib.request.urlopen(req_m, timeout=4) as r:
                res_m = json.loads(r.read().decode("utf-8"))
                series_vals = res_m.get("results", [{}])[0].get("series", [{}])[0].get("values", [])
                series_count = len(series_vals)

        latency = round((time.time() - t0) * 1000, 1)
        return {
            "status": "success",
            "message": f"Verbinding geslaagd! Database '{target_db}' bereikbaar ({series_count} meetreeksen).",
            "databases": dbs,
            "series_count": series_count,
            "latency_ms": latency
        }
    except urllib.error.HTTPError as e:
        latency = round((time.time() - t0) * 1000, 1)
        if e.code == 401:
            return {"status": "error", "message": "HTTP 401: Niet geautoriseerd. Controleer gebruikersnaam en wachtwoord.", "latency_ms": latency}
        if e.code == 403:
            return {"status": "error", "message": "HTTP 403: Toegang geweigerd tot deze database voor deze gebruiker.", "latency_ms": latency}
        return {"status": "error", "message": f"HTTP {e.code}: {e.reason}", "latency_ms": latency}
    except Exception as e:
        latency = round((time.time() - t0) * 1000, 1)
        return {"status": "error", "message": f"Fout bij verbinden: {str(e)}", "latency_ms": latency}


def test_mqtt_connection(host: str, port: int, username: str = "", password: str = "", client_id: str = "") -> Dict[str, Any]:
    """Tests TCP connectivity and performs an MQTT 3.1.1 CONNECT handshake."""
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    try:
        s.connect((host, int(port)))
        cid = (client_id or "open-hems-test").encode("utf-8")
        clean_session = 0x02
        flags = clean_session
        if username:
            flags |= 0x80
        if password:
            flags |= 0x40

        payload = bytes([0, len(cid)]) + cid
        if username:
            ub = username.encode("utf-8")
            payload += bytes([0, len(ub)]) + ub
        if password:
            pb = password.encode("utf-8")
            payload += bytes([0, len(pb)]) + pb

        var_header = b"\x00\x04MQTT\x04" + bytes([flags, 0, 60])
        packet = bytes([0x10, len(var_header) + len(payload)]) + var_header + payload
        s.send(packet)
        resp = s.recv(10)
        s.close()
        latency = round((time.time() - t0) * 1000, 1)

        if len(resp) >= 4 and resp[0] == 0x20:
            rc = resp[3]
            rc_map = {
                0: ("success", f"Verbinding geslaagd met broker ({host}:{port})! (RC 0: OK)"),
                1: ("error", "Protocolversie niet geaccepteerd door broker (RC 1)"),
                2: ("error", "Client ID geweigerd door broker (RC 2)"),
                4: ("error", "Gebruikersnaam of wachtwoord onjuist (RC 4)"),
                5: ("error", "Niet geautoriseerd: broker vereist geldige login (RC 5)")
            }
            st, msg = rc_map.get(rc, ("error", f"Broker return code: {rc}"))
            return {"status": st, "message": msg, "latency_ms": latency}
        return {"status": "error", "message": "Geen geldig MQTT CONNACK pakket ontvangen.", "latency_ms": latency}
    except socket.timeout:
        return {"status": "error", "message": f"Timeout bij verbinden met {host}:{port}.", "latency_ms": round((time.time() - t0) * 1000, 1)}
    except ConnectionRefusedError:
        return {"status": "error", "message": f"Verbinding geweigerd op {host}:{port}. Is de broker actief?", "latency_ms": round((time.time() - t0) * 1000, 1)}
    except Exception as e:
        return {"status": "error", "message": f"Fout: {str(e)}", "latency_ms": round((time.time() - t0) * 1000, 1)}
