import os
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

DB_PATH = Path(os.environ.get("MANTIS_DB_PATH", "/data/jobs.db"))


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                targets TEXT NOT NULL,
                snapshot_ids TEXT,
                egress_manifest TEXT,
                manifest_hash TEXT,
                approved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                progress_percent INTEGER DEFAULT 0,
                current_stage TEXT DEFAULT 'pending',
                findings TEXT DEFAULT '[]',
                summary TEXT DEFAULT '{}',
                usage TEXT DEFAULT '{"tokens_in": 0, "tokens_out": 0, "estimated_cost_eur": 0.0}',
                markdown_report TEXT,
                error_message TEXT
            )
        """)
        # Crash recovery: mark stale 'running' jobs from earlier process runs as 'interrupted'
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute("""
            UPDATE jobs 
            SET status = 'interrupted', error_message = 'Add-on restarted while job was in flight'
            WHERE status IN ('running', 'queued')
        """)
    return conn


def create_job(
    job_id: str,
    job_type: str,
    targets: List[Any],
    status: str = "queued",
    snapshot_ids: Optional[Dict[str, str]] = None,
    egress_manifest: Optional[Dict[str, Any]] = None,
    manifest_hash: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, job_type, status, targets, snapshot_ids,
                egress_manifest, manifest_hash, created_at, updated_at,
                progress_percent, current_stage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'created')
            """,
            (
                job_id,
                job_type,
                status,
                json.dumps(targets),
                json.dumps(snapshot_ids or {}),
                json.dumps(egress_manifest) if egress_manifest else None,
                manifest_hash,
                now,
                now,
            ),
        )
    return get_job(job_id, db_path=db_path)


def get_job(job_id: str, db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["targets"] = json.loads(d["targets"])
    d["snapshot_ids"] = json.loads(d["snapshot_ids"] or "{}")
    d["egress_manifest"] = json.loads(d["egress_manifest"] or "{}")
    d["findings"] = json.loads(d["findings"] or "[]")
    d["summary"] = json.loads(d["summary"] or "{}")
    d["usage"] = json.loads(d["usage"] or "{}")
    return d


def update_job_status(
    job_id: str,
    status: str,
    progress: Optional[int] = None,
    stage: Optional[str] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    summary: Optional[Dict[str, Any]] = None,
    usage: Optional[Dict[str, Any]] = None,
    markdown_report: Optional[str] = None,
    error_message: Optional[str] = None,
    approved: bool = False,
    db_path: Path = DB_PATH,
):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    with conn:
        fields = ["status = ?", "updated_at = ?"]
        params = [status, now]

        if approved:
            fields.append("approved_at = ?")
            params.append(now)
        if progress is not None:
            fields.append("progress_percent = ?")
            params.append(progress)
        if stage is not None:
            fields.append("current_stage = ?")
            params.append(stage)
        if findings is not None:
            fields.append("findings = ?")
            params.append(json.dumps(findings))
        if summary is not None:
            fields.append("summary = ?")
            params.append(json.dumps(summary))
        if usage is not None:
            fields.append("usage = ?")
            params.append(json.dumps(usage))
        if markdown_report is not None:
            fields.append("markdown_report = ?")
            params.append(markdown_report)
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)
        if status in ("completed", "failed", "cancelled", "interrupted"):
            fields.append("completed_at = ?")
            params.append(now)

        params.append(job_id)
        query = f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?"
        conn.execute(query, params)


def list_jobs(status: Optional[str] = None, limit: int = 20, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if status:
        cur.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cur.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    rows = cur.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["targets"] = json.loads(d["targets"])
        d["snapshot_ids"] = json.loads(d["snapshot_ids"] or "{}")
        d["summary"] = json.loads(d["summary"] or "{}")
        d["usage"] = json.loads(d["usage"] or "{}")
        results.append(d)
    return results
