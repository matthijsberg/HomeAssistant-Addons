import json
import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, Tuple


class BudgetExceededError(Exception):
    pass


def check_and_update_budget(
    db_path: Path,
    job_id: str,
    tokens_in: int,
    tokens_out: int,
    cost_eur: float,
    budget_limits: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Checks per-job and per-day token and cost caps.
    Raises BudgetExceededError if limit is breached.
    """
    max_tokens_job = budget_limits.get("max_tokens_per_job", 2_000_000)
    max_cost_job = budget_limits.get("max_cost_eur_per_job", 5.0)
    max_cost_day = budget_limits.get("max_cost_eur_per_day", 15.0)

    total_job_tokens = tokens_in + tokens_out
    if total_job_tokens > max_tokens_job:
        raise BudgetExceededError(
            f"Job {job_id} exceeded token limit: {total_job_tokens} > {max_tokens_job}"
        )
    if cost_eur > max_cost_job:
        raise BudgetExceededError(
            f"Job {job_id} exceeded cost limit: €{cost_eur:.2f} > €{max_cost_job:.2f}"
        )

    # Check daily cumulative cost across all jobs
    today_prefix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    cur = conn.cursor()
    cur.execute(
        "SELECT usage FROM jobs WHERE created_at LIKE ?",
        (f"{today_prefix}%",),
    )
    rows = cur.fetchall()
    daily_cost = cost_eur
    for (usage_raw,) in rows:
        if usage_raw:
            try:
                u = json.loads(usage_raw)
                daily_cost += u.get("estimated_cost_eur", 0.0)
            except Exception:
                pass

    if daily_cost > max_cost_day:
        raise BudgetExceededError(
            f"Daily cost limit breached: €{daily_cost:.2f} > €{max_cost_day:.2f}"
        )

    return True, "Budget OK"
