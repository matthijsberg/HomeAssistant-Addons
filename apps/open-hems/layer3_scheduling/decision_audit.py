"""
Layer 3: Decision Audit Trail & Telemetry Logger
===============================================
Manages structured decision logging across InfluxDB and persistent JSONL storage,
providing transparent observability into Open HEMS dispatch reasoning.
"""

from typing import List, Optional, Dict, Any
from pathlib import Path
from datetime import datetime
import json
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo
from models.decision_log import DecisionRecord

AMS_TZ = ZoneInfo("Europe/Amsterdam")
AUDIT_FILE = Path("/config/open_hems_decisions.jsonl")
MAX_AUDIT_RECORDS = 500
ENABLE_INFLUX_QUERY = True


class DecisionAuditLogger:
    @classmethod
    def log_decision(
        cls,
        domain: str,
        decision_type: str,
        chosen_mode: str,
        target_temp_c: Optional[float],
        inputs: Dict[str, Any],
        reason: str,
        explanation: str,
        savings_estimate_eur: float = 0.0,
        category: str = "DECISION",
        influx_cfg: Optional[Dict[str, Any]] = None,
        influx_pwd: str = ""
    ) -> DecisionRecord:
        """
        Records a structured decision to persistent JSONL and InfluxDB.
        """
        now_iso = datetime.now(AMS_TZ).isoformat()
        rec = DecisionRecord(
            timestamp_iso=now_iso,
            domain=domain,
            decision_type=decision_type,
            chosen_mode=chosen_mode,
            target_temp_c=target_temp_c,
            inputs=inputs,
            reason=reason,
            explanation=explanation,
            savings_estimate_eur=savings_estimate_eur,
            category=category
        )

        # 1. Append to local JSONL
        cls._append_jsonl(rec)

        # 2. Write to InfluxDB if configuration provided
        if influx_cfg:
            cls._write_to_influx(rec, influx_cfg, influx_pwd)

        return rec

    @classmethod
    def _append_jsonl(cls, rec: DecisionRecord):
        """Appends record to rotating JSONL file."""
        try:
            records = []
            if AUDIT_FILE.exists():
                with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except Exception:
                                pass
            records.append(rec.to_dict())
            if len(records) > MAX_AUDIT_RECORDS:
                records = records[-MAX_AUDIT_RECORDS:]

            with open(AUDIT_FILE, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
        except Exception as e:
            print(f"Warning writing decision audit JSONL: {e}")

    @classmethod
    def _write_to_influx(cls, rec: DecisionRecord, influx_cfg: Dict[str, Any], pwd: str):
        """Writes decision to InfluxDB hems_decisions measurement."""
        try:
            db_name = influx_cfg.get("database", "openhems")
            db_user = influx_cfg.get("username", "openhems")
            db_url = influx_cfg.get("url", "http://a0d7b954-influxdb:8086").rstrip("/")

            line = rec.to_influx_line("hems_decisions")
            write_url = f"{db_url}/write?" + urllib.parse.urlencode({"u": db_user, "p": pwd, "db": db_name})
            req = urllib.request.Request(write_url, data=line.encode("utf-8"), method="POST")
            with urllib.request.urlopen(req, timeout=3) as resp:
                pass
        except Exception as e:
            print(f"Warning writing decision to InfluxDB: {e}")

    @classmethod
    def get_recent_decisions(cls, limit: int = 50, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves recent decisions, combining persistent JSONL storage
        with live InfluxDB measurements (hems_decisions and hems_annotations).
        """
        records = []

        # 1. Read from local JSONL files
        for audit_path in [Path("/data/open_hems_decisions.jsonl"), AUDIT_FILE]:
            if audit_path.exists():
                try:
                    with open(audit_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    d = json.loads(line)
                                    if domain and d.get("domain") != domain:
                                        continue
                                    records.append(d)
                                except Exception:
                                    pass
                except Exception as e:
                    print(f"Warning reading {audit_path}: {e}")

        # 2. Query InfluxDB for hems_decisions & hems_annotations
        if ENABLE_INFLUX_QUERY:
            try:
                sec_file = Path("/config/open_hems_secrets.json")
                if not sec_file.exists():
                    sec_file = Path("/data/open_hems_secrets.json")

                sec = {}
                if sec_file.exists():
                    with open(sec_file, "r", encoding="utf-8") as f:
                        sec = json.load(f)

                pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")
                if pwd:
                    safe_limit = min(500, max(1, int(limit)))
                    q = f'SELECT * FROM "hems_decisions" ORDER BY time DESC LIMIT {safe_limit}; SELECT * FROM "hems_annotations" ORDER BY time DESC LIMIT {safe_limit};'
                    url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q)}"
                    with urllib.request.urlopen(url, timeout=3) as resp:
                        res = json.loads(resp.read().decode())

                    for stmt in res.get("results", []):
                        for s in stmt.get("series", []):
                            meas = s.get("name")
                            cols = s.get("columns", [])
                            for val in s.get("values", []):
                                d = dict(zip(cols, val))
                                if meas == "hems_decisions":
                                    rec_dom = d.get("domain", "general")
                                    if domain and rec_dom != domain:
                                        continue
                                    records.append({
                                        "timestamp_iso": d.get("time"),
                                        "category": d.get("category", "DECISION"),
                                        "domain": rec_dom,
                                        "decision_type": d.get("decision_type", "dispatch"),
                                        "chosen_mode": d.get("chosen_mode", "normal"),
                                        "target_temp_c": d.get("target_temp_c"),
                                        "inputs": {
                                            "tank_temp_c": d.get("tank_temp_c"),
                                            "target_temp_c": d.get("target_temp_c"),
                                            "solar_surplus_kw": d.get("solar_surplus_kw"),
                                            "financial_impact_eur": d.get("financial_impact_eur")
                                        },
                                        "reason": d.get("reason", "HEMS dispatch besluit"),
                                        "explanation": d.get("explanation", d.get("reason", "")),
                                        "savings_estimate_eur": d.get("financial_impact_eur", 0.0)
                                    })
                                elif meas == "hems_annotations":
                                    evt = (d.get("event_type") or "").lower()
                                    tit = (d.get("title") or "").lower()
                                    sev = (d.get("severity") or "").lower()

                                    if "error" in evt or "fout" in tit or "mislukt" in tit or "unconfirmed" in evt or "niet bevestigd" in tit or sev == "error":
                                        category = "ERROR"
                                        rec_dom = "hardware" if "schakel" in tit or "relais" in tit or "ha" in tit else ("grid_tariff" if "tarief" in tit or "energyzero" in tit else "system")
                                    elif "warning" in evt or "waarschuwing" in tit or sev == "warning":
                                        category = "WARNING"
                                        rec_dom = "weather" if "meteo" in tit else ("solar" if "forecast" in tit else "system")
                                    elif "hardware" in evt or "relais" in tit or "actuatie" in evt:
                                        rec_dom = "hardware"
                                        category = "ACTION"
                                    elif "peak" in evt or "spits" in tit:
                                        rec_dom = "grid_tariff"
                                        category = "DECISION"
                                    elif "space_heating" in evt or "cv" in tit or "ruimteverwarming" in tit:
                                        rec_dom = "space_heating"
                                        category = "DECISION"
                                    elif "dhw" in evt or "dhw" in tit or "boiler" in tit or "zonnebuffer" in tit or "nacht" in tit:
                                        rec_dom = "dhw"
                                        category = "ACTION" if any(w in tit for w in ["bereikt", "vrijgegeven", "relais"]) else "DECISION"
                                    else:
                                        rec_dom = "general"
                                        category = "DECISION"

                                    if domain and rec_dom != domain:
                                        continue
                                    records.append({
                                        "timestamp_iso": d.get("time"),
                                        "category": category,
                                        "domain": rec_dom,
                                        "decision_type": d.get("event_type", "annotation"),
                                        "chosen_mode": d.get("state_code", "normal"),
                                        "target_temp_c": d.get("target_temp_c"),
                                        "inputs": {
                                            "power_kw": d.get("power_kw"),
                                            "target_temp_c": d.get("target_temp_c")
                                        },
                                        "reason": d.get("title", "HEMS Statuswijziging"),
                                        "explanation": d.get("description", ""),
                                        "savings_estimate_eur": d.get("savings_eur", 0.0)
                                    })
            except Exception as e:
                print(f"Warning querying InfluxDB decisions: {e}")

        # Deduplicate & normalize timestamps to local epoch
        parsed = []
        for r in records:
            ts_str = r.get("timestamp_iso") or ""
            clean = ts_str.replace("Z", "+00:00")
            if "." in clean:
                parts = clean.split(".")
                sub = parts[1]
                tz_part = ""
                if "+" in sub:
                    sub, tz_part = sub.split("+")
                    tz_part = "+" + tz_part
                elif "-" in sub:
                    sub, tz_part = sub.split("-")
                    tz_part = "-" + tz_part
                clean = f"{parts[0]}.{sub[:6]}{tz_part}"
            try:
                dt = datetime.fromisoformat(clean)
                epoch = int(dt.timestamp())
                iso_ams = dt.astimezone(AMS_TZ).isoformat()
            except Exception:
                epoch = 0
                iso_ams = ts_str

            parsed.append({
                **r,
                "_epoch": epoch,
                "timestamp_iso": iso_ams
            })

        # Sort chronologically ascending to track state transitions
        parsed.sort(key=lambda x: x["_epoch"])

        deduped_chronological = []
        last_action_state = None
        last_decision_title = None

        for r in parsed:
            cat = r.get("category", "DECISION")
            title = r.get("reason", "")
            mode = r.get("chosen_mode", "")
            domain_val = r.get("domain", "")

            # If an identical event occurred within 60s, keep ACTION over DECISION
            is_dup = False
            for prev in deduped_chronological:
                if prev.get("reason") == title and abs(prev.get("_epoch", 0) - r["_epoch"]) < 60:
                    if r.get("category") == "ACTION":
                        prev["category"] = "ACTION"
                    is_dup = True
                    break
            if is_dup:
                continue

            if cat == "ACTION":
                # Only log genuine hardware state transitions
                if last_action_state == (domain_val, mode):
                    continue
                last_action_state = (domain_val, mode)
            else:
                # Decisions: if identical decision was logged within 4 hours, skip duplicate
                if last_decision_title and last_decision_title[0] == title and (r["_epoch"] - last_decision_title[1]) < 14400:
                    continue
                last_decision_title = (title, r["_epoch"])

            deduped_chronological.append(r)

        # Reverse to newest first and remove temporary _epoch
        final_list = list(reversed(deduped_chronological))
        for r in final_list:
            r.pop("_epoch", None)

        return final_list[:limit]
