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
            savings_estimate_eur=savings_estimate_eur
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
        """Retrieves recent decisions from local JSONL storage."""
        if not AUDIT_FILE.exists():
            return []
        try:
            records = []
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
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
            return list(reversed(records))[:limit]
        except Exception as e:
            print(f"Warning reading decision audit JSONL: {e}")
            return []
