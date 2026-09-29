#!/usr/bin/env python3
"""
Home Assistant KPI Sensor Reporter for Langfuse v4 Add-on.
Queries local ClickHouse periodically and publishes metrics to Home Assistant REST API.
"""

import os
import sys
import time
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ha-sensors] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ha-sensors")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_URL = "http://supervisor/core/api"
CLICKHOUSE_URL = "http://127.0.0.1:8123"
INTERVAL_SECONDS = 30


def query_clickhouse(sql: str) -> list[dict]:
    """Execute query against ClickHouse HTTP endpoint."""
    url = f"{CLICKHOUSE_URL}/?query={urllib.parse.quote(sql)}%20FORMAT%20JSON"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as exc:
        logger.debug("ClickHouse query error: %s", exc)
        return []


def post_ha_sensor(entity_id: str, state: str | int | float, attributes: dict) -> bool:
    """Publish sensor state and attributes to Home Assistant REST API."""
    if not SUPERVISOR_TOKEN:
        logger.debug("No SUPERVISOR_TOKEN available; skipping HA publish.")
        return False

    url = f"{HA_URL}/states/{entity_id}"
    payload = json.dumps({
        "state": state,
        "attributes": attributes
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except Exception as exc:
        logger.debug("Failed to post sensor %s: %s", entity_id, exc)
        return False


def collect_and_publish() -> None:
    # 1. Total tokens and cost today (UTC / local midnight)
    today_iso = datetime.now().strftime("%Y-%m-%d 00:00:00")
    
    sql_today = f"""
    SELECT
        count() AS total_generations,
        sum(ifNotFinite(usage_details['total'], 0)) AS total_tokens,
        sum(ifNotFinite(usage_details['input'], 0)) AS input_tokens,
        sum(ifNotFinite(usage_details['output'], 0)) AS output_tokens,
        sum(ifNotFinite(usage_details['reasoning'], 0)) AS reasoning_tokens,
        sum(ifNotFinite(total_cost, 0)) AS total_cost_usd,
        round(avg(if(end_time IS NOT NULL AND start_time IS NOT NULL, dateDiff('millisecond', start_time, end_time) / 1000.0, NULL)), 2) AS avg_latency_s
    FROM default.observations
    WHERE start_time >= '{today_iso}'
      AND type = 'GENERATION'
      AND is_deleted = 0
    """
    
    rows = query_clickhouse(sql_today)
    metrics = rows[0] if rows else {}

    total_tokens = int(metrics.get("total_tokens") or 0)
    input_tokens = int(metrics.get("input_tokens") or 0)
    output_tokens = int(metrics.get("output_tokens") or 0)
    reasoning_tokens = int(metrics.get("reasoning_tokens") or 0)
    total_cost_usd = round(float(metrics.get("total_cost_usd") or 0.0), 4)
    total_generations = int(metrics.get("total_generations") or 0)
    avg_latency = float(metrics.get("avg_latency_s") or 0.0) if metrics.get("avg_latency_s") is not None else 0.0

    # 2. Latest observation for model info
    sql_latest = """
    SELECT
        provided_model_name,
        ifNotFinite(usage_details['total'], 0) AS last_tokens,
        ifNotFinite(total_cost, 0) AS last_cost,
        start_time
    FROM default.observations
    WHERE type = 'GENERATION' AND is_deleted = 0
    ORDER BY start_time DESC
    LIMIT 1
    """
    latest_rows = query_clickhouse(sql_latest)
    latest = latest_rows[0] if latest_rows else {}
    last_model = latest.get("provided_model_name") or "none"
    last_tokens = int(latest.get("last_tokens") or 0)
    last_cost = round(float(latest.get("last_cost") or 0.0), 4)
    last_time = latest.get("start_time") or ""

    now_iso = datetime.now(timezone.utc).isoformat()

    # Sensor 1: Tokens Today
    post_ha_sensor(
        "sensor.langfuse_tokens_today",
        total_tokens,
        {
            "friendly_name": "Langfuse Tokens Today",
            "icon": "mdi:counter",
            "unit_of_measurement": "tokens",
            "state_class": "total",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "last_updated": now_iso
        }
    )

    # Sensor 2: Cost Today (USD)
    post_ha_sensor(
        "sensor.langfuse_cost_today",
        total_cost_usd,
        {
            "friendly_name": "Langfuse Cost Today",
            "icon": "mdi:currency-usd",
            "unit_of_measurement": "$",
            "state_class": "total",
            "last_updated": now_iso
        }
    )

    # Sensor 3: Generations / Prompts Today
    post_ha_sensor(
        "sensor.langfuse_generations_today",
        total_generations,
        {
            "friendly_name": "Langfuse LLM Calls Today",
            "icon": "mdi:brain",
            "unit_of_measurement": "calls",
            "state_class": "total",
            "last_updated": now_iso
        }
    )

    # Sensor 4: Average Latency
    post_ha_sensor(
        "sensor.langfuse_avg_latency",
        avg_latency,
        {
            "friendly_name": "Langfuse Average Latency",
            "icon": "mdi:timer-outline",
            "unit_of_measurement": "s",
            "state_class": "measurement",
            "last_updated": now_iso
        }
    )

    # Sensor 5: Last Model Used
    post_ha_sensor(
        "sensor.langfuse_last_model",
        last_model,
        {
            "friendly_name": "Langfuse Last Used Model",
            "icon": "mdi:robot",
            "last_tokens": last_tokens,
            "last_cost": last_cost,
            "last_timestamp": last_time,
            "last_updated": now_iso
        }
    )

    logger.debug(
        "Published KPIs: tokens=%d, cost=$%.4f, calls=%d, latency=%.2fs, model=%s",
        total_tokens, total_cost_usd, total_generations, avg_latency, last_model
    )


def main():
    logger.info("Starting Langfuse Home Assistant Sensor Reporter (interval: %ds)...", INTERVAL_SECONDS)
    
    # Wait for ClickHouse readiness
    for attempt in range(1, 30):
        try:
            with urllib.request.urlopen(f"{CLICKHOUSE_URL}/ping", timeout=2) as resp:
                if resp.status == 200:
                    logger.info("ClickHouse is ready.")
                    break
        except Exception:
            time.sleep(2)
    else:
        logger.warning("ClickHouse readiness probe timed out; proceeding anyway.")

    while True:
        try:
            collect_and_publish()
        except Exception as exc:
            logger.error("Error in reporter loop: %s", exc)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
